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
import sqlite3
import urllib.request
import urllib.error

from .models import parse_alerts, parse_devices, Device, Alert


class KismetClient:
    def __init__(self, base: str = "http://localhost:2501",
                 user: str = "", password: str = "", timeout: float = 4.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self._auth = None
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
            loc = (gps.get("kismet.common.location") or gps) if isinstance(gps, dict) else {}
            avg = loc.get("kismet.common.location.avg_lat_lon") or {}
            out["gps"] = {
                "lat": avg.get("kismet.location.lat") or loc.get("kismet.location.lat") or 0,
                "lon": avg.get("kismet.location.lon") or loc.get("kismet.location.lon") or 0,
                "fix": loc.get("kismet.common.location.fix") or "",
            }
        except Exception:
            pass
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

    def poll(self) -> dict:
        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=5)
        try:
            rows = con.execute(
                "SELECT device FROM devices ORDER BY last_time DESC").fetchall()
        finally:
            con.close()
        devices = parse_devices([r[0] for r in rows])
        return {"devices": devices, "alerts": [], "gps": {},
                "status": {"server": f".kismet file: {self.path}"},
                "connected": True}
