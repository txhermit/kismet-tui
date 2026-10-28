import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kismet_tui.models import parse_devices, parse_device, sparkline
from kismet_tui.demo import DemoSource
from kismet_tui.client import KismetDB, _parse_gps

# Fixture built from the REAL Kismet record structure, verified
# 2026-10-28 against an actual .kismet log + live REST field docs
# (values/MACs are invented, the *shape* is not):
#  - signal: nested map at kismet.device.base.signal
#  - packets: FLAT keys kismet.device.base.packets.total / .rx_total / ...
#  - location: kismet.device.base.location -> avg_loc -> geopoint [lon, lat]
#  - type: full string "Wi-Fi AP"; ssid maps are LISTS of records
# v0.1.0's fixture invented a friendlier shape and the tests passed
# while the parser read nothing real Kismet sends. Never again.
RAW_AP = {
    "kismet.device.base.key": "IEEE802.11_AA",
    "kismet.device.base.macaddr": "AA:BB:CC:DD:EE:FF",
    "kismet.device.base.name": "CoffeeShop",
    "kismet.device.base.commonname": "CoffeeShop",
    "kismet.device.base.phyname": "IEEE802.11",
    "kismet.device.base.type": "Wi-Fi AP",
    "kismet.device.base.crypt": "WPA2",
    "kismet.device.base.channel": 6,
    "kismet.device.base.frequency": 2437000,
    "kismet.device.base.packets.total": 1234,
    "kismet.device.base.packets.rx_total": 1000,
    "kismet.device.base.packets.tx_total": 234,
    "kismet.device.base.first_time": 1000,
    "kismet.device.base.last_time": 2000,
    "kismet.device.base.manuf": "Ubiquiti",
    "kismet.device.base.seenby": [{"x": 1}, {"x": 2}],
    "kismet.device.base.signal": {
        "kismet.common.signal.type": "dbm",
        "kismet.common.signal.last_signal": -47,
        "kismet.common.signal.max_signal": -40,
    },
    "kismet.device.base.location": {
        "kismet.common.location.avg_loc": {
            "kismet.common.location.geopoint": [-98.4936, 29.4241],
            "kismet.common.location.fix": 3,
        },
    },
    "dot11.device": {
        "dot11.device.advertised_ssid_map": [
            {"dot11.advertisedssid.ssid": "CoffeeShop"}],
        "dot11.device.associated_client_map": {"0": {"x": 1}},
    },
}

GHOST = {  # inferred/bridged: signal component present but zeroed
    "kismet.device.base.macaddr": "00:00:5E:00:01:01",
    "kismet.device.base.phyname": "IEEE802.11",
    "kismet.device.base.type": "Wi-Fi Bridged",
    "kismet.device.base.packets.total": 0,
    "kismet.device.base.signal": {"kismet.common.signal.last_signal": 0},
}


def test_parse_real_shaped_ap():
    d = parse_device(RAW_AP)
    assert d.mac == "AA:BB:CC:DD:EE:FF" and d.signal == -47
    assert d.packets == 1234 and d.category == "AP"
    assert d.advertised == ["CoffeeShop"] and len(d.clients) == 1
    assert d.located and not d.inferred and d.freq_mhz == 2437.0
    assert (round(d.lat, 4), round(d.lon, 4)) == (29.4241, -98.4936)  # [lon, lat]!
    assert d.datasources == 2 and d.channel == "6"


def test_ghost_is_inferred_and_unlocated():
    d = parse_device(GHOST)
    assert d.inferred and not d.located and d.category == "Bridged"


def test_exporter_simplified_shape():
    # The shape kismet-grafana's exporter requests via field simplification.
    d = parse_device({
        "kismet.device.base.macaddr": "11:22:33:44:55:66",
        "kismet.device.base.type": "Wi-Fi Client",
        "last_signal": -71,
    })
    assert d.signal == -71 and d.category == "Client" and not d.inferred


def test_gps_parse():
    g = _parse_gps({"kismet.common.location.geopoint": [-98.49, 29.42],
                    "kismet.common.location.fix": 3})
    assert g["lat"] == 29.42 and g["lon"] == -98.49 and g["fix"] == "3D"


def test_demo_source_shape():
    data = DemoSource().poll()
    assert data["connected"] and len(data["devices"]) > 20
    assert any(d.inferred for d in data["devices"])       # ghosts on purpose
    assert any(d.probed for d in data["devices"])
    assert data["gps"]["lat"] != 0


def test_kismet_db_roundtrip(tmp_path):
    import json
    import sqlite3
    p = tmp_path / "test.kismet"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE devices (device TEXT, last_time INTEGER, "
                "strongest_signal REAL, avg_lat REAL, avg_lon REAL)")
    # Bind as bytes: real .kismet files store the device column as a
    # BLOB and sqlite returns bytes -- a str here let v0.1.0's DB reader
    # pass its test while returning zero devices from real files.
    con.execute("INSERT INTO devices VALUES (?, ?, ?, ?, ?)",
                (json.dumps(RAW_AP).encode(), 2000, -47, 29.4241, -98.4936))
    con.commit(); con.close()
    data = KismetDB(str(p)).poll()
    assert len(data["devices"]) == 1
    assert data["devices"][0].display_name == "CoffeeShop"
    assert data["devices"][0].signal == -47


def test_sparkline_and_bars():
    assert sparkline([-80, -60, -40]) != "—"
    assert parse_device(RAW_AP).signal_bar() != "········"
