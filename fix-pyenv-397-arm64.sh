#!/bin/zsh
# Rebuild pyenv 3.9.7 as native arm64 (was x86_64, built against the now-removed
# Intel Homebrew at /usr/local). Same reversible approach as the 3.11.9 fix.
#
# 3.9.7 dates from Aug 2021 and may not compile against the macOS 26 SDK. If it
# doesn't, we fall back to the newest 3.9.x -- same minor version, so every package
# stays compatible -- and say so loudly rather than silently substituting.
set -u

SCRATCH="$(cd "$(dirname "$0")" && pwd)"
VER=3.9.7
OLD="$HOME/.pyenv/versions/$VER"
BROKEN="$HOME/.pyenv/versions/${VER}-x86_64-broken"
REQ="$SCRATCH/pyenv-3.9.7-packages.txt"

echo "=== 1. Moving broken x86_64 build aside (rollback point) ==="
if [ -d "$OLD" ] && [ ! -d "$BROKEN" ]; then
    mv "$OLD" "$BROKEN"; echo "    $OLD -> $BROKEN"
else
    echo "    already moved or absent; continuing"
fi

export PKG_CONFIG_PATH="$(brew --prefix openssl@3)/lib/pkgconfig:$(brew --prefix readline)/lib/pkgconfig:$(brew --prefix zlib)/lib/pkgconfig"
export PYTHON_CONFIGURE_OPTS="--enable-shared"
export CFLAGS="-I$(brew --prefix openssl@3)/include -I$(brew --prefix readline)/include -I$(brew --prefix xz)/include -I$(brew --prefix zlib)/include"
export LDFLAGS="-L$(brew --prefix openssl@3)/lib -L$(brew --prefix readline)/lib -L$(brew --prefix xz)/lib -L$(brew --prefix zlib)/lib"

echo "=== 2. Building $VER natively (arm64) ==="
BUILT=""
if pyenv install -s "$VER" 2>&1 | tail -25; then
    BUILT="$VER"
else
    echo "!!! $VER failed to compile on this SDK - trying newest 3.9.x instead"
    FALLBACK=$(pyenv install --list 2>/dev/null | tr -d ' ' | grep -E '^3\.9\.[0-9]+$' | tail -1)
    echo "!!! fallback candidate: $FALLBACK"
    if [ -n "$FALLBACK" ] && pyenv install -s "$FALLBACK" 2>&1 | tail -25; then
        BUILT="$FALLBACK"
        echo "VERSION_SUBSTITUTED:$VER->$FALLBACK"
    fi
fi

if [ -z "$BUILT" ]; then
    echo "!!! ALL BUILDS FAILED - rolling back"
    rm -rf "$OLD"; mv "$BROKEN" "$OLD"
    echo "ROLLED_BACK"; exit 1
fi

NEWPY="$HOME/.pyenv/versions/$BUILT/bin/python3"
echo "=== 3. Verifying architecture ($BUILT) ==="
file "$NEWPY"
if ! file "$NEWPY" | grep -q arm64; then
    echo "!!! STILL NOT arm64 - rolling back"
    rm -rf "$HOME/.pyenv/versions/$BUILT" "$OLD"; mv "$BROKEN" "$OLD"
    echo "ROLLED_BACK"; exit 1
fi
"$NEWPY" -c 'import platform,ssl,lzma,sqlite3,ctypes;print("python",platform.python_version(),platform.machine());print("ssl",ssl.OPENSSL_VERSION)'

echo "=== 4. Reinstalling $(wc -l < $REQ | tr -d ' ') packages (exact pins first) ==="
"$NEWPY" -m pip install -q --upgrade pip setuptools wheel 2>&1 | tail -3
FAILED=""
if ! "$NEWPY" -m pip install -q -r "$REQ" 2>&1 | tail -15; then
    echo "--- bulk pinned install failed; retrying package-by-package ---"
    while read -r P; do
        [ -z "$P" ] && continue
        if ! "$NEWPY" -m pip install -q "$P" >/dev/null 2>&1; then
            BARE="${P%%==*}"
            if "$NEWPY" -m pip install -q "$BARE" >/dev/null 2>&1; then
                NEW=$("$NEWPY" -m pip show "$BARE" 2>/dev/null | awk '/^Version:/{print $2}')
                echo "    DRIFTED: $P -> ${BARE}==${NEW}"
            else
                echo "    FAILED:  $P"
                FAILED="$FAILED $P"
            fi
        fi
    done < "$REQ"
fi

echo "=== 5. Regenerating pyenv shims ==="
pyenv rehash

echo "=== 6. Smoke test ==="
"$NEWPY" -c 'import esphome, numpy, cryptography, serial, esptool; print("esphome+numpy+cryptography+pyserial+esptool import OK")' 2>&1 | tail -3
"$NEWPY" -m esphome version 2>&1 | tail -2

echo
echo "BUILT_VERSION=$BUILT"
[ -n "$FAILED" ] && echo "STILL_FAILED:$FAILED"
echo "DONE"
