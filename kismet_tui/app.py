"""Kismet TUI -- a field-usable terminal front end for Kismet.

Design choices, on purpose:
* One screen, no mouse required. Everything a wardrive needs to *notice*
  something is visible at a glance: counts, strongest/newest devices,
  GPS state, channel crowding, who's probing for what.
* Inferred devices are a first-class concept, not noise to silently
  drop: they're hidden by default, counted separately, and one key
  (``i``) brings them back. Signal 0 + 0 packets + 0,0 is *never*
  presented as a located, heard device.
* Fails soft. Kismet down, an endpoint 404ing on an older build, a GPS
  with no fix -- each dims its own pane and says so, nothing crashes.
"""
from __future__ import annotations

import time
from collections import Counter

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import (DataTable, Footer, Input, Static, TabbedContent,
                             TabPane)

from .models import Device, sparkline

SORTS = ["signal", "packets", "last_seen", "name"]
PHYS = ["ALL", "IEEE802.11", "Bluetooth"]


def fmt_age(ts: float) -> str:
    if not ts:
        return "—"
    s = int(time.time() - ts)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def sig_style(dbm: int) -> str:
    if dbm == 0:
        return "dim"
    if dbm >= -55:
        return "bold green"
    if dbm >= -70:
        return "yellow"
    return "red"


class KismetTUI(App):
    TITLE = "Kismet TUI"
    SUB_TITLE = "wardrive console"
    CSS = """
    Screen { background: #0a0e0a; }
    #topbar { height: 1; background: #142014; color: #b7f0b7; padding: 0 1; }
    #stats  { height: 1; background: #0f150f; color: #9db89d; padding: 0 1; }
    #toolbar { height: 3; padding: 0 1; }
    #search { width: 42; }
    #filterlabel { padding: 1 1; color: #7fa37f; width: auto; }
    #main { height: 1fr; }
    #devices { width: 1fr; }
    #detail { width: 38; border-left: solid #2a3d2a; padding: 0 1; }
    #bottom { height: 11; border-top: solid #2a3d2a; }
    DataTable { background: #0a0e0a; }
    TabbedContent { height: 100%; }
    .pane { padding: 0 1; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("p", "toggle_pause", "Pause"),
        Binding("r", "refresh_now", "Refresh"),
        Binding("/", "focus_search", "Search"),
        Binding("escape", "clear_search", "Clear"),
        Binding("f", "cycle_phy", "PHY filter"),
        Binding("n", "toggle_new", "New only"),
        Binding("i", "toggle_inferred", "Inferred"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("e", "export_csv", "Export CSV"),
    ]

    def __init__(self, source, refresh: float = 2.0, source_label: str = ""):
        super().__init__()
        self.source = source
        self.refresh_s = refresh
        self.source_label = source_label
        self.devices: list[Device] = []
        self.alerts = []
        self.gps: dict = {}
        self.status: dict = {}
        self.connected = False
        self.error = ""
        self.paused = False
        self.sort_by = "signal"
        self.phy_filter = "ALL"
        self.new_only = False
        self.show_inferred = False
        self.query = ""
        self._by_row: list[Device] = []
        self._timer = None

    # -- layout ----------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("starting…", id="topbar")
        yield Static("", id="stats")
        with Horizontal(id="toolbar"):
            yield Input(placeholder="filter: ssid / mac / manuf / probe…  ( / )",
                        id="search")
            yield Static("", id="filterlabel")
        with Horizontal(id="main"):
            yield DataTable(id="devices", cursor_type="row", zebra_stripes=True)
            yield Static("Select a device", id="detail")
        with Vertical(id="bottom"):
            with TabbedContent():
                with TabPane("Channels", id="tab-channels"):
                    yield Static("", id="channels", classes="pane")
                with TabPane("Probes", id="tab-probes"):
                    yield DataTable(id="probes", cursor_type="row")
                with TabPane("Alerts", id="tab-alerts"):
                    yield DataTable(id="alerts", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#devices", DataTable)
        # Explicit widths: without them DataTable sizes to content and the
        # right-hand columns (Pkts / Seen / NEW) get pushed off-screen
        # behind the detail pane at real terminal widths.
        # Budget: content widths + ~2 cells padding per column must fit
        # terminal minus the 38-wide detail pane (~97 cells at 140 cols).
        for label, width in [("Sig", 4), ("", 6), ("MAC", 17),
                             ("Name / SSID", 19), ("Type", 6), ("Crypt", 10),
                             ("Ch", 3), ("Pkts", 5), ("Seen", 5), ("", 3)]:
            table.add_column(label, width=width)
        probes = self.query_one("#probes", DataTable)
        probes.add_columns("Probed SSID", "Clients probing", "Strongest")
        alerts = self.query_one("#alerts", DataTable)
        alerts.add_columns("Time", "Alert", "Detail")
        # The device table owns the keyboard by default. If the search
        # Input grabbed focus, every hotkey (i, n, s, f...) would be
        # *typed into the filter* instead of acting -- caught in testing.
        table.focus()
        self._timer = self.set_interval(self.refresh_s, self._poll)
        self._poll()

    # -- data ------------------------------------------------------------
    def _poll(self) -> None:
        if self.paused:
            return
        self.run_worker(self._fetch, thread=True, exclusive=True,
                        group="poll")

    def _fetch(self) -> None:
        try:
            data = self.source.poll()
        except Exception as exc:  # source blew up -- surface, keep old data
            self.call_from_thread(self._apply_error, str(exc))
            return
        self.call_from_thread(self._apply, data)

    def _apply_error(self, msg: str) -> None:
        self.connected, self.error = False, msg
        self._render_chrome()

    def _apply(self, data: dict) -> None:
        self.devices = data.get("devices") or []
        self.alerts = data.get("alerts") or []
        self.gps = data.get("gps") or {}
        self.status = data.get("status") or {}
        self.connected = bool(data.get("connected"))
        self.error = data.get("error", "")
        self._render_all()

    # -- filtering / sorting ---------------------------------------------
    def visible(self) -> list[Device]:
        out = []
        q = self.query.lower()
        for d in self.devices:
            if not self.show_inferred and d.inferred:
                continue
            if self.new_only and not d.is_new:
                continue
            if self.phy_filter != "ALL" and d.phy != self.phy_filter:
                continue
            if q:
                hay = " ".join([d.mac, d.name, d.manuf, d.type,
                                *d.advertised, *d.probed]).lower()
                if q not in hay:
                    continue
            out.append(d)
        key = {"signal": lambda d: d.signal, "packets": lambda d: d.packets,
               "last_seen": lambda d: d.last_seen,
               "name": lambda d: d.display_name.lower()}[self.sort_by]
        return sorted(out, key=key, reverse=self.sort_by != "name")

    # -- rendering ---------------------------------------------------------
    def _render_chrome(self) -> None:
        gps = self.gps
        if gps.get("lat"):
            gps_txt = (f"GPS {gps['lat']:.5f}, {gps['lon']:.5f} "
                       f"{gps.get('fix') or ''}")
        else:
            gps_txt = "GPS no fix"
        state = "● LIVE" if self.connected else "● DOWN"
        if self.paused:
            state = "❚❚ PAUSED"
        self.query_one("#topbar", Static).update(
            f"{state}  {self.source_label or self.status.get('server', '')}"
            f"   {gps_txt}   pps {self.status.get('pps', '—')}"
            + (f"   ⚠ {self.error}" if self.error else ""))
        heard = [d for d in self.devices if not d.inferred]
        located = [d for d in heard if d.located]
        c = Counter(d.type or "?" for d in heard)
        self.query_one("#stats", Static).update(
            f"devices {len(heard)}  (+{len(self.devices) - len(heard)} inferred)   "
            f"AP {c.get('AP', 0)}  clients {c.get('Client', 0)}  "
            f"BT {sum(v for k, v in c.items() if 'BT' in k)}   "
            f"new<5m {sum(1 for d in heard if d.is_new)}   "
            f"located {len(located)}/{len(heard) or 1} "
            f"({(100 * len(located) // len(heard)) if heard else 0}%)   "
            f"alerts {len(self.alerts)}")
        self.query_one("#filterlabel", Static).update(
            f"sort:{self.sort_by}  phy:{self.phy_filter}"
            f"{'  NEW-ONLY' if self.new_only else ''}"
            f"{'  +inferred' if self.show_inferred else ''}")

    def _render_all(self) -> None:
        self._render_chrome()
        table = self.query_one("#devices", DataTable)
        cursor_mac = None
        if table.cursor_row is not None and 0 <= table.cursor_row < len(self._by_row):
            cursor_mac = self._by_row[table.cursor_row].mac
        rows = self.visible()
        table.clear()
        self._by_row = rows
        for d in rows:
            table.add_row(
                Text(str(d.signal) if d.signal else "—", style=sig_style(d.signal)),
                Text(d.signal_bar(6), style=sig_style(d.signal)),
                d.mac, d.display_name[:19], d.type, (d.crypt or "—")[:10],
                d.channel or "—", str(d.packets), fmt_age(d.last_seen),
                Text("NEW", style="bold black on yellow") if d.is_new else "",
                key=d.mac or d.key)
        if cursor_mac:
            for i, d in enumerate(rows):
                if d.mac == cursor_mac:
                    table.move_cursor(row=i)
                    break
        self._render_detail()
        self._render_channels()
        self._render_probes()
        self._render_alerts()

    def _selected(self) -> Device | None:
        table = self.query_one("#devices", DataTable)
        if table.cursor_row is not None and 0 <= table.cursor_row < len(self._by_row):
            return self._by_row[table.cursor_row]
        return self._by_row[0] if self._by_row else None

    def _render_detail(self) -> None:
        d = self._selected()
        pane = self.query_one("#detail", Static)
        if not d:
            pane.update("No devices match the current filter.")
            return
        lines = [
            f"[b]{d.display_name}[/b]",
            f"{d.mac}   {d.manuf or ''}",
            f"type {d.type or '—'}   phy {d.phy or '—'}",
            f"crypt {d.crypt or '—'}   ch {d.channel or '—'}"
            + (f"   {d.freq_mhz:.0f} MHz" if d.freq else ""),
            f"signal {d.signal or '—'} dBm  {sparkline(d.signal_history)}",
            f"packets {d.packets}   datasources {d.datasources}",
            f"first {fmt_age(d.first_seen)} ago   last {fmt_age(d.last_seen)} ago",
            ("location " + (f"{d.lat:.5f}, {d.lon:.5f}" if d.located
                            else "none (never located)")),
        ]
        if d.inferred:
            lines.append("[yellow]INFERRED -- never heard transmitting[/yellow]")
        if d.advertised:
            lines += ["", "[b]Advertised SSIDs[/b]"] + [f"  {s}" for s in d.advertised]
        if d.probed:
            lines += ["", "[b]Probing for[/b]"] + [f"  {s}" for s in d.probed[:12]]
        if d.clients:
            lines += ["", f"[b]Associated clients ({len(d.clients)})[/b]"] + \
                     [f"  {c}" for c in d.clients[:10]]
        pane.update("\n".join(lines))

    def _render_channels(self) -> None:
        chans: dict[str, list[Device]] = {}
        for d in self.devices:
            if d.channel and not d.inferred:
                chans.setdefault(str(d.channel), []).append(d)
        if not chans:
            self.query_one("#channels", Static).update("No channel data yet.")
            return
        lines = []
        for ch in sorted(chans, key=lambda c: (0, int(c)) if c.isdigit()
                         else (1, c)):
            devs = chans[ch]
            n = len(devs)
            bar = "█" * min(40, n * 2)
            best = max(x.signal for x in devs)
            lines.append(f"ch {ch:>4}  {n:>3} dev  best {best:>4} dBm  "
                         f"[green]{bar}[/green]")
        self.query_one("#channels", Static).update("\n".join(lines))

    def _render_probes(self) -> None:
        counts: Counter = Counter()
        best: dict[str, int] = {}
        for d in self.devices:
            for ssid in d.probed:
                counts[ssid] += 1
                best[ssid] = max(best.get(ssid, -99), d.signal)
        table = self.query_one("#probes", DataTable)
        table.clear()
        for ssid, n in counts.most_common(40):
            table.add_row(ssid, str(n), f"{best[ssid]} dBm" if best[ssid] else "—")

    def _render_alerts(self) -> None:
        table = self.query_one("#alerts", DataTable)
        table.clear()
        for a in self.alerts[:60]:
            table.add_row(a.when, a.header, a.message[:70])

    # -- events / actions --------------------------------------------------
    def on_data_table_row_highlighted(self, _event) -> None:
        self._render_detail()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query = event.value.strip()
        if self.devices:
            self._render_all()

    def on_input_submitted(self, _event) -> None:
        self.query_one("#devices", DataTable).focus()

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_clear_search(self) -> None:
        self.query_one("#search", Input).value = ""
        self.query = ""
        self.query_one("#devices", DataTable).focus()
        if self.devices:
            self._render_all()

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        self._render_chrome()

    def action_refresh_now(self) -> None:
        self.paused = False
        self._poll()

    def action_cycle_phy(self) -> None:
        i = PHYS.index(self.phy_filter)
        self.phy_filter = PHYS[(i + 1) % len(PHYS)]
        if self.devices:
            self._render_all()

    def action_toggle_new(self) -> None:
        self.new_only = not self.new_only
        if self.devices:
            self._render_all()

    def action_toggle_inferred(self) -> None:
        self.show_inferred = not self.show_inferred
        if self.devices:
            self._render_all()

    def action_cycle_sort(self) -> None:
        i = SORTS.index(self.sort_by)
        self.sort_by = SORTS[(i + 1) % len(SORTS)]
        if self.devices:
            self._render_all()

    def action_export_csv(self) -> None:
        import csv
        import io
        import os
        path = os.path.expanduser(f"~/kismet-tui-export-{int(time.time())}.csv")
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["mac", "name", "type", "phy", "crypt", "channel",
                        "signal_dbm", "packets", "first_seen", "last_seen",
                        "lat", "lon", "advertised", "probed", "inferred"])
            for d in self.visible():
                w.writerow([d.mac, d.display_name, d.type, d.phy, d.crypt,
                            d.channel, d.signal, d.packets, d.first_seen,
                            d.last_seen, d.lat, d.lon,
                            "|".join(d.advertised), "|".join(d.probed),
                            int(d.inferred)])
        self.notify(f"Exported {len(self.visible())} devices to {path}",
                    title="CSV export")
