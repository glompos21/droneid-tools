#!/usr/bin/env python3
"""Tiny web viewer for detections.csv (written by csv_logger.py).

Run:   python3 csv_viewer.py [--csv detections.csv] [--host 127.0.0.1] [--port 8080]
Open:  http://127.0.0.1:8080

Standard library only. Each poll reads just the lines appended since the
last one, so it stays cheap while csv_logger.py keeps writing. Read-only
and no auth: only use --host 0.0.0.0 on a trusted network.

Grouping: droneid-go sends each Remote ID broadcast as a burst of messages
(Basic ID, Location/Vector, Self-ID, System, Operator ID) and csv_logger.py
writes one row per message. Only Basic ID carries the serial/MAC, so each
following row belongs to the drone of the last Basic ID row. Positions are
read from raw_json, not the extracted columns: csv_logger.py puts the
System Message's lat/lon (the *pilot*) into the same latitude/longitude
columns as the drone's own position.
"""
import argparse
import csv
import io
import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
TRACK_MAX = 2000  # track points kept per drone
RECENT_MAX = 20   # raw messages kept per drone for the detail view
_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")


def num(v):
    """Leading number of a value like 80, '80.0 m' or '-0.5 m/s'; None if none."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM.match(str(v).strip())
    return float(m.group()) if m else None


def channel(freq_mhz):
    """WiFi channel number for a frequency in MHz (2.4 / 5 GHz); None otherwise."""
    f = num(freq_mhz)
    if f is None:
        return None
    f = int(f)
    if f == 2484:
        return 14
    if 2412 <= f <= 2472:
        return (f - 2407) // 5
    if 5000 < f < 5900:
        return (f - 5000) // 5
    return None


def latlon(lat, lon):
    """(lat, lon) floats, or None for missing / out-of-range / 0,0 (no fix)."""
    lat, lon = num(lat), num(lon)
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def epoch(ts):
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (TypeError, ValueError):
        return None


def new_drone(key, t):
    return {
        "key": key, "ids": [], "mac": None, "first_seen": t, "last_seen": t,
        "frames": 0, "rssi": None, "rssi_min": None, "rssi_max": None,
        "transport": None, "freq": None, "channel": None, "ua_type": None,
        "id_type": None, "status": None, "pos": None, "alt_geo": None,
        "height": None, "height_type": None, "speed": None, "vert_speed": None,
        "heading": None, "pilot": None, "operator_id": None, "self_id": None,
        "classification": None,
        "track": deque(maxlen=TRACK_MAX),    # [lat, lon, epoch]
        "recent": deque(maxlen=RECENT_MAX),  # {"t", "type", "data"}
    }


class Store:
    """Drone state built incrementally from the CSV file."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self._reset()

    def _reset(self):
        self.offset = 0      # bytes of the file consumed so far
        self.header = None
        self.rows = 0
        self.drones = {}     # key -> drone dict
        self.by_mac = {}     # MAC -> key
        self.current = None  # key of the drone of the last Basic ID row

    def refresh(self):
        with self.lock:
            try:
                size = os.path.getsize(self.path)
            except OSError:
                self._reset()
                return
            if size < self.offset:  # truncated, e.g. csv_logger.py --fresh
                self._reset()
            if size == self.offset:
                return
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read(size - self.offset)
            end = chunk.rfind(b"\n") + 1  # only consume complete lines
            if end == 0:
                return
            self.offset += end
            text = chunk[:end].decode("utf-8", "replace")
            for rec in csv.reader(io.StringIO(text, newline="")):
                if self.header is None:
                    self.header = rec
                    continue
                self.rows += 1
                self._ingest(dict(zip(self.header, rec)))

    def _ingest(self, rec):
        ts = rec.get("received_at", "")
        t = epoch(ts)
        try:
            msg = json.loads(rec.get("raw_json") or "")
        except json.JSONDecodeError:
            return
        if t is None or not isinstance(msg, dict):
            return
        for kind, payload in msg.items():
            if isinstance(payload, dict):
                self._message(kind, payload, t, ts)

    def _drone_for(self, uid, mac, t):
        """Find or create the drone for a serial and/or MAC."""
        key = ((self.by_mac.get(mac) if mac else None)
               or (uid if uid in self.drones else None) or uid or mac)
        if not key:
            return None
        d = self.drones.get(key)
        if d is None:
            d = self.drones[key] = new_drone(key, t)
        if mac:
            self.by_mac[mac] = key
            d["mac"] = mac
        if uid and uid not in d["ids"]:
            d["ids"].append(uid)
        return d

    def _message(self, kind, p, t, ts):
        if kind == "Basic ID":
            d = self._drone_for(p.get("id"), p.get("MAC"), t)
            if d is None:
                return
            self.current = d["key"]
            d["frames"] += 1
            rssi = num(p.get("RSSI"))
            if rssi is not None:
                d["rssi"] = rssi
                d["rssi_min"] = rssi if d["rssi_min"] is None else min(d["rssi_min"], rssi)
                d["rssi_max"] = rssi if d["rssi_max"] is None else max(d["rssi_max"], rssi)
            d["transport"] = p.get("transport") or d["transport"]
            if p.get("frequency_mhz") is not None:
                d["freq"] = num(p["frequency_mhz"])
                d["channel"] = channel(p["frequency_mhz"])
            d["ua_type"] = p.get("ua_type", d["ua_type"])
            d["id_type"] = p.get("id_type") or d["id_type"]
        else:
            # Messages carrying their own MAC (e.g. single BLE adverts) go
            # to that drone; the rest follow the last Basic ID.
            mac = p.get("MAC")
            d = self._drone_for(None, mac, t) if mac else self.drones.get(self.current)
            if d is None:
                return
            if kind == "Location/Vector Message":
                pos = latlon(p.get("latitude"), p.get("longitude"))
                if pos:
                    d["pos"] = pos
                    tr = d["track"]
                    if not tr or (tr[-1][0], tr[-1][1]) != pos:
                        tr.append([pos[0], pos[1], t])
                d["alt_geo"] = num(p.get("geodetic_altitude"))
                d["height"] = num(p.get("height_agl", p.get("height")))
                d["height_type"] = p.get("height_type")
                d["speed"] = num(p.get("speed"))
                d["vert_speed"] = num(p.get("vert_speed"))
                d["heading"] = num(p.get("direction"))
                d["status"] = p.get("op_status") or d["status"]
            elif kind == "System Message":
                pilot = latlon(p.get("operator_latitude", p.get("latitude")),
                               p.get("operator_longitude", p.get("longitude")))
                d["pilot"] = pilot or d["pilot"]
                d["classification"] = p.get("classification_type") or d["classification"]
            elif kind == "Self-ID Message":
                d["self_id"] = p.get("text") or d["self_id"]
            elif kind == "Operator ID Message":
                d["operator_id"] = p.get("operator_id") or d["operator_id"]
        d["last_seen"] = max(d["last_seen"], t)
        d["recent"].append({"t": ts, "type": kind, "data": p})

    def snapshot(self):
        with self.lock:
            return {
                "file": self.path,
                "rows": self.rows,
                "drones": [dict(d, track=list(d["track"]), recent=list(d["recent"]))
                           for d in self.drones.values()],
            }


def make_handler(store):
    page = os.path.join(HERE, "viewer.html")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                with open(page, "rb") as f:
                    self._send(200, "text/html; charset=utf-8", f.read())
            elif path == "/api/drones":
                store.refresh()
                body = dict(store.snapshot(), now=time.time())
                self._send(200, "application/json", json.dumps(body).encode())
            else:
                self._send(404, "text/plain", b"not found")

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep the terminal quiet
            pass

    return Handler


def main():
    ap = argparse.ArgumentParser(description="Web viewer for detections.csv")
    ap.add_argument("--csv", default=os.path.join(HERE, "../detections.csv"),
                    help="CSV written by csv_logger.py")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address; 0.0.0.0 lets other machines connect (no auth)")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()

    store = Store(args.csv)
    store.refresh()
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(store))
    print(f"[csv_viewer] {args.csv}: {store.rows} rows, {len(store.drones)} drones"
          f" -> http://{args.host}:{args.port}/  (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
