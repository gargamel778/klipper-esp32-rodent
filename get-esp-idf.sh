#!/bin/zsh
# Robust ESP-IDF v5.5.1 fetch. The one-shot --recursive clone died on a network
# timeout; this splits it into the main repo plus per-submodule fetches, each retried,
# so a single flaky transfer doesn't discard the whole download.
set -u

IDF=~/esp/esp-idf
BRANCH=v5.5.1

# Be tolerant of slow/flaky transfers rather than aborting
git config --global http.postBuffer 524288000
git config --global http.lowSpeedLimit 1000
git config --global http.lowSpeedTime 300

retry() {  # retry <attempts> <description> <cmd...>
    local n=$1 desc=$2; shift 2
    for i in $(seq 1 $n); do
        echo ">>> [$i/$n] $desc"
        if "$@"; then return 0; fi
        echo "!!! attempt $i failed; sleeping 5s"
        sleep 5
    done
    echo "XXX GAVE UP: $desc"
    return 1
}

mkdir -p ~/esp

# 1. Main repo, shallow, WITHOUT submodules (resumable on its own)
if [ ! -d "$IDF/.git" ]; then
    retry 5 "clone esp-idf $BRANCH (no submodules)" \
        git clone --depth 1 -b "$BRANCH" https://github.com/espressif/esp-idf.git "$IDF" || exit 1
fi
echo "=== main repo present: $(du -sh $IDF | cut -f1) ==="

cd "$IDF" || exit 1

# 2. Submodules one at a time, each retried independently.
#    Only the ones an esp32 (xtensa) build actually needs are mandatory; the rest
#    are attempted but a failure there won't block the build.
SUBS=$(git config -f .gitmodules --get-regexp '^submodule\..*\.path$' | awk '{print $2}')
FAILED=""
for s in ${(f)SUBS}; do
    if retry 3 "submodule $s" git submodule update --init --depth 1 -- "$s"; then :; else FAILED="$FAILED $s"; fi
done

echo "=== TOTAL: $(du -sh $IDF | cut -f1) ==="
if [ -n "$FAILED" ]; then
    echo "SUBMODULES_FAILED:$FAILED"
else
    echo "ALL_SUBMODULES_OK"
fi
echo "FETCH_DONE"
