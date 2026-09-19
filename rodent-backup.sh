#!/bin/zsh
# Full 4MB backup of the BTT Rodent, read in chunks.
#
# Empirically on this board + CH340 + macOS 26:
#   - 460800 baud is broken outright ("serial data stream stopped"); 921600,
#     230400, 115200 and 57600 all work.
#   - Reads are ALSO size-limited: 256KB succeeds, 4MB fails part-way at every
#     baud tried. So read 16 x 256KB with per-chunk retries and concatenate.
#
# Every check here is on the artifact itself (size, per-chunk device verify),
# never on a pipeline's exit status.
setopt PIPE_FAIL 2>/dev/null || set -o pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ESPTOOL="$HERE/esptool-venv/bin/esptool"
PORT="${1:-/dev/cu.wchusbserial120}"
BAUD="${2:-921600}"
CHUNK=$((256 * 1024))
TOTAL=$((4 * 1024 * 1024))
NCHUNK=$((TOTAL / CHUNK))
RETRIES=12

DIR="$HERE/rodent-backup"
PARTS="$DIR/parts"
OUT="$DIR/rodent-fluidnc-full-4MB.bin"
# Board MAC. Export RODENT_MAC for your own board:
#   RODENT_MAC=aa:bb:cc:dd:ee:ff ./$(basename "$0")
RODENT_MAC="${RODENT_MAC:?set RODENT_MAC to the board MAC address}"
mkdir -p "$PARTS"

echo "=== confirming this really is the Rodent ==="
MAC=$("$ESPTOOL" --port "$PORT" --baud 115200 chip-id 2>/dev/null | awk '/^MAC:/{print $2; exit}')
echo "    MAC $MAC"
[ "$MAC" = "$RODENT_MAC" ] || { echo "!!! expected $RODENT_MAC - aborting"; exit 1; }

echo "=== reading ${NCHUNK} x 256KB at ${BAUD} ==="
for i in $(seq 0 $((NCHUNK-1))); do
    OFF=$((i * CHUNK))
    P="$PARTS/part_$(printf '%02d' $i).bin"
    if [ -f "$P" ] && [ "$(stat -f%z "$P")" -eq $CHUNK ]; then
        printf "  [%2d/%d] 0x%06x cached\n" $((i+1)) $NCHUNK $OFF
        continue
    fi
    # Cycle bauds across attempts: 460800 is excluded (broken on this CH340),
    # but 921600/230400/115200/57600 all work and a chunk that refuses one
    # rate often comes down cleanly at another.
    BAUDS=($BAUD 230400 115200 57600)
    OK=0
    for a in $(seq 1 $RETRIES); do
        B=${BAUDS[$(( (a-1) % ${#BAUDS[@]} + 1 ))]}
        rm -f "$P"
        "$ESPTOOL" --port "$PORT" --baud "$B" read-flash $OFF $CHUNK "$P" >/dev/null 2>&1
        if [ -f "$P" ] && [ "$(stat -f%z "$P" 2>/dev/null || echo 0)" -eq $CHUNK ]; then
            printf "  [%2d/%d] 0x%06x ok%s\n" $((i+1)) $NCHUNK $OFF \
                   "$([ $a -gt 1 ] && echo " (attempt $a @ ${B})")"
            OK=1; break
        fi
    done
    [ $OK -eq 1 ] || { echo "!!! chunk at 0x$(printf %06x $OFF) failed after $RETRIES attempts"; exit 1; }
done

echo "=== assembling ==="
cat "$PARTS"/part_*.bin > "$OUT"
SZ=$(stat -f%z "$OUT")
echo "    size $SZ"
[ "$SZ" -eq $TOTAL ] || { echo "!!! WRONG SIZE - unusable"; exit 1; }
MD5=$(md5 -q "$OUT")
echo "    md5  $MD5"

echo "=== verifying each 256KB region against the device ==="
FAILED=0
for i in $(seq 0 $((NCHUNK-1))); do
    OFF=$((i * CHUNK))
    P="$PARTS/part_$(printf '%02d' $i).bin"
    if "$ESPTOOL" --port "$PORT" --baud "$BAUD" verify-flash $OFF "$P" 2>&1 \
       | grep -q "Verification successful"; then
        printf "  [%2d/%d] 0x%06x verified\n" $((i+1)) $NCHUNK $OFF
    else
        printf "  [%2d/%d] 0x%06x VERIFY FAILED\n" $((i+1)) $NCHUNK $OFF
        FAILED=1
    fi
done

echo
if [ $FAILED -eq 0 ]; then
    echo "BACKUP_OK  $OUT  md5=$MD5"
else
    echo "!!! one or more regions did not verify - DO NOT TRUST THIS BACKUP"
    exit 1
fi
