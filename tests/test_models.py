import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kismet_tui.models import parse_devices, parse_device, sparkline
from kismet_tui.demo import DemoSource
from kismet_tui.client import KismetDB

RAW_AP = {
    "kismet.device.base.key": "IEEE802.11_AA",
    "kismet.device.base.macaddr": "AA:BB:CC:DD:EE:FF",
    "kismet.device.base.name": "CoffeeShop",
    "kismet.device.base.phyname": "IEEE802.11",
    "kismet.device.base.basic_type": "AP",
    "kismet.device.base.crypt": "WPA2",
    "kismet.device.base.channel": "6",
    "kismet.device.base.frequency": 2437000,
    "kismet.device.base.packets": 1234,
    "kismet.device.base.first_time": 1000,
    "kismet.device.base.last_time": 2000,
    "kismet.device.base.manuf": "Ubiquiti",
    "kismet.common.signal": {"kismet.common.signal.last_signal": -47},
    "kismet.common.location": {
        "kismet.common.location.avg_lat_lon": {
            "kismet.location.lat": 29.4241, "kismet.location.lon": -98.4936}},
    "dot11.device": {
        "dot11.device.advertised_ssid_map": {
            "x": {"dot11.advertisedssid.ssid": "CoffeeShop"}},
        "dot11.device.associated_client_map": {"11:22:33:44:55:66": {}},
    },
}

GHOST = {
    "kismet.device.base.macaddr": "00:00:5E:00:01:01",
    "kismet.device.base.phyname": "IEEE802.11",
    "kismet.device.base.basic_type": "Bridged",
    "kismet.device.base.packets": 0,
    "kismet.common.signal": {"kismet.common.signal.last_signal": 0},
}


def test_parse_ap():
    d = parse_device(RAW_AP)
    assert d.mac == "AA:BB:CC:DD:EE:FF" and d.signal == -47
    assert d.advertised == ["CoffeeShop"] and d.clients == ["11:22:33:44:55:66"]
    assert d.located and not d.inferred and d.freq_mhz == 2437.0


def test_ghost_is_inferred_and_unlocated():
    d = parse_device(GHOST)
    assert d.inferred and not d.located


def test_demo_source_shape():
    data = DemoSource().poll()
    assert data["connected"] and len(data["devices"]) > 20
    assert any(d.inferred for d in data["devices"])       # ghosts on purpose
    assert any(d.probed for d in data["devices"])
    assert data["gps"]["lat"] != 0


def test_kismet_db_roundtrip(tmp_path):
    import json
    p = tmp_path / "test.kismet"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE devices (device TEXT, last_time INTEGER)")
    con.execute("INSERT INTO devices VALUES (?, ?)",
                (json.dumps(RAW_AP), 2000))
    con.commit(); con.close()
    data = KismetDB(str(p)).poll()
    assert len(data["devices"]) == 1
    assert data["devices"][0].display_name == "CoffeeShop"


def test_sparkline_and_bars():
    assert sparkline([-80, -60, -40]) != "—"
    assert parse_device(RAW_AP).signal_bar() != "········"
