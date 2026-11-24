#!/bin/bash
# run-with-csv.sh — run droneid-go with a live CSV log of detections.
#
# 1. Stops the systemd zmq-decoder service if active (it would otherwise
#    bind the same ZMQ port as the manual run started here). Skipped when
#    this script IS the zmq-decoder service's own ExecStart (systemd sets
#    $INVOCATION_ID on every process of a unit) - it already owns the
#    port, and self-stopping mid-start would just kill this run.
# 2. Starts csv_logger.py in the background, subscribed to droneid-go's
#    ZMQ output, writing detections to a CSV file.
# 3. Runs droneid-go in the foreground with ZMQ publishing enabled.
#
# WiFi (-i), verbose, 5GHz+hop, survey and EU-legal hop channels are all
# baked in below, so the normal invocation only needs:
#   sudo ./run-with-csv.sh -i wlx00c0cab7f854 -ble /dev/ttyUSB0
#
# Any flag passed on the command line overrides its baked-in default
# (droneid-go's flag parser keeps the last occurrence of a flag).
#
# Env overrides:
#   ZMQ_BIND      ZMQ publisher bind address for droneid-go   (default 0.0.0.0:4224)
#   ZMQ_CONN      ZMQ endpoint the CSV logger connects to      (default tcp://127.0.0.1:4224)
#   CSV_OUT       CSV output file                              (default detections.csv)
#   CSV_LOGGER_LOG  csv_logger.py's own stderr/stdout log      (default /dev/shm/droneid-csv_logger.<uid>.log,
#                 tmpfs - the detections themselves still land on disk in
#                 CSV_OUT, but this incidental debug log doesn't need to.
#                 Per-uid name because /dev/shm is sticky + world-writable:
#                 with fs.protected_regular set, even root can't append to
#                 a file another user left there, so a sudo run after a
#                 non-sudo run would otherwise fail with "Permission denied")
#   BLE_DEV       BLE sniffle dongle serial port               (default /dev/ttyUSB0)
#   BLE_BAUD      BLE dongle baud rate                         (default 2000000)
#                 droneid-go's "-ble auto" mis-detects this Sonoff CC2652P
#                 dongle as CP2102 and probes at 921600, which never syncs -
#                 so this script always passes an explicit -ble/-ble-baud.
#   HOP_CHANNELS  WiFi hop channel list                        (default EU-legal set, see below)
#   HOP_CYCLE     Dwell time per channel, seconds               (default "2,1,1,1,1,1,1,1,1,1,1")
#
# HOP_CHANNELS default is EU/ETSI-legal 5GHz only: 36,40,44,48 (UNII-1) and
# 52,116,132,144 (UNII-2/2e, DFS), plus 2.4GHz 1,6,11. US UNII-3 channels
# 149/153/157/161/165 (droneid-go's US-tuned upstream default) aren't part
# of the ETSI WiFi allocation, so EU-market drones won't broadcast there.

set -euo pipefail

# WiFi capture (-i, or its alias -wifi) needs root: droneid-go runs `ip link set <iface> down`
# and `iw ... set monitor`, which need CAP_NET_ADMIN. Without it, it just
# retries "Operation not permitted" forever while BLE keeps running, so
# fail fast instead. (BLE-only runs work unprivileged via the dialout group.)
if [ "$(id -u)" -ne 0 ]; then
    for arg in "$@"; do
        case "$arg" in
            -i|--i|-i=*|--i=*|-wifi|--wifi|-wifi=*|--wifi=*)
                echo "[run-with-csv] WiFi capture (-i/-wifi) needs root - rerun with: sudo $0 $*" >&2
                exit 1 ;;
        esac
    done
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ZMQ_BIND="${ZMQ_BIND:-0.0.0.0:4224}"
ZMQ_CONN="${ZMQ_CONN:-tcp://127.0.0.1:4224}"
CSV_OUT="${CSV_OUT:-detections.csv}"
CSV_LOGGER_LOG="${CSV_LOGGER_LOG:-/dev/shm/droneid-csv_logger.$(id -u).log}"
BLE_DEV="${BLE_DEV:-/dev/ttyUSB0}"
BLE_BAUD="${BLE_BAUD:-2000000}"
HOP_CHANNELS="${HOP_CHANNELS:-1,6,11,36,40,44,48,52,116,132,144}"
HOP_CYCLE="${HOP_CYCLE:-2,1,1,1,1,1,1,1,1,1,1}"

# Locate droneid-go binary. Upstream droneid-go is the droneid-go/ git
# submodule; a droneid-go/droneid (e.g. a local build) wins over the
# prebuilt binaries in droneid-go/bin/.
UPSTREAM_DIR="$SCRIPT_DIR/droneid-go"
ARCH=$(uname -m)
if [ -x "$UPSTREAM_DIR/droneid" ]; then
    DRONEID_BIN="$UPSTREAM_DIR/droneid"
else
    case "$ARCH" in
        x86_64)         DRONEID_BIN="$UPSTREAM_DIR/bin/droneid-linux-x86_64" ;;
        aarch64|arm64)  DRONEID_BIN="$UPSTREAM_DIR/bin/droneid-linux-arm64" ;;
        *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
    esac
fi
[ -x "$DRONEID_BIN" ] || {
    echo "droneid-go binary not found/executable: $DRONEID_BIN" >&2
    echo "Is the submodule checked out? Run: git -C '$SCRIPT_DIR' submodule update --init" >&2
    exit 1
}

# Each prebuilt binary was compiled against a specific libzmq patch
# version, and pebbe/zmq4 (its ZMQ binding) refuses to start if the
# runtime libzmq is a different patch (x86_64 binary -> 4.3.4, arm64
# binary -> 4.3.5, per droneid-go/README.md). Preload a tiny shim that makes
# zmq_version() report whichever patch this binary expects - every
# other ZMQ call still runs against the real system libzmq. See
# zmq-version-shim/.
case "$ARCH" in
    x86_64)        SHIM_PATCH=4 ;;
    aarch64|arm64) SHIM_PATCH=5 ;;
    *)             SHIM_PATCH="" ;;
esac

ZMQ_PRELOAD=""
if [ -n "$SHIM_PATCH" ]; then
    SHIM_SRC="$SCRIPT_DIR/zmq-version-shim/zmq_version_shim.c"
    SHIM_SO="$SCRIPT_DIR/zmq-version-shim/libzmqvershim-4.3.${SHIM_PATCH}.so"
    if [ ! -f "$SHIM_SO" ] && [ -f "$SHIM_SRC" ] && command -v gcc &>/dev/null; then
        echo "[run-with-csv] building libzmq version-check shim (4.3.$SHIM_PATCH)..."
        gcc -shared -fPIC -DZMQ_SHIM_PATCH="$SHIM_PATCH" -o "$SHIM_SO" "$SHIM_SRC"
    fi
    if [ -f "$SHIM_SO" ]; then
        ZMQ_PRELOAD="$SHIM_SO"
    else
        echo "[run-with-csv] warning: libzmq version shim not available (no gcc?) - droneid-go may fail to create its ZMQ publisher" >&2
    fi
fi

# 1. Free up the ZMQ port if the systemd service already owns it
if [ -z "${INVOCATION_ID:-}" ] && systemctl is-active --quiet zmq-decoder 2>/dev/null; then
    echo "[run-with-csv] stopping systemd zmq-decoder service (port ${ZMQ_BIND##*:} would conflict)..."
    systemctl stop zmq-decoder
fi

CSV_LOGGER_PID=""
cleanup() {
    if [ -n "$CSV_LOGGER_PID" ] && kill -0 "$CSV_LOGGER_PID" 2>/dev/null; then
        echo "[run-with-csv] stopping csv_logger.py (pid $CSV_LOGGER_PID)..."
        kill "$CSV_LOGGER_PID" 2>/dev/null || true
        wait "$CSV_LOGGER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# 2. Start CSV logger in background
echo "[run-with-csv] starting csv_logger.py -> $CSV_OUT"
python3 "$SCRIPT_DIR/csv_logger.py" --zmq-endpoint "$ZMQ_CONN" --out "$CSV_OUT" \
    >>"$CSV_LOGGER_LOG" 2>&1 &
CSV_LOGGER_PID=$!
sleep 1
kill -0 "$CSV_LOGGER_PID" 2>/dev/null || { echo "[run-with-csv] csv_logger.py failed to start, check $CSV_LOGGER_LOG" >&2; exit 1; }

# 3. Run droneid-go in the foreground (not exec'd, so the cleanup trap
#    still fires for the CSV logger once droneid-go exits or is signaled).
#    Its "Setting channel N on IFACE" / "Hopped to channel N (dwell: Ns)"
#    chatter is one line per hop, which floods the terminal during
#    channel hopping. Drop the "Setting channel" line (redundant) and
#    collapse "Hopped to channel N ..." to just N, packed onto ~100-char
#    rows; every other line (timestamped logs, errors, status) still
#    prints immediately, one per line, unchanged.
echo "[run-with-csv] starting droneid-go (zmq bind $ZMQ_BIND, ble $BLE_DEV @ $BLE_BAUD, hop $HOP_CHANNELS)..."
LD_PRELOAD="$ZMQ_PRELOAD" "$DRONEID_BIN" -z -zmqsetting "$ZMQ_BIND" \
    -ble "$BLE_DEV" -ble-baud "$BLE_BAUD" \
    -v -g -survey -hop-channels "$HOP_CHANNELS" -hop-cycle "$HOP_CYCLE" \
    "$@" 2>&1 | awk '
    function flush() { if (buf != "") { print buf; fflush(); buf = "" } }
    /^Setting channel [0-9]+ on / { next }
    /^Hopped to channel [0-9]+ / {
        ch = $4
        cand = (buf == "") ? ch : buf " " ch
        if (length(cand) > 100) { flush(); buf = ch } else { buf = cand }
        next
    }
    { flush(); print; fflush() }
    END { flush() }
'
