#!/bin/zsh
# Build fermino's Klipper ESP32 port for a classic ESP32.
# ESP_IDF_VERSION being set makes the port's Makefile skip its docker fallback
# and drive cmake/ninja directly.
set -u
cd "$(dirname "$0")/fermino-esp32" || exit 1

echo "=== sourcing ESP-IDF ==="
source ~/esp/esp-idf/export.sh >/dev/null 2>&1
echo "ESP_IDF_VERSION=${ESP_IDF_VERSION:-UNSET}"
echo "gcc: $(which xtensa-esp32-elf-gcc)"
echo "ninja: $(which ninja)"
echo

echo "=== .config sanity ==="
grep -E "MACH_ESPRESSIF_ESP32|CONFIG_MCU|CLOCK_FREQ" .config
echo

echo "=== make ==="
unset MAKEFLAGS; make 2>&1
echo "MAKE_EXIT=$?"

echo
echo "=== artifacts ==="
ls -la out/klipper.elf out/klipper.bin 2>/dev/null || echo "(no elf/bin)"
if [ -f out/klipper.elf ]; then
    xtensa-esp32-elf-size out/klipper.elf
    echo "BUILD_OK"
fi
