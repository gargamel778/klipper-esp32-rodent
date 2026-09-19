#!/bin/zsh
# Reinstall the captured 3.9.7 package set.
#
# The bulk pinned install hits a genuine conflict (esphome 2023.12.9 vs
# pyelftools==0.30), so install package-by-package: try the exact pin, and on
# failure fall back to whatever pip can resolve, recording the drift.
#
# PYTHONNOUSERSITE keeps ~/.local/lib/python3.9/site-packages (shared by any
# python3.9 on this box) out of the resolver, which was confusing it with an
# unrelated matplotlib 3.9.4.
set -u
setopt PIPE_FAIL 2>/dev/null || true

PY="$HOME/.pyenv/versions/3.9.7/bin/python3"
REQ="$(cd "$(dirname "$0")" && pwd)/pyenv-3.9.7-packages.txt"
export PYTHONNOUSERSITE=1

"$PY" -m pip install -q --upgrade pip setuptools wheel >/dev/null 2>&1

OK=0; DRIFT=(); FAIL=()
while IFS= read -r P; do
    [ -z "$P" ] && continue
    if "$PY" -m pip install -q "$P" >/dev/null 2>&1; then
        OK=$((OK+1))
    else
        BARE="${P%%==*}"
        if "$PY" -m pip install -q "$BARE" >/dev/null 2>&1; then
            NEW=$("$PY" -m pip show "$BARE" 2>/dev/null | awk '/^Version:/{print $2}')
            DRIFT+=("$P -> ${BARE}==${NEW}")
        else
            FAIL+=("$P")
        fi
    fi
done < "$REQ"

echo "=== installed at pinned version: $OK ==="
echo
echo "=== drifted (pin unsatisfiable, resolved to a compatible version): ${#DRIFT[@]} ==="
for d in "${DRIFT[@]}"; do echo "    $d"; done
echo
echo "=== failed outright: ${#FAIL[@]} ==="
for f in "${FAIL[@]}"; do echo "    $f"; done
echo
echo "=== smoke test ==="
"$PY" -c 'import esphome; print("esphome", esphome.const.__version__)' 2>&1 | tail -2
"$PY" -c 'import numpy, cryptography, serial, esptool, zeroconf, aioesphomeapi; print("numpy+cryptography+pyserial+esptool+zeroconf+aioesphomeapi OK")' 2>&1 | tail -2
"$PY" -c 'import tasmotizer' 2>&1 | tail -1
echo
pyenv rehash
echo "DONE"
