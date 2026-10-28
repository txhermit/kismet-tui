"""Synthetic wardrive -- so the TUI is demoable / testable with no radio.

Generates a believable San Antonio drive: infrastructure APs that stay
put, clients that wander and probe, a handful of Bluetooth devices, the
odd inferred/bridged ghost (signal 0, location 0,0 -- on purpose, so the
'hide inferred' toggle has something to do), and periodic alerts.
Signals wander with a random walk; the GPS trundles around downtown.
"""
from __future__ import annotations

import random
import time

from .models import Alert, Device

_APS = [
    ("xfinitywifi", "WPA2", "6", 2437000), ("ATT_Fiber_5G", "WPA3", "44", 5220000),
    ("NETGEAR47", "WPA2", "11", 2462000), ("LaQuinta_Guest", "Open", "1", 2412000),
    ("UTHSA-Secure", "WPA2-Enterprise", "149", 5745000), ("linksys", "WEP", "3", 2422000),
    ("HP-Print-OfficeJet", "WPA2", "6", 2437000), ("MySpectrumWiFi", "WPA2", "36", 5180000),
    ("DIRECT-roku-881", "WPA2", "11", 2462000), ("WalmartWiFi", "Open", "6", 2437000),
    ("JMV5G-WIFI6", "WPA2", "48", 5240000), ("CenturyLink8821", "WPA2", "1", 2412000),
]
_PROBES = ["HomeWiFi", "Starbucks", "AA-Inflight", "eduroam", "iPhone Hotspot",
           "Galaxy S22", "Airport_Free", "Work VPN AP", "Pretty Fly for a WiFi"]
_MANUFS = ["Cisco", "Aruba", "Ubiquiti", "Netgear", "Samsung", "Apple", "Espressif", "TP-Link"]


class DemoSource:
    def __init__(self, seed: int = 42) -> None:
        self.rng = random.Random(seed)
        self.start = time.time()
        self.devices: dict[str, Device] = {}
        self.alerts: list[Alert] = []
        self.lat, self.lon = 29.4241, -98.4936   # downtown San Antonio
        self._seed_devices()

    def _mac(self) -> str:
        return ":".join(f"{self.rng.randrange(256):02X}" for _ in range(6))

    def _seed_devices(self) -> None:
        now = time.time()
        for i, (ssid, crypt, ch, freq) in enumerate(_APS):
            mac = self._mac()
            self.devices[mac] = Device(
                key=f"ap-{i}", mac=mac, name=ssid, phy="IEEE802.11",
                type="AP", crypt=crypt, channel=ch, freq=freq,
                signal=self.rng.randrange(-82, -38), packets=self.rng.randrange(400, 9000),
                manuf=self.rng.choice(_MANUFS), first_seen=now - self.rng.randrange(60, 2400),
                last_seen=now, lat=self.lat + self.rng.uniform(-.01, .01),
                lon=self.lon + self.rng.uniform(-.01, .01),
                advertised=[ssid], clients=[],
            )
        for i in range(14):
            mac = self._mac()
            probed = self.rng.sample(_PROBES, k=self.rng.randrange(1, 4))
            ch, freq = self.rng.choice([("1", 2412000), ("6", 2437000),
                                        ("11", 2462000), ("36", 5180000),
                                        ("44", 5220000)])
            self.devices[mac] = Device(
                key=f"cli-{i}", mac=mac, phy="IEEE802.11", type="Client",
                channel=ch, freq=freq,
                signal=self.rng.randrange(-88, -45), packets=self.rng.randrange(3, 600),
                manuf=self.rng.choice(_MANUFS), first_seen=now - self.rng.randrange(10, 900),
                last_seen=now, lat=self.lat, lon=self.lon, probed=probed,
            )
        for i, name in enumerate(["Galaxy Buds2", "Apple Watch", "Axon Body 4",
                                  "Tile Tracker", "Fitbit Charge"]):
            mac = self._mac()
            self.devices[mac] = Device(
                key=f"bt-{i}", mac=mac, name=name, phy="Bluetooth",
                type="BTLE", signal=self.rng.randrange(-85, -50),
                packets=self.rng.randrange(5, 300), manuf=self.rng.choice(_MANUFS),
                first_seen=now - 300, last_seen=now, lat=self.lat, lon=self.lon,
            )
        # The ghosts: inferred / bridged, never heard. Deliberate.
        for i in range(6):
            mac = self._mac()
            self.devices[mac] = Device(
                key=f"ghost-{i}", mac=mac, phy="IEEE802.11", type="Bridged",
                signal=0, packets=0, first_seen=now - 1800, last_seen=now - 1700,
            )

    # -- source interface (same as KismetClient / KismetDB) --------------
    def poll(self) -> dict:
        now = time.time()
        self.lat += self.rng.uniform(-0.0004, 0.0005)
        self.lon += self.rng.uniform(-0.0004, 0.0005)
        for d in self.devices.values():
            if d.inferred:
                continue
            d.signal = max(-92, min(-30, d.signal + self.rng.randrange(-4, 5)))
            d.signal_history = (d.signal_history + [d.signal])[-40:]
            d.packets += self.rng.randrange(0, 40)
            d.last_seen = now
            if d.type == "AP" and self.rng.random() < 0.2:
                d.clients = [self._mac()] if self.rng.random() < 0.5 else d.clients
        if self.rng.random() < 0.06:
            self.alerts.insert(0, Alert(
                ts=now, header=self.rng.choice(
                    ["NEWDEVICE", "DEAUTHFLOOD", "CLIENTPROBING", "CHANNELCHANGE"]),
                message="synthetic demo alert", severity=self.rng.randrange(0, 10)))
            self.alerts = self.alerts[:50]
        return {
            "devices": list(self.devices.values()),
            "alerts": list(self.alerts),
            "gps": {"lat": self.lat, "lon": self.lon, "fix": "3D",
                    "alt": 198.0, "speed": 7.3},
            "status": {"uptime": now - self.start,
                       "pps": self.rng.randrange(180, 640),
                       "datasources": 4, "server": "demo (no radio)"},
            "connected": True,
        }
