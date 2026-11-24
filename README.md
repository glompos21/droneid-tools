# droneid-tools

Run [droneid-go](https://github.com/alphafox02/droneid-go) (Open Drone ID /
Remote ID receiver for WiFi + BLE), log what it hears to a CSV file, and view
that CSV in a browser.

droneid-go itself is not modified: it is included as a git submodule in
`droneid-go/`, and its prebuilt binaries are used as-is.

## Setup

```
git clone --recurse-submodules <this repo> ~/droneid-tools
# or, in an existing clone:
git submodule update --init
```

Needs `iw`, `iproute2`, `libpcap0.8`, `libzmq5`, `python3` with `pyzmq`
(for `csv_logger.py`), and `gcc` on first run (it builds the libzmq version
shim).

## Use

```
sudo ./run-with-csv.sh -i <wifi-iface>   # capture WiFi + BLE -> detections.csv
python3 csv_viewer.py                    # viewer at http://127.0.0.1:8080
```

See `commands.md` for more invocations, the systemd service, and updating
the submodule.

## Files

| File | Purpose |
|---|---|
| `run-with-csv.sh` | Starts `csv_logger.py`, then droneid-go with ZMQ output, EU channel hopping and the BLE dongle settings baked in |
| `csv_logger.py` | Subscribes to droneid-go's ZMQ output and appends every message to `detections.csv` |
| `csv_viewer.py`, `viewer.html` | Read-only web viewer for the CSV: map, drone list, details (standard library only) |
| `test_csv_viewer.py` | Tests for the viewer: `python3 -m pytest test_csv_viewer.py` |
| `zmq-version-shim/` | `LD_PRELOAD` shim so the prebuilt binary accepts the system's libzmq patch version |
| `zmq-decoder.service` | systemd unit that runs `run-with-csv.sh` (paths are machine-specific) |
| `droneid-go/` | Upstream droneid-go (submodule) |
