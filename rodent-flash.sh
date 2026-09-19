#!/bin/zsh
# Flash Klipper onto the BTT Rodent V1.1 -- the FIRST write to this board.
#
# Preconditions, all enforced and all checked against artifacts rather than
# exit statuses:
#   1. the target really is the Rodent (MAC match)
#   2. a verified 4MB backup exists at the known md5
#   3. the firmware actually contains shift-register support
#
# 460800 baud is broken on this CH340 - use 921600.
setopt PIPE_FAIL 2>/dev/null || set -o pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ESPTOOL="$HERE/esptool-venv/bin/esptool"
PORT="${1:-/dev/cu.wchusbserial120}"
BAUD="${2:-921600}"
BUILD="$HERE/fermino-esp32/out"
BACKUP="$HERE/rodent-backup/rodent-fluidnc-full-4MB.bin"
BACKUP_MD5="24d343d897f2994cd4c68388538eb0c9"
# Board MAC. Export RODENT_MAC for your own board:
#   RODENT_MAC=aa:bb:cc:dd:ee:ff ./$(basename "$0")
RODENT_MAC="${RODENT_MAC:?set RODENT_MAC to the board MAC address}"
echo "=== 1. target identity ==="
MAC=$("$ESPTOOL" --port "$PORT" --baud 115200 chip-id 2>/dev/null | awk '/^MAC:/{print $2; exit}')
echo "    MAC $MAC"
[ "$MAC" = "$RODENT_MAC" ] || { echo "!!! not the Rodent - aborting"; exit 1; }

echo "=== 2. rollback point ==="
[ -f "$BACKUP" ] || { echo "!!! no backup - REFUSING"; exit 1; }
SZ=$(stat -f%z "$BACKUP"); MD5=$(md5 -q "$BACKUP")
echo "    $SZ bytes, md5 $MD5"
[ "$SZ" -eq 4194304 ] || { echo "!!! backup wrong size - REFUSING"; exit 1; }
[ "$MD5" = "$BACKUP_MD5" ] || { echo "!!! backup md5 mismatch - REFUSING"; exit 1; }

echo "=== 3. firmware sanity ==="
for f in "$BUILD/bootloader/bootloader.bin" "$BUILD/partition_table/partition-table.bin" "$BUILD/klipper.bin"; do
    [ -f "$f" ] || { echo "!!! missing $f"; exit 1; }
done
if ! python3 -c "
import json,sys
j=json.load(open('$BUILD/klipper.dict'))
sys.exit(0 if 'SR0' in j['enumerations']['pin'] else 1)
"; then
    echo "!!! firmware has no SR pins - wrong build for the Rodent"; exit 1
fi
echo "    klipper.bin $(stat -f%z "$BUILD/klipper.bin") bytes, SR pins present"

echo
echo "=== 4. flashing ==="
"$ESPTOOL" --port "$PORT" --baud "$BAUD" --chip esp32 write-flash \
    --flash_mode dio --flash_freq 40m --flash_size detect \
    0x1000  "$BUILD/bootloader/bootloader.bin" \
    0x8000  "$BUILD/partition_table/partition-table.bin" \
    0x10000 "$BUILD/klipper.bin" 2>&1 | grep -E "Wrote|Hash of data|Compressed|error|Error"

echo
echo "=== 5. verifying each region against the device ==="
FAIL=0
# NB: the bootloader is deliberately NOT byte-compared. esptool patches image
# header byte 3 (flash_size<<4|flash_freq) at the bootloader offset to match the
# detected chip, and recomputes the appended SHA-256 - so 33 bytes always differ
# from the on-disk file. esptool's own "Hash of data verified" during the write
# already covers it.
for pair in "0x8000:$BUILD/partition_table/partition-table.bin" \
            "0x10000:$BUILD/klipper.bin"; do
    OFF="${pair%%:*}"; F="${pair#*:}"
    if "$ESPTOOL" --port "$PORT" --baud "$BAUD" verify-flash "$OFF" "$F" 2>&1 \
       | grep -q "Verification successful"; then
        echo "    $OFF $(basename $F) verified"
    else
        echo "    $OFF $(basename $F) VERIFY FAILED"; FAIL=1
    fi
done

echo
[ $FAIL -eq 0 ] && echo "FLASH_OK" || { echo "!!! verification failed"; exit 1; }
echo "restore with: esptool --port $PORT --baud 921600 write-flash 0x0 $BACKUP"
