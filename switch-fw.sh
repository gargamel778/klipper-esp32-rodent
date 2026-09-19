#!/bin/zsh
# Flash one of the stashed firmware variants to the Rodent and point the stress
# configs at the matching baud.
#
#   ./switch-fw.sh baud250000 [port]
#   ./switch-fw.sh baud115200 [port]
#
# Only the serial baud differs between them (both are SR@40MHz, edge-optimised).
setopt PIPE_FAIL 2>/dev/null || set -o pipefail

VAR="${1:?usage: switch-fw.sh <baud250000|baud115200> [port]}"
PORT="${2:-/dev/cu.wchusbserial140}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ESPTOOL="$HERE/esptool-venv/bin/esptool"
D="$HERE/fw-variants/$VAR"
# Board MAC. Export RODENT_MAC for your own board:
#   RODENT_MAC=aa:bb:cc:dd:ee:ff ./$(basename "$0")
RODENT_MAC="${RODENT_MAC:?set RODENT_MAC to the board MAC address}"
BAUD_VAL="${VAR#baud}"

[ -d "$D" ] || { echo "no such variant: $VAR"; exit 1; }

MAC=$("$ESPTOOL" --port "$PORT" --baud 115200 chip-id 2>/dev/null | awk '/^MAC:/{print $2; exit}')
[ "$MAC" = "$RODENT_MAC" ] || { echo "!!! $PORT is not the Rodent (got '$MAC') - aborting"; exit 1; }
echo "target Rodent $MAC, flashing $VAR"

"$ESPTOOL" --port "$PORT" --baud 921600 --chip esp32 write-flash \
    --flash_mode dio --flash_freq 40m --flash_size detect \
    0x1000 "$D/bootloader.bin" 0x8000 "$D/partition-table.bin" \
    0x10000 "$D/klipper.bin" 2>&1 | grep -E "Wrote|Hash of data"

"$ESPTOOL" --port "$PORT" --baud 921600 verify-flash 0x10000 "$D/klipper.bin" 2>&1 \
    | grep -E "Verification"

# keep the stress configs in step with the firmware's compiled-in baud
for f in "$HERE"/stress-rodent.cfg "$HERE"/stress-directgpio.cfg "$HERE"/stress-rodent-notmc.cfg; do
    [ -f "$f" ] || continue
    sed -i '' "s|^baud: .*|baud: $BAUD_VAL|" "$f"
    sed -i '' "s|^serial: /dev/cu.wchusbserial.*|serial: $PORT|" "$f"
done
echo "configs set to baud $BAUD_VAL on $PORT"
