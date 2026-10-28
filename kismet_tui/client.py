"""Live Kismet REST client (stdlib only -- no httpx/requests to break
on a field laptop) plus an offline ``.kismet`` SQLite reader.

REST shape follows what John's existing tooling already uses:
``devices/views/all/devices.json``, ``system/status.json``,
``gps/location.json`` -- polled, tolerant of missing endpoints (a 404 on
alerts should dim that pane, not kill the app).
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import time
import urllib.request
import urllib.error

from .models import parse_alerts, parse_devices, Device, Alert


def _parse_gps(gps) -> dict:
    """GPS location, exporter-verified shape: the response carries
    ``kismet.common.location.geopoint`` = [lon, lat] (either at the top
    level or inside a ``kismet.common.location`` map), plus a fix that
    may be an int (0/2/3) or a string."""
    if not isinstance(gps, dict):
        return {}
    loc = gps.get("kismet.common.location")
    if not isinstance(loc, dict):
        loc = gps
    gp = loc.get("kismet.common.location.geopoint") or gps.get(
        "kismet.common.location.geopoint") or []
    lat = lon = 0.0
    if isinstance(gp, (list, tuple)) and len(gp) >= 2 and gp[0] and gp[1]:
        lon, lat = float(gp[0]), float(gp[1])
    fix_raw = loc.get("kismet.common.location.fix")
    fix = {0: "no fix", 2: "2D", 3: "3D"}.get(fix_raw, str(fix_raw or ""))
    return {"lat": lat, "lon": lon, "fix": fix,
            "alt": loc.get("kismet.common.location.alt") or 0,
            "speed": loc.get("kismet.common.location.speed") or 0}


class KismetClient:
    def __init__(self, base: str = "http://localhost:2501",
                 user: str = "", password: str = "", timeout: float = 4.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self._auth = None
        self._prev_pk_total = 0
        self._prev_pk_time = 0.0
        if user:
            token = base64.b64encode(f"{user}:{password}".encode()).decode()
            self._auth = f"Basic {token}"
        self._history: dict[str, list[int]] = {}

    def _get(self, path: str):
        req = urllib.request.Request(self.base + path,
                                     headers={"Accept": "application/json"})
        if self._auth:
            req.add_header("Authorization", self._auth)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def poll(self) -> dict:
        out: dict = {"devices": [], "alerts": [], "gps": {}, "status": {},
                     "connected": False}
        try:
            status = self._get("/system/status.json")
            out["connected"] = True
            if isinstance(status, dict):
                s = status.get("kismet.system") or status
                out["status"] = {
                    "uptime": s.get("kismet.system.timestamp.sec_loop") or 0,
                    "pps": s.get("kismet.system.packets.rate") or 0,
                    "server": s.get("kismet.system.server_name") or "kismet",
                }
        except Exception as exc:  # connection refused etc. -- show, don't crash
            out["error"] = f"{type(exc).__name__}: {exc}"
            return out
        for path, key, parser in (
            ("/devices/views/all/devices.json", "devices", parse_devices),
            ("/alerts/wrapped/last-time/-3600/alerts.json", "alerts", parse_alerts),
        ):
            try:
                out[key] = parser(self._get(path))
            except Exception:
                pass  # endpoint shape varies by version; pane stays empty
        try:
            gps = self._get("/gps/location.json")
            out["gps"] = _parse_gps(gps)
        except Exception:
            pass
        # Packet rate: Kismet's status field name for this has moved
        # between versions, so derive it from the device packet totals
        # we already fetched -- delta over wall time between polls.
        total_pk = sum(d.packets for d in out["devices"])
        now = time.time()
        if self._prev_pk_time and total_pk >= self._prev_pk_total:
            dt = now - self._prev_pk_time
            if dt > 0:
                out["status"]["pps"] = round((total_pk - self._prev_pk_total) / dt)
        self._prev_pk_total, self._prev_pk_time = total_pk, now
        # stitch signal history across polls (REST is stateless)
        for d in out["devices"]:  # type: Device
            hist = self._history.setdefault(d.mac or d.key, [])
            if d.signal:
                hist.append(d.signal)
                self._history[d.mac or d.key] = hist[-40:]
            d.signal_history = list(hist)
        return out


class KismetDB:
    """Offline reader for a .kismet log. WAL mode -> safe to open while
    Kismet is still writing it (read-only URI, never locks the writer)."""

    def __init__(self, path: str):
        self.path = path
        self._history: dict[str, list[int]] = {}
        self._cache_key: tuple | None = None
        self._cache_result: dict | None = None

    def poll(self) -> dict:
        # A big log takes seconds to parse (19k devices / ~1 GB file =
        # ~16 s measured). Kismet only flushes it every ~30 s anyway, so
        # don't re-parse an unchanged file on every 2 s UI tick.
        try:
            st = os.stat(self.path)
            key = (st.st_size, st.st_mtime_ns)
        except OSError:
            key = None
        if key is not None and key == self._cache_key \
                and self._cache_result is not None:
            return self._cache_result
        result = self._read()
        if key is not None:
            self._cache_key, self._cache_result = key, result
        return result

    def _read(self) -> dict:
        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=5)
        try:
            rows = con.execute(
                "SELECT device, strongest_signal, avg_lat, avg_lon "
                "FROM devices ORDER BY last_time DESC").fetchall()
        finally:
            con.close()
        devices = parse_devices([r[0] for r in rows])
        # The devices table's summary columns are Kismet's own rollup --
        # use them to backfill anything the JSON parse missed (rows and
        # parsed devices are in the same order).
        for dev, (_blob, sig, la, lo) in zip(devices, rows):
            if dev.signal == 0 and sig:
                dev.signal = int(sig)
            if not dev.located and la and lo:
                dev.lat, dev.lon = float(la), float(lo)
        return {"devices": devices, "alerts": [], "gps": {},
                "status": {"server": f".kismet file: {self.path}"},
                "connected": True}
