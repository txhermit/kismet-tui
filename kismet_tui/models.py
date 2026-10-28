"""Pure-python normalisation of Kismet data.

No Textual / network dependencies here: everything in this module can be
unit-tested on a machine that has never seen Kismet.  It knows how to turn

* a raw Kismet REST device record (nested ``kismet.*`` / ``dot11.*`` keys)
* a row from a ``.kismet`` SQLite log (the ``device`` column is JSON)

into one flat :class:`Device` the TUI can render without caring where it
came from.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterable


def _get(d: Any, *keys: str, default: Any = None) -> Any:
    """Nested dict get that tolerates Kismet's dotted keys.

    Kismet uses *flat* keys that contain dots (``"kismet.device.base.macaddr"``)
    at the top level and genuinely nested dicts one level down
    (``"kismet.common.signal"`` -> ``{"kismet.common.signal.last_signal": -42}``).
    Try both shapes.
    """
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        if k in cur:
            cur = cur[k]
        else:
            return default
    return cur


def _sig(device: dict) -> int:
    sig = _get(device, "kismet.common.signal")
    if isinstance(sig, dict):
        for key in ("kismet.common.signal.last_signal",
                    "kismet.common.signal.peak_signal"):
            v = sig.get(key)
            if isinstance(v, (int, float)) and v != 0:
                return int(v)
    v = device.get("kismet.common.signal.last_signal")
    if isinstance(v, (int, float)):
        return int(v)
    return 0


def _ssids(device: dict) -> tuple[list[str], list[str]]:
    """Return (advertised SSIDs, probed SSIDs) for a dot11 device."""
    advertised: list[str] = []
    probed: list[str] = []
    dot11 = device.get("dot11.device") or {}
    if not isinstance(dot11, dict):
        return advertised, probed

    def _collect(container: Any, out: list[str]) -> None:
        if isinstance(container, dict):
            # map of bssid/mac -> record, or ssid -> record
            for rec in container.values():
                if isinstance(rec, dict):
                    ssid = (rec.get("dot11.advertisedssid.ssid")
                            or rec.get("dot11.probedssid.ssid") or "")
                    if ssid and ssid not in out:
                        out.append(str(ssid))
                elif isinstance(rec, str) and rec and rec not in out:
                    out.append(rec)
        elif isinstance(container, list):
            for rec in container:
                _collect(rec, out)

    _collect(dot11.get("dot11.device.advertised_ssid_map"), advertised)
    _collect(dot11.get("dot11.device.probed_ssid_map"), probed)
    # Some firmware/exports flatten these:
    name = device.get("dot11.device.last_beaconed_ssid") or device.get(
        "kismet.device.base.name")
    if not advertised and name and device.get(
            "kismet.device.base.basic_type") in ("AP", "Wi-Fi AP"):
        advertised.append(str(name))
    return advertised, probed


def _clients(device: dict) -> list[str]:
    dot11 = device.get("dot11.device") or {}
    if not isinstance(dot11, dict):
        return []
    cmap = dot11.get("dot11.device.associated_client_map") or {}
    if isinstance(cmap, dict):
        return sorted(str(k) for k in cmap.keys())
    if isinstance(cmap, list):
        return sorted(str(x) for x in cmap)
    return []


@dataclass
class Device:
    key: str = ""
    mac: str = ""
    name: str = ""
    phy: str = ""
    type: str = ""               # AP / Client / Bridged / BTLE / ...
    crypt: str = ""
    channel: str = ""
    freq: int = 0                # kHz, as Kismet reports it
    signal: int = 0              # dBm, 0 == never heard (inferred)
    packets: int = 0
    datasources: int = 0
    manuf: str = ""
    first_seen: float = 0.0
    last_seen: float = 0.0
    lat: float = 0.0
    lon: float = 0.0
    advertised: list[str] = field(default_factory=list)
    probed: list[str] = field(default_factory=list)
    clients: list[str] = field(default_factory=list)
    signal_history: list[int] = field(default_factory=list)

    # -- derived ---------------------------------------------------------
    @property
    def inferred(self) -> bool:
        """Never actually heard transmitting.

        John's hard-won lesson from the 2026-10-22 run: ~half the Wi-Fi
        rows in a .kismet DB sit at 0,0 forever -- inferred/bridged/VRRP
        entries with signal 0.  They are not bad data, but they must
        never be counted as 'located' or 'heard'.
        """
        return self.signal == 0 and self.packets == 0

    @property
    def located(self) -> bool:
        return not (self.lat == 0.0 and self.lon == 0.0)

    @property
    def age_s(self) -> float:
        return max(0.0, time.time() - self.last_seen) if self.last_seen else 0.0

    @property
    def is_new(self) -> bool:
        return bool(self.first_seen) and (time.time() - self.first_seen) < 300

    @property
    def display_name(self) -> str:
        if self.advertised:
            return self.advertised[0]
        if self.name and self.name != self.mac:
            return self.name
        if self.probed:
            return f"probing: {self.probed[0]}"
        return self.manuf or "—"

    @property
    def freq_mhz(self) -> float:
        return self.freq / 1000.0 if self.freq else 0.0

    def signal_bar(self, width: int = 8) -> str:
        if self.signal == 0:
            return "·" * width
        # -30 (excellent) .. -90 (floor)
        frac = max(0.0, min(1.0, (self.signal + 90) / 60.0))
        filled = round(frac * width)
        return "█" * filled + "░" * (width - filled)


def parse_device(raw: dict) -> Device | None:
    if not isinstance(raw, dict):
        return None
    base = "kismet.device.base."
    loc = _get(raw, "kismet.common.location")
    lat = lon = 0.0
    if isinstance(loc, dict):
        avg = loc.get("kismet.common.location.avg_lat_lon") or loc
        if isinstance(avg, dict):
            lat = float(avg.get("kismet.location.lat") or 0.0)
            lon = float(avg.get("kismet.location.lon") or 0.0)
    advertised, probed = _ssids(raw)
    dev = Device(
        key=str(raw.get(base + "key") or ""),
        mac=str(raw.get(base + "macaddr") or ""),
        name=str(raw.get(base + "name") or ""),
        phy=str(raw.get(base + "phyname") or ""),
        type=str(raw.get(base + "basic_type") or raw.get(base + "type") or ""),
        crypt=str(raw.get(base + "crypt") or ""),
        channel=str(raw.get(base + "channel") or ""),
        freq=int(raw.get(base + "frequency") or 0),
        signal=_sig(raw),
        packets=int(raw.get(base + "packets") or raw.get("kismet.device.base.packets") or 0),
        manuf=str(raw.get(base + "manuf") or ""),
        first_seen=float(raw.get(base + "first_time") or 0),
        last_seen=float(raw.get(base + "last_time") or 0),
        lat=lat,
        lon=lon,
        advertised=advertised,
        probed=probed,
        clients=_clients(raw),
    )
    ds = raw.get("kismet.device.base.datasources")
    if isinstance(ds, list):
        dev.datasources = len(ds)
    return dev


def parse_devices(payload: Any) -> list[Device]:
    """Accept the several shapes Kismet / exports hand back."""
    rows: Iterable[Any]
    if isinstance(payload, dict):
        rows = payload.get("devices") or payload.get("kismet.devices") or []
        if not rows and "kismet.device.base.key" in payload:
            rows = [payload]
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    out: list[Device] = []
    for r in rows:
        if isinstance(r, str):
            try:
                r = json.loads(r)
            except json.JSONDecodeError:
                continue
        d = parse_device(r)
        if d and (d.mac or d.key):
            out.append(d)
    return out


@dataclass
class Alert:
    ts: float = 0.0
    header: str = ""
    message: str = ""
    severity: int = 0

    @property
    def when(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.ts)) if self.ts else "--:--:--"


def parse_alerts(payload: Any) -> list[Alert]:
    rows = payload if isinstance(payload, list) else []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        inner = r.get("kismet.alert") if isinstance(r.get("kismet.alert"), dict) else r
        out.append(Alert(
            ts=float(inner.get("kismet.alert.timestamp") or 0),
            header=str(inner.get("kismet.alert.header") or inner.get("header") or "?"),
            message=str(inner.get("kismet.alert.text") or inner.get("text") or ""),
            severity=int(inner.get("kismet.alert.severity") or 0),
        ))
    return sorted(out, key=lambda a: a.ts, reverse=True)


SPARK = "▁▂▃▄▅▆▇█"

def sparkline(values: list[int], width: int = 24) -> str:
    if not values:
        return "—"
    vals = values[-width:]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return SPARK[3] * len(vals)
    return "".join(SPARK[round((v - lo) / (hi - lo) * 7)] for v in vals)
