#!/bin/bash
# esp-env CMD ARGS… — run one command inside ESP-IDF's environment for the C3: the riscv32-esp-elf toolchain, cmake,
# ninja, the QEMU fork, IDF's python env and its tools/ on the PATH. This is what ESP-IDF's export.sh establishes, set
# here directly: export.sh refuses an install that carries only ONE target's tools (it demands the xtensa toolchain,
# esp32ulp-elf, openocd and gdb too — verified on v5.5.5), and this image installs the C3's alone (the cost rule).
set -e
T=/opt/esp/tools
one() { local d; d=$(ls -d $1 2>/dev/null | head -1); [ -n "$d" ] || { echo "esp-env: missing $1" >&2; exit 2; }; echo "$d"; }
export IDF_PYTHON_ENV_PATH=$(one "/opt/esp/python_env/idf*_env")
export ESP_ROM_ELF_DIR=$(one "$T/esp-rom-elfs/*")/
export PATH="$IDF_PYTHON_ENV_PATH/bin:$IDF_PATH/tools:$(one "$T/riscv32-esp-elf/*/riscv32-esp-elf/bin"):$(one "$T/cmake/*/bin"):$(one "$T/ninja/*"):$(one "$T/qemu-riscv32/*/qemu/bin"):$PATH"
exec "$@"
