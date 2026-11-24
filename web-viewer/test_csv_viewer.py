"""Tests for csv_viewer.py. Run: python3 -m pytest test_csv_viewer.py"""
import csv
import io
import json

import pytest

from csv_viewer import Store, channel, latlon, num

HEADER = ["received_at", "id", "latitude", "longitude", "raw_json"]


def row(ts, kind, payload):
    out = io.StringIO()
    csv.writer(out).writerow([f"2026-11-24T09:{ts}+00:00", "", "", "",
                              json.dumps({kind: payload}, separators=(",", ":"))])
    return out.getvalue()


def frame(ts, serial, mac, rssi, lat, lon, pilot_lat, pilot_lon):
    """One broadcast as csv_logger.py writes it: 5 rows, only Basic ID names the drone."""
    return "".join([
        row(ts, "Basic ID", {"id": serial, "MAC": mac, "RSSI": rssi, "ua_type": 2,
                             "transport": "WiFi-Beacon", "frequency_mhz": 2437}),
        row(ts, "Location/Vector Message", {"latitude": str(lat), "longitude": str(lon),
                                            "geodetic_altitude": "80.0 m", "height_agl": "13.5 m",
                                            "speed": "9.00 m/s", "vert_speed": "-0.5 m/s",
                                            "direction": 279, "op_status": "Airborne"}),
        row(ts, "Self-ID Message", {"text": ""}),
        row(ts, "System Message", {"latitude": str(pilot_lat), "longitude": str(pilot_lon)}),
        row(ts, "Operator ID Message", {"operator_id": ""}),
    ])


def header():
    out = io.StringIO()
    csv.writer(out).writerow(HEADER)
    return out.getvalue()


@pytest.mark.parametrize("value, want", [
    (None, None),
    (True, None),
    (-72, -72.0),
    ("80.0 m", 80.0),
    ("-0.5 m/s", -0.5),
    ("35.3329783", 35.3329783),
    ("<3 m", None),
    ("Unknown", None),
])
def test_num(value, want):
    assert num(value) == want


@pytest.mark.parametrize("freq, want", [
    (2412, 1), (2437, 6), ("2462", 11), (2484, 14),
    (5180, 36), (5580, 116), (5720, 144), (5825, 165),
    (None, None), (900, None),
])
def test_channel(freq, want):
    assert channel(freq) == want


@pytest.mark.parametrize("lat, lon, want", [
    ("35.33", "25.17", (35.33, 25.17)),
    ("0", "0", None),       # no GPS fix
    ("91", "25", None),
    (None, "25", None),
])
def test_latlon(lat, lon, want):
    assert latlon(lat, lon) == want


def test_groups_rows_by_basic_id_and_separates_pilot(tmp_path):
    f = tmp_path / "d.csv"
    f.write_text(header()
                 + frame("51:48.1", "SER-A", "aa:aa", -72, 35.1, 25.1, 35.9, 25.9)
                 + frame("51:48.2", "SER-B", "bb:bb", -60, 36.1, 26.1, 36.9, 26.9)
                 + frame("51:49.1", "SER-A", "aa:aa", -65, 35.2, 25.2, 35.9, 25.9))
    s = Store(str(f))
    s.refresh()

    assert s.rows == 15
    assert sorted(s.drones) == ["SER-A", "SER-B"]
    a, b = s.drones["SER-A"], s.drones["SER-B"]
    assert a["pos"] == (35.2, 25.2)        # drone position from Location/Vector ...
    assert a["pilot"] == (35.9, 25.9)      # ... pilot from System Message, not mixed up
    assert b["pos"] == (36.1, 26.1) and b["pilot"] == (36.9, 26.9)
    assert [p[:2] for p in a["track"]] == [[35.1, 25.1], [35.2, 25.2]]
    assert (a["rssi"], a["rssi_min"], a["rssi_max"]) == (-65, -72, -65)
    assert a["frames"] == 2 and a["channel"] == 6
    assert (a["height"], a["speed"], a["heading"]) == (13.5, 9.0, 279)


def test_incremental_read_skips_partial_line(tmp_path):
    f = tmp_path / "d.csv"
    first = header() + frame("51:48.1", "SER-A", "aa:aa", -72, 35.1, 25.1, 35.9, 25.9)
    second = frame("51:49.1", "SER-B", "bb:bb", -60, 36.1, 26.1, 36.9, 26.9)
    f.write_text(first + second[:40])      # logger mid-write
    s = Store(str(f))
    s.refresh()
    assert s.rows == 5 and list(s.drones) == ["SER-A"]

    f.write_text(first + second)           # rest of the line arrives
    s.refresh()
    assert s.rows == 10 and sorted(s.drones) == ["SER-A", "SER-B"]


def test_truncated_file_resets_state(tmp_path):
    f = tmp_path / "d.csv"
    f.write_text(header() + frame("51:48.1", "SER-A", "aa:aa", -72, 35.1, 25.1, 35.9, 25.9)
                 + frame("51:48.2", "SER-B", "bb:bb", -60, 36.1, 26.1, 36.9, 26.9))
    s = Store(str(f))
    s.refresh()
    assert len(s.drones) == 2

    f.write_text(header() + frame("52:00.0", "SER-C", "cc:cc", -50, 37.1, 27.1, 37.9, 27.9))
    s.refresh()
    assert list(s.drones) == ["SER-C"] and s.rows == 5


def test_second_basic_id_with_same_mac_merges(tmp_path):
    f = tmp_path / "d.csv"
    f.write_text(header()
                 + row("51:48.1", "Basic ID", {"id": "SERIAL-1", "MAC": "aa:aa"})
                 + row("51:48.1", "Basic ID", {"id": "CAA-REG-9", "MAC": "aa:aa"}))
    s = Store(str(f))
    s.refresh()
    assert list(s.drones) == ["SERIAL-1"]
    assert s.drones["SERIAL-1"]["ids"] == ["SERIAL-1", "CAA-REG-9"]


def test_bad_rows_are_skipped(tmp_path):
    f = tmp_path / "d.csv"
    f.write_text(header()
                 + 'not-a-time,,,,"{}"\r\n'
                 + '2026-11-24T09:51:48+00:00,,,,"not json"\r\n'
                 + row("51:48.1", "Location/Vector Message", {"latitude": "35"}))  # no Basic ID yet
    s = Store(str(f))
    s.refresh()
    assert s.rows == 3 and s.drones == {}
