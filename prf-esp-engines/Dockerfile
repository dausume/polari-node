# prf-esp-engines — the ESP32-C3 engines worker (sc-3, AI-Notes/plans/FIRMWARE_SCENARIO_PLAN.md §9 sc-3; D-sc-4 RULED
# 2026-10-02: the RTOS board is the ESP32-C3): the ESP-IDF toolchain (FreeRTOS inside), esptool and Espressif's QEMU fork
# (the C3 twin) in ONE image, every input pinned.
#
#   ESP-IDF v5.5.5 (commit b774170f…)       Apache-2.0 (FreeRTOS-Kernel inside it: MIT)   the build system + the C RTOS (RULE 2: the apps are C)
#   riscv32-esp-elf esp-14.2.0_20260121      GPL-3.0 with the GCC runtime exception        the C compiler (installed by idf_tools.py, sha256 from tools.json)
#   qemu-riscv32 esp_develop_9.2.2_20260417  GPL-2.0-only (espressif/qemu COPYING)         the twin: `-machine esp32c3` (idf_tools.py, sha256 547f03e0…)
#   esptool (IDF's python env)               GPL-2.0+                                       merge_bin for the twin's flash image; write_flash (DRY-RUN here)
#   cmake 3.30.2 / ninja 1.12.1              BSD-3-Clause / Apache-2.0                      what idf.py drives (idf_tools.py, by sha256)
#   python3-falcon + gunicorn                Apache-2.0 / MIT                               the /capability + /run worker (the board engines' protocol)
#
#   polari-idf-build  (polari_idf_build.py, ours)  project.tar → idf.py build → firmware.elf, flash_image.bin (merged, 4 MB),
#                                                  app.bin, size.json (idf.py size --format json2), build.json (wall + peak RSS)
#   polari-c3-run     (polari_c3_run.py, ours)     flash_image.bin → QEMU esp32c3 for N virtual ms with UART0 = the SimRigState
#                                                  frames and UART1 = the FreeRTOS trace lines → uart0.bin, trace.log, run.json
#
# WHY NOT `FROM espressif/idf:v5.5.5`: that image installs EVERY target — 5 116 MB compressed on Docker Hub (amd64,
# measured 2026-10-02), > 12 GB unpacked, more than this device had free. Espressif's OWN Dockerfile
# (esp-idf/tools/docker/Dockerfile @ v5.5.5) is followed here with IDF_INSTALL_TARGETS=esp32c3 + a shallow clone, and the
# git history / examples / docs are dropped in a build stage. The cost rule: measured in cost.json.
#
#   docker build -t prf-esp-engines:noble .
#   docker run --rm -p 9850:9850 prf-esp-engines:noble                                   # the worker
#   docker run --rm -v $PWD:/w -w /w prf-esp-engines:noble polari-idf-build project.tar  # one engine, CLI style
ARG BASE=ubuntu:24.04@sha256:a853f94d226358a79c740cfc7bce0c289748f3fe3488d921d038ccd752c61b60
ARG IDF_TAG=v5.5.5
ARG IDF_COMMIT=b774170ff46c393eeb5e495ea37936038d3f4f4f

FROM ${BASE} AS idf
ARG IDF_TAG
ARG IDF_COMMIT
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git wget xz-utils unzip python3 python3-venv \
        libglib2.0-0 libpixman-1-0 libsdl2-2.0-0 libslirp0 libgcrypt20 \
    && rm -rf /var/lib/apt/lists/*
ENV IDF_PATH=/opt/esp/idf IDF_TOOLS_PATH=/opt/esp
RUN git clone --recursive --depth=1 --shallow-submodules -b ${IDF_TAG} https://github.com/espressif/esp-idf.git $IDF_PATH \
    && test "$(git -C $IDF_PATH rev-parse HEAD)" = "${IDF_COMMIT}" \
    && echo "${IDF_TAG}" > $IDF_PATH/version.txt
RUN ln -sf /usr/bin/python3 /usr/local/bin/python \
    && $IDF_PATH/tools/idf_tools.py --non-interactive install --targets=esp32c3 riscv32-esp-elf esp-rom-elfs qemu-riscv32 cmake ninja \
    && $IDF_PATH/tools/idf_tools.py --non-interactive install-python-env \
    && rm -rf $IDF_TOOLS_PATH/dist
# the checkout's history and what a build never reads (version.txt keeps IDF_VER without git); the toolchain keeps ONE
# multilib — the C3's rv32imc_zicsr_zifencei/ilp32 (asserted with -print-multi-directory) — and drops picolibc (ESP-IDF
# v5.5 links newlib unless CONFIG_LIBC_PICOLIBC): the other four RISC-V multilibs and picolibc are ≈ 1.4 GB of 2.05 GB
RUN find $IDF_PATH -name .git -prune -exec rm -rf {} + \
    && rm -rf $IDF_PATH/examples $IDF_PATH/docs $IDF_PATH/tools/ci/test_apps $IDF_PATH/components/*/test_apps \
    && T=$(ls -d $IDF_TOOLS_PATH/tools/riscv32-esp-elf/*/riscv32-esp-elf) \
    && test "$($T/bin/riscv32-esp-elf-gcc -march=rv32imc_zicsr_zifencei -mabi=ilp32 -print-multi-directory)" = rv32imc_zicsr_zifencei/ilp32 \
    && rm -rf $T/picolibc \
    && for d in $T/riscv32-esp-elf/lib $T/lib/gcc/riscv32-esp-elf/*; do \
         for m in rv32i_zicsr_zifencei rv32imac_zicsr_zifencei rv32imafc_zicsr_zifencei rv32imafc_zicsr_zifencei_zba_zbb_zbc_zbs; do rm -rf $d/$m; done; done \
    && du -sm $IDF_PATH $IDF_TOOLS_PATH/tools $IDF_TOOLS_PATH/tools/* $IDF_TOOLS_PATH/python_env

FROM ${BASE}
ARG DEBIAN_FRONTEND=noninteractive
# runtime only: python for idf.py/esptool, the QEMU fork's shared libraries (Espressif's list), the worker
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates python3 python3-venv python-is-python3 git make \
        libglib2.0-0 libpixman-1-0 libsdl2-2.0-0 libslirp0 libgcrypt20 libusb-1.0-0 python3-falcon gunicorn \
    && rm -rf /var/lib/apt/lists/* \
    && dpkg-query -W -f '${Package} ${Version}\n' python3 python3-falcon gunicorn
COPY --from=idf /opt/esp /opt/esp
ENV IDF_PATH=/opt/esp/idf IDF_TOOLS_PATH=/opt/esp IDF_PYTHON_CHECK_CONSTRAINTS=no IDF_CCACHE_ENABLE=0 \
    IDF_COMPONENT_MANAGER=0 IDF_TARGET=esp32c3
# the engines the worker and `docker run` name directly: esptool from IDF's python env, the QEMU fork, two binutils
# IDF's venv was made by /usr/local/bin/python in the build stage (pyvenv.cfg home) — the same path here
RUN ln -sf /usr/bin/python3 /usr/local/bin/python && git config --system --add safe.directory '*' && (userdel -r ubuntu 2>/dev/null || true) && useradd -m -u 1000 worker \
    && printf '#!/bin/sh\nexec %s "$@"\n' "$(ls /opt/esp/python_env/*/bin/esptool.py)" > /usr/local/bin/esptool.py \
    && chmod 0755 /usr/local/bin/esptool.py \
    && ln -s "$(ls /opt/esp/tools/qemu-riscv32/*/qemu/bin/qemu-system-riscv32)" /usr/local/bin/qemu-system-riscv32 \
    && for b in nm objdump; do ln -s "$(ls /opt/esp/tools/riscv32-esp-elf/*/riscv32-esp-elf/bin/riscv32-esp-elf-$b)" /usr/local/bin/riscv32-esp-elf-$b; done \
    && qemu-system-riscv32 --version | head -1 && esptool.py version | tail -1 && riscv32-esp-elf-nm --version | head -1
COPY esp_env.sh /usr/local/bin/esp-env
RUN chmod 0755 /usr/local/bin/esp-env && esp-env idf.py --version && esp-env riscv32-esp-elf-gcc --version | head -1
COPY polari_idf_build.py /usr/local/bin/polari-idf-build
COPY polari_c3_run.py /usr/local/bin/polari-c3-run
COPY esp_engines_service.py /srv/esp_engines_service.py
RUN chmod 0755 /usr/local/bin/polari-idf-build /usr/local/bin/polari-c3-run
COPY cost.json /srv/cost.json
USER worker
WORKDIR /w
EXPOSE 9850 9851
CMD ["gunicorn", "-b", "0.0.0.0:9850", "-w", "1", "-t", "1800", "--chdir", "/srv", "esp_engines_service:app"]
