# prf-esp-engines

The ESP32-C3 engines worker (sc-3, `AI-Notes/plans/FIRMWARE_SCENARIO_PLAN.md` §9 sc-3; D-sc-4 RULED 2026-10-02: the RTOS
board is the ESP32-C3). It holds the C3's open toolchain, its flasher and its twin in ONE image, with every input pinned.

| piece | version | licence | role |
|---|---|---|---|
| ESP-IDF | **v5.5.5** (tag → commit `b774170ff46c393eeb5e495ea37936038d3f4f4f`, asserted at build) | Apache-2.0 (FreeRTOS-Kernel inside: MIT) | the build system and the RTOS; the apps are C (RULE 2) |
| riscv32-esp-elf | esp-14.2.0_20260121 (sha256 `b3fce4b0…`, from ESP-IDF's tools.json) | GPL-3.0 with the GCC runtime exception | the C compiler; **one multilib kept**: `rv32imc_zicsr_zifencei/ilp32` (the C3's, asserted with `-print-multi-directory`) |
| qemu-riscv32 (Espressif fork) | **esp_develop_9.2.2_20260417** (sha256 `547f03e0…`, from tools.json) | GPL-2.0-only (espressif/qemu `COPYING`) | the twin: `-machine esp32c3` |
| esptool | 4.12.0 (ESP-IDF's python env) | GPL-2.0+ | `merge_bin` for the twin's 4 MB image; `write_flash` only on the USB host (refused here) |
| cmake / ninja | 3.30.2 / 1.12.1 (tools.json sha256) | BSD-3-Clause / Apache-2.0 | what idf.py drives |
| `polari-idf-build` (`polari_idf_build.py`) | ours | — | `project.tar` → `idf.py build` → ELF, three images, `flash_args`, the merged image, `idf.py size --format json2`, the build's cost (wait4) |
| `polari-c3-run` (`polari_c3_run.py`) | ours | — | the merged image + a params block → QEMU: UART0 → `uart0.bin` (SimRigState frames), UART1 → `trace.log` (the FreeRTOS trace lines), `run.json`; `--serve PORT` = the twin |
| `esp-env` (`esp_env.sh`) | ours | — | ESP-IDF's environment set directly (see below) |
| python3-falcon + gunicorn | noble | Apache-2.0 / MIT | the board engines' `/capability` + `/run` protocol on :9850 |

Base: `ubuntu:24.04@sha256:a853f94d226358a79c740cfc7bce0c289748f3fe3488d921d038ccd752c61b60`, which is the distribution Espressif's own
`tools/docker/Dockerfile` uses. The build follows that Dockerfile with `IDF_INSTALL_TARGETS=esp32c3` and a shallow clone.

    docker compose -p esp-engines -f ../docker-compose.esp-engines.yml build     # the image (enough for a local device)
    docker compose -p esp-engines -f ../docker-compose.esp-engines.yml up -d     # the worker on :9850, for other devices

## Why not `FROM espressif/idf:v5.5.5`

That image installs every target. Docker Hub lists it at **5 116 MB compressed** (amd64, measured 2026-10-02) and more than
12 GB unpacked, which is more than this device had free. Following Espressif's own recipe for the C3 alone gives
**1 813 MB**:
- ESP-IDF itself is 431 MB without `.git`, the examples and the docs. `version.txt` keeps `IDF_VER` working without git.
- The tools take 862 MB. riscv32-esp-elf ships five RISC-V multilibs plus picolibc, 2 050 MB in total; ESP-IDF v5.5 links newlib
  unless `CONFIG_LIBC_PICOLIBC` is set, so picolibc and the four multilibs the C3 never uses are dropped.
- The python env takes 92 MB.

## Findings while building it

- **`export.sh` refuses a single-target install.** On v5.5.5, `idf_tools.py export` demands the xtensa toolchain, esp32ulp-elf,
  openocd and gdb even when only esp32c3 tools were installed. `esp-env` therefore sets what export.sh would: IDF's python env,
  `$IDF_PATH/tools`, the toolchain, cmake, ninja and QEMU on the PATH, and `ESP_ROM_ELF_DIR`.
- **IDF's venv records the interpreter path it was made with** (`pyvenv.cfg` home = `/usr/local/bin`). The runtime stage
  recreates that symlink.
- **What the QEMU fork emulates for the C3** (read from `hw/riscv/esp32c3.c` at `esp-develop-9.2.2-20260417`):
  - UART0 and UART1 are real character devices.
  - Timer groups, SYSTIMER, the interrupt matrix and the SPI flash (`-drive if=mtd`) are emulated.
  - SHA/AES/RSA/HMAC/DS and an OpenCores Ethernet MAC are present.
  - **The USB Serial/JTAG controller is a register stub with no character device**, so the twin's host channel is UART0. On
    silicon, UART0 is GPIO21/20 through a dev board's USB-UART.
  - The docs list no RTC watchdog, no Secure Boot, and "no free-running mode: `-icount` required".
- **`-icount`:**
  - Scenario runs use `shift=3,align=off,sleep=off`. Virtual time is then a function of the instruction stream alone, so the
    same image and params give a bit-identical trace (verified).
  - With `shift=3` the twin ran virtual time about 5.6x ahead of the wall clock, even with `align=on,sleep=on`. So the twin uses
    `shift=auto,sleep=on`, which settles at 0.7–1.1x after a burst at boot (≈ 35 virtual s in the first ≈ 2 wall s).

## Measured (pol-core, 2026-10-02; `cost.json` is served in `/capability`'s `resources` block)

Image **1 813 008 516 B**. One from-scratch `idf.py build` of a C3 variant takes **57 s wall, 178 CPU-s, 193 MB peak RSS**
(the largest process). `idf.py size` takes 0.95 s and `merge_bin` 0.09 s. The builds are **reproducible**
(`CONFIG_APP_REPRODUCIBLE_BUILD=y`): forced rebuilds, the image rung and the worker rung all gave the same merged-image sha256.
One QEMU scenario run takes 0.3–0.9 s inside QEMU (≈ 1.5 s end to end through `docker run`) at 43 MB peak RSS. The wall-time
ratio is 0.533 virtual s per wall s, which is 66.6 M virtual instructions/s at `-icount 3`. A `--no-cache` image build takes 7 min 44 s with the base already local (apt 110 s, the clone 86 s, the tools and
python env 154 s, the copy 57 s) and is network-bound.
