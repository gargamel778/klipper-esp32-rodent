#!/bin/zsh
# Rebuild pyenv 3.11.9 as native arm64 (was x86_64, built against the now-removed
# Intel Homebrew at /usr/local). Reversible: old tree is moved aside, not deleted.
set -e

SCRATCH="$(cd "$(dirname "$0")" && pwd)"
VER=3.11.9
OLD="$HOME/.pyenv/versions/$VER"
BROKEN="$HOME/.pyenv/versions/${VER}-x86_64-broken"

echo "=== 1. Moving broken x86_64 build aside (rollback point) ==="
if [ -d "$OLD" ] && [ ! -d "$BROKEN" ]; then
    mv "$OLD" "$BROKEN"
    echo "    $OLD -> $BROKEN"
else
    echo "    already moved or absent; continuing"
fi

echo "=== 2. Building $VER natively (arm64) ==="
# Point the build at arm64 Homebrew deps explicitly
export PKG_CONFIG_PATH="$(brew --prefix openssl@3)/lib/pkgconfig:$(brew --prefix readline)/lib/pkgconfig:$(brew --prefix zlib)/lib/pkgconfig"
export PYTHON_CONFIGURE_OPTS="--enable-shared"
export CFLAGS="-I$(brew --prefix openssl@3)/include -I$(brew --prefix readline)/include -I$(brew --prefix xz)/include -I$(brew --prefix zlib)/include"
export LDFLAGS="-L$(brew --prefix openssl@3)/lib -L$(brew --prefix readline)/lib -L$(brew --prefix xz)/lib -L$(brew --prefix zlib)/lib"

if ! pyenv install -s "$VER"; then
    echo "!!! BUILD FAILED - rolling back"
    rm -rf "$OLD"
    mv "$BROKEN" "$OLD"
    exit 1
fi

echo "=== 3. Verifying architecture ==="
NEWPY="$HOME/.pyenv/versions/$VER/bin/python3"
file "$NEWPY"
if ! file "$NEWPY" | grep -q arm64; then
    echo "!!! STILL NOT arm64 - rolling back"
    rm -rf "$OLD"
    mv "$BROKEN" "$OLD"
    exit 1
fi
"$NEWPY" -c 'import platform,ssl,lzma,sqlite3,ctypes;print("python",platform.python_version(),platform.machine());print("ssl",ssl.OPENSSL_VERSION)'

echo "=== 4. Reinstalling the 49 captured packages ==="
"$NEWPY" -m pip install -q --upgrade pip setuptools wheel
"$NEWPY" -m pip install -q -r "$SCRATCH/pyenv-3.11.9-packages.txt"

echo "=== 5. Regenerating pyenv shims ==="
pyenv rehash

echo "=== 6. Smoke test ==="
esptool version
python3 -c 'import numpy, matplotlib, cryptography; print("numpy",numpy.__version__,"| matplotlib ok | cryptography ok")' 2>/dev/null \
  || "$NEWPY" -c 'import numpy, matplotlib, cryptography; print("numpy",numpy.__version__,"| matplotlib ok | cryptography ok")'
echo "DONE_OK"
