# prf-esp-engines

The ESP32-C3 engines worker (sc-3, `AI-Notes/plans/FIRMWARE_SCENARIO_PLAN.md` §9 sc-3; D-sc-4 RULED 2026-10-02: the RTOS
board is the ESP32-C3). It holds the C3's open toolchain, its flasher and its twin in ONE image, with every input pinned.

| piece | version | licence | role |
|---|---|---|---|
| ESP-IDF | **v5.5.5** (tag → commit `b774170ff46c393eeb5e495ea37936038d3f4f4f`, asserted at build) | Apache-2.0 (FreeRTOS-Kernel inside: MIT) | the build system and the RTOS; the apps are C (RULE 2) |
| riscv32-esp-elf | esp-14.2.0_20260121 (sha256 `b3fce4b0…`, from ESP-IDF's tools.json) | GPL-3.0 with the GCC runtime exception | the C compiler; **one multilib kept**: `rv32imc_zicsr_zifencei/ilp32` (the C3's, asserted with `-print-multi-directory`) |
| qemu-riscv32 (Espressif fork) | **esp_develop_9.2.2_20260417** (sha256 `547f03e0…`, from tools.json) | GPL-2.0-only (espressif/qemu `COPYING`) | the twin: `-machine esp32c3` |
| qemu-riscv32 + `polari-block` (`qemu-patches/`) | the same fork source, tag `esp-develop-9.2.2-20260417` → commit `40edccac415693c5130f91c01d84176ae6008566` (asserted at build) + `qemu-patches/0001..0007` | GPL-2.0-only; our files GPL-2.0-or-later | pt-4, the **pin model**: installed beside the prebuilt binary as `qemu-system-riscv32-polari`, used by `polari-c3-run --pins` only |
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

## The pin model (pt-4, `AI-Notes/plans/PIN_TWIN_PLAN.md`)

The prebuilt fork's `esp32c3.gpio` is a register stub (below). `qemu-patches/` adds one device, `polari-block`, to the same source: it takes a
**device layout** exported from Polari's own chip rows (`board.custom.sim_layout.layout('esp32c3-qemu')`: the GPIO and IO_MUX registers, their
resets, each field's access word and the roles of each pad) and answers those registers itself, above the vendor model; every other address
falls through unchanged. With it:

- `polari-c3-run IMAGE --pins layout.json` runs `qemu-system-riscv32-polari -machine esp32c3,polari-layout=…`. Out: `pins.log`, the **pin
  channel** (`@<virtual ns> <pad> 0|1|z`, one line per pad level change, the reset levels at `@0`), and `pb-dump.txt` (final registers, pad
  levels, edge and interrupt counts, the wires).
- `--pins-in FILE`: what the board does to the pads, stamped in virtual time (`@<ns> <pad> 0|1|z`; a button press to ground = `0`, its release
  = `z`, and the firmware's pull-up takes the pad back). A stamped level lands on the same instruction every run, so a run repeats byte for byte.
- `--wires A-B,…`: pads joined on the board (the light's LED-to-sense jumper). A wired pad sees the channel's level on either end, else the
  other end's driver, else the pulls of both ends. One wire per pad; a node of three pads is refused.
- `--serve PORT --pins layout.json`: the pin channel is a TCP server on `PORT + 1` (`--pins-port` to choose): it sends the level lines and takes
  `<pad> 0|1|z` lines, applied now. A client is sent every pad's current level when it connects (one client at a time); a pad driven from
  outside stays driven after the client closes, until it is let go with `z`. `pol board twin c3 up --pins` starts this, and
  `pol board twin c3 pins` / `pin <pad> 0|1|z` are the host end (`board.custom.twin_c3_pins`).
- Without `--pins` nothing changes: the prebuilt binary, the same argv, the same outputs.
- What it is not: voltages, pad driver timing, analog, a peripheral's signal on a pad (UART / LEDC / SPI outputs on a pad are logged as not
  modelled and the pad is not driven by this device). An image without the binary refuses `--pins` in words; it never falls back to the stub.
- **State (2026-11-23):** the `qemu` stage was built from this Dockerfile on 2026-11-22 (the user's word; it downloads the apt
  packages and the espressif/qemu source). That image is the `esp-engines` store app of release 93, installed on econ.

## Host-driven runs (smp S5, the light Mission on the C3 twin)

`polari-c3-run` also takes `--rx-plan FILE` and `--boots N` (the other options are unchanged, so the sc-3 scenario runs are byte-for-byte as before):
- `--rx-plan`: UART0 becomes a TCP socket the engine owns (QEMU waits for it before it starts) and the plan's files are written to UART0 RX, each once `count` trace lines starting with
  `after` (the firmware's own `@TEL n`, one per telemetry frame) have appeared since the previous step. The pace is the firmware's VIRTUAL time: under `-icount ...,sleep=off` idle time
  warps (a 1.15 s run takes about 0.6-0.9 s of wall time), so a wall-clock pace would race the guest.
- `--boots N`: N boots on the same flash copy (a power cycle each; NVS survives). `trace.log` carries `@POWER-CYCLE n` between them.
- What the machine does NOT model: `esp32c3.gpio` is a register stub (measured 2026-10-19 with `gpio_set_level`/`gpio_get_level` under `-monitor`: `GPIO_OUT` stays 0, `GPIO_IN`
  never moves, `qom-tree` shows no pin lines, one `sysbus-irq`). A button or an LED level cannot be reached from outside, so on the PREBUILT binary the light firmware keeps its pins in a RAM shim on the twin (the pin model above removes that need)
  (`board/custom/firmware/esp32c3/apps/light_hal.h`, `PIN_SHIM=1`). That is a twin result, not a chip result.

## Twin sessions (c3g, `AI-Notes/plans/C3_SESSION_PLAN.md`)

A session is a C3 twin that stays up and takes presses: the Sim bench's button on a C3 pad. The worker holds them with the
same sessions module as the board worker (`prf-board-engines/board_sessions.py`, copied in at build time from a named build
context, `SESSIONS_KIND=c3`), so the backend talks to both workers the same way (`POST /sessions`, `GET /sessions`, stop).

- `polari-c3-twin` (`polari_c3_twin.py`) runs one session: QEMU with the pin model, the session's image, the chip's pin layout
  and the wires. It speaks the AVR twin's control line (`{"set":"GPIO3","level":0|1|"z"}`, `{"watch":[…]}`, `{"status":1}`), so
  one control hub in the backend serves both chips.
- One session holds about 64 MiB (measured once, `cost.json` `session`); at most eight run at once.
- `test_c3_twin.py` and `test_c3_sessions.py` run in the image with the network off.
- **State (2026-11-23): source-tested only.** The installed `esp-engines` (release 93) has no sessions yet. They need the image
  rebuilt from this Dockerfile (it downloads again) and a door for the sessions' lines between devices: both the user's word
  (plan §4, D-c3g-1 and D-c3g-2).

## As an isle store app

The worker reaches other devices as the `esp-engines` store app (`Isle-Mesh/isle-cli/releases/esp-engines/`): `isle store
build` makes the app from this image, `isle release build --store-app <device>:<folder>` puts it into a release, and the
release run installs it on that device (econ, since release 93). The app's definition holds its limits and its health path
(`/capability`). A long build answers through the device's agent because the worker sends a space every 20 s until the
answer is ready (`WORKER_KEEPALIVE_S`, `test_keepalive.py`).

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
