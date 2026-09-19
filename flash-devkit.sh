#!/bin/zsh
# Flash the built Klipper ESP32 image to a devkit.
#
#   ./flash-devkit.sh /dev/cu.usbserial-XXXX
#
# Deliberately NOT for the Rodent: that board still has its FluidNC image and no
# verified backup. This refuses any port whose name looks like the Rodent's CH340.
set -u
# PIPE_FAIL matters here: `esptool ... | tail -3 || abort` reports *tail's* status,
# so without it a failed backup silently passes the guard and we flash anyway.
setopt PIPE_FAIL 2>/dev/null || set -o pipefail

PORT="${1:-}"
if [ -z "$PORT" ]; then
    echo "usage: $0 <serial-port>"
    echo "available:"; ls /dev/cu.* 2>/dev/null | grep -viE "bluetooth|debug-console" | sed 's/^/    /'
    exit 1
fi

BUILD="$(cd "$(dirname "$0")" && pwd)/fermino-esp32/out"
ESPTOOL="$(cd "$(dirname "$0")" && pwd)/esptool-venv/bin/esptool"

# Refuse the Rodent by MAC, not by device path: its /dev/cu.* name changes with
# whichever USB port it is plugged into (it has already moved once).
# Board MAC. Export RODENT_MAC for your own board:
#   RODENT_MAC=aa:bb:cc:dd:ee:ff ./$(basename "$0")
RODENT_MAC="${RODENT_MAC:?set RODENT_MAC to the board MAC address}"
THIS_MAC=$("$ESPTOOL" --port "$PORT" --baud 115200 chip-id 2>/dev/null \
           | awk '/^MAC:/{print $2; exit}')
if [ -z "$THIS_MAC" ]; then
    echo "REFUSING: could not read the MAC of $PORT - not flashing blind."
    exit 1
fi
if [ "$THIS_MAC" = "$RODENT_MAC" ]; then
    echo "REFUSING: $PORT is the BTT Rodent ($THIS_MAC)."
    echo "Use a dedicated Rodent script once its backup is verified."
    exit 1
fi
echo "target MAC $THIS_MAC (not the Rodent) - proceeding"

for f in "$BUILD/bootloader/bootloader.bin" "$BUILD/partition_table/partition-table.bin" "$BUILD/klipper.bin"; do
    [ -f "$f" ] || { echo "missing artifact: $f  (run ./build-esp32.sh first)"; exit 1; }
done

echo "=== target ==="
"$ESPTOOL" --port "$PORT" --baud 115200 chip-id 2>&1 | grep -E "Chip type|Features|Crystal|MAC" || exit 1

echo
echo "=== backing up existing flash first (rollback point) ==="
mkdir -p "$(dirname "$0")/devkit-backup"
BAK="$(dirname "$0")/devkit-backup/devkit-preklipper-$(echo $PORT | tr '/' '_').bin"
if [ ! -f "$BAK" ]; then
    "$ESPTOOL" --port "$PORT" --baud 460800 read-flash 0 0x400000 "$BAK" 2>&1 | tail -3
    # Belt and braces: verify the artifact independently of any exit status.
    if [ ! -f "$BAK" ] || [ "$(stat -f%z "$BAK" 2>/dev/null || echo 0)" -ne 4194304 ]; then
        echo "!!! backup missing or wrong size - NOT flashing"
        rm -f "$BAK"; exit 1
    fi
    echo "    saved $BAK  md5=$(md5 -q "$BAK")"
else
    echo "    backup already exists: $BAK"
fi

echo
echo "=== flashing ==="
"$ESPTOOL" --port "$PORT" --baud 460800 --chip esp32 write-flash \
    --flash_mode dio --flash_freq 40m --flash_size detect \
    0x1000  "$BUILD/bootloader/bootloader.bin" \
    0x8000  "$BUILD/partition_table/partition-table.bin" \
    0x10000 "$BUILD/klipper.bin" 2>&1 | tail -20

echo
echo "=== verifying app image against flash ==="
"$ESPTOOL" --port "$PORT" --baud 460800 verify-flash 0x10000 "$BUILD/klipper.bin" 2>&1 | tail -5
