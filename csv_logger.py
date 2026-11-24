#!/usr/bin/env python3
"""Subscribe to droneid-go's ZMQ drone output and log detections to CSV.

droneid-go publishes JSON Remote ID frames (single dict or list of dicts)
over a ZMQ PUB socket. Since the exact nesting of sub-messages (Basic ID,
Location/Vector, Self-ID, System, Operator ID, ...) can vary, common fields
are pulled out via a recursive, alias-based, case-insensitive key search
rather than hardcoded paths. The full original frame is always preserved
in the raw_json column so no data is ever lost, even for fields not
extracted into their own column.
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

import zmq

FIELD_ALIASES = {
    "transport": ["transport"],
    "id_type": ["id_type", "idtype"],
    "ua_type": ["ua_type", "uatype"],
    "id": ["id", "uas_id", "serial"],
    "frequency_mhz": ["frequency_mhz", "freq_mhz", "frequency"],
    "mac": ["mac", "mac_address", "source_address"],
    "rssi": ["rssi"],
    "latitude": ["latitude", "lat"],
    "longitude": ["longitude", "lon", "long"],
    "altitude_geo": ["altitude_geo", "geodetic_altitude", "alt"],
    "height": ["height"],
    "speed": ["speed"],
    "vert_speed": ["vert_speed", "vertical_speed"],
    "heading": ["heading", "direction", "track"],
    "operator_id": ["operator_id"],
    "operator_latitude": ["operator_latitude"],
    "operator_longitude": ["operator_longitude"],
    "self_id_text": ["text", "description"],
}
FIELDNAMES = ["received_at"] + list(FIELD_ALIASES.keys()) + ["raw_json"]


def find_any(obj, aliases):
    """Recursively search obj for the first key matching any alias (case-insensitive)."""
    aliases_lower = {a.lower() for a in aliases}
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k.lower() in aliases_lower:
                    return v
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def to_row(item):
    row = {"received_at": datetime.now(timezone.utc).isoformat()}
    for col, aliases in FIELD_ALIASES.items():
        row[col] = find_any(item, aliases)
    row["raw_json"] = json.dumps(item, separators=(",", ":"))
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zmq-endpoint", default="tcp://127.0.0.1:4224",
                     help="droneid-go ZMQ PUB endpoint to connect to")
    ap.add_argument("--out", default="detections.csv", help="CSV file to write")
    ap.add_argument("--fresh", action="store_true",
                     help="truncate the CSV file instead of appending")
    args = ap.parse_args()

    write_header = args.fresh or not os.path.exists(args.out) or os.path.getsize(args.out) == 0
    mode = "w" if args.fresh else "a"

    ctx = zmq.Context()
    sock = ctx.socket(zmq.SUB)
    sock.connect(args.zmq_endpoint)
    sock.subscribe(b"")
    sock.setsockopt(zmq.RCVTIMEO, 1000)

    print(f"[csv_logger] subscribed to {args.zmq_endpoint}, writing to {args.out}", flush=True)

    with open(args.out, mode, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
            f.flush()

        while True:
            try:
                raw = sock.recv()
            except zmq.Again:
                continue
            except KeyboardInterrupt:
                break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            for item in (msg if isinstance(msg, list) else [msg]):
                if not isinstance(item, dict):
                    continue
                writer.writerow(to_row(item))
            f.flush()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
