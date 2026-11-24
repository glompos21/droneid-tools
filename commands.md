 sudo ./run-with-csv.sh -ble /dev/ttyUSB0 -ble-baud 2000000 -i wlx00c0cab7f854 -v -g -survey

 sudo ./run-with-csv.sh -ble /dev/ttyUSB0 -ble-baud 2000000 -i wlan1 -v -g -survey

 sudo ./run-with-csv.sh -i wlx00c0cab7f854 -ble /dev/ttyUSB0

 python3 csv_viewer.py          # web viewer for detections.csv -> http://127.0.0.1:8080

## Upstream droneid-go (submodule)

`droneid-go/` is the upstream repo https://github.com/alphafox02/droneid-go
as a git submodule; run-with-csv.sh uses its prebuilt `bin/` binaries.

```
git submodule update --init                # after a plain clone
git submodule update --remote droneid-go   # move to upstream's latest, then commit the new pointer
```

Don't run `droneid-go/install.sh`: it installs upstream's own
zmq-decoder.service over this one and copies the binary to
`~/WarDragon/droneid-go`.

## systemd service install (Raspberry Pi) — RAM-only log, multi-machine tail

Deploy (from dev machine to Pi) — copies this repo incl. the submodule's
binaries, without git metadata, the CSV, or the locally built shim:

```
rsync -a --exclude .git --exclude detections.csv --exclude '*.so' \
    ~/droneid-tools/ pi:~/droneid-tools/
```

(Once the repo has a remote: `git clone --recurse-submodules <url> ~/droneid-tools` on the Pi.)

If the Pi's path isn't `/home/<user>/droneid-tools`, edit
`zmq-decoder.service` first: `WorkingDirectory`, `ExecStart`,
`ReadWritePaths`, `Environment=HOME=`.

Install + enable (on the Pi):

```
sudo cp zmq-decoder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now zmq-decoder
```

Confirm the journal is RAM-only (no SD writes beyond detections.csv):

```
journalctl --disk-usage
```

Should report files under `/run`, not `/var/log/journal`. If it says
`/var/log/journal`, journald is persisting to disk — delete
`/var/log/journal` and reboot, or set `Storage=volatile` in
`/etc/systemd/journald.conf` and `systemctl restart systemd-journald`.

Watch live from any machine (any number of concurrent viewers):

```
ssh <pi-host> journalctl -u zmq-decoder -f
```

Service management:

```
sudo systemctl status zmq-decoder
sudo systemctl restart zmq-decoder
sudo systemctl stop zmq-decoder
```



wlx90de805be50f