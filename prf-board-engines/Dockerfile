# prf-board-engines — the board arc's engines worker (brd-1, BOARD_PROGRAMMING_PLAN.md §3/§5): the UNO toolchain,
# its flasher and its twin, from Debian 13 (trixie) packages only, on a base PINNED BY DIGEST.
#
#   gcc-avr + binutils-avr + avr-libc   GPL-3.0+ (runtime exception) / binutils GPL-3.0+ / modified BSD   the C compiler (RULE 2)
#   avrdude                             GPL-2.0                                                           the flasher (run on the USB host only)
#   simavr + libsimavr2                 GPL-3.0                                                           the AVR twin
#   polari-avr-twin (built here)        GPL-3.0 (links libsimavr)                                         simavr with USART0↔TCP, ADC0 stimulus, PORTB5 trace;
#                                                                                                         sc-0: the scenario flags (twin_forcing.c: --irq-at, --poke, VCD, stack, ISR latency)
#                                                                                                         sc-1: twin_scenario_io.c (--inject, --respond, --drop-frame, --uart-ber, --rx-noise,
#                                                                                                         --reset-at, --jump-at, EEPROM set/dump, tx/rx logs); -lm for the exponential gaps
#                                                                                                         sc-2: --align-at-pc, --flip-bit, --drop-frame tx:N / rx:p=P, 64 --irq-at;
#                                                                                                         the worker returns stdout/stderr WHOLE (was: the last 20 000 chars)
#   pyvcd 0.5.0 + polari-vcd-window     MIT (pyvcd; pure Python, no deps)                                 sc-0: the engine-side VCD reader (the "cycles around the fault" rows);
#                                                                                                         trixie has no python3-pyvcd, so the PyPI wheel is installed BY HASH in the build stage
#   gcc-riscv64-unknown-elf 14.2.0+19   GPL-3.0+ with the GCC Runtime Library Exception (upstream gcc)    hal-4 (2026-11-13, the user's go): the RISC-V C compiler,
#   + binutils-riscv64-unknown-elf      binutils GPL-3.0+                                                 multilib rv32i..rv32imafdc / rv64i..rv64imafc (Hazard3, VexRiscv,
#     2.44-3+7+b1                                                                                         the C3, rv64 later); .deb sha256 c5637d33...8295dc (trixie pool)
#   make (GPL-3.0+) so the template Makefile also runs in the image
#   python3-falcon + gunicorn           Apache-2.0 / MIT                                                  the /capability + /run worker
#   python3-websockets 15.0.1-1         BSD-3-Clause (trixie package, pure Python)                        td-0 (TWIN_DOOR_PLAN.md): twin_door.py, the WebSocket door to twin sessions;
#                                                                                                         the SAME image runs it as its own container (`python3 /srv/twin_door.py`, :9864)
#
#   docker build -t prf-board-engines:trixie .
#   docker run --rm -p 9830:9830 prf-board-engines:trixie                         # the worker
#   docker run --rm -p 9864:9864 -e TWIN_DOOR_WORKER=http://<worker>:9830 prf-board-engines:trixie python3 /srv/twin_door.py   # the twin door
#   docker run --rm -v $PWD:/w -w /w prf-board-engines:trixie avr-gcc --version   # one engine, CLI style
# Versions are printed at build time (the last RUN) and served by GET /capability.
ARG BASE=debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a

FROM ${BASE} AS twin-build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev libsimavr-dev libelf-dev python3-pip \
    && rm -rf /var/lib/apt/lists/*
# sc-0: pyvcd pinned by sha256 (pip --require-hashes refuses anything else)
RUN printf 'pyvcd==0.5.0 --hash=sha256:dec595b73328a198ccb9981c6a9e9cd09b3c9b60cd42fba12105db9c41ce894d\n' > /tmp/req.txt \
    && python3 -m pip install --no-deps --require-hashes --no-compile --target /opt/pyvcd -r /tmp/req.txt
COPY polari_avr_twin.c twin_forcing.c twin_forcing.h twin_scenario_io.c twin_scenario_io.h twin_ctl.c twin_ctl.h /src/
RUN gcc -O2 -Wall -Wextra -o /src/polari-avr-twin /src/polari_avr_twin.c /src/twin_forcing.c /src/twin_scenario_io.c /src/twin_ctl.c -I/usr/include/simavr -lsimavr -lelf -lm

FROM ${BASE}
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc-avr binutils-avr avr-libc avrdude simavr libsimavr2 make cmake python3-falcon gunicorn python3-websockets=15.0.1-1 \
        gcc-riscv64-unknown-elf=14.2.0+19 binutils-riscv64-unknown-elf=2.44-3+7+b1 \
    && rm -rf /var/lib/apt/lists/* \
    && dpkg-query -W -f '${Package} ${Version}\n' gcc-avr binutils-avr avr-libc avrdude simavr libsimavr2 python3-falcon gunicorn python3-websockets \
        gcc-riscv64-unknown-elf binutils-riscv64-unknown-elf
COPY --from=twin-build /src/polari-avr-twin /usr/local/bin/polari-avr-twin
COPY --from=twin-build /opt/pyvcd/ /usr/lib/python3/dist-packages/
COPY polari_vcd_window.py /usr/local/bin/polari-vcd-window
COPY board_engines_service.py board_sessions.py twin_door.py twin_door_selftest.py twin_hold_selftest.py /srv/
COPY cost.json /srv/cost.json
RUN useradd -m -u 1000 worker && mkdir -p /srv/sessions && chown worker /srv/sessions && avr-gcc --version >/dev/null && riscv64-unknown-elf-gcc --version >/dev/null && avr-gcc --version | head -1 && riscv64-unknown-elf-gcc --version | head -1 \
    && (polari-avr-twin 2>&1 | head -1 || true)
USER worker
WORKDIR /w
# td-0: the door, the token check and the transports block are proved IN the image (its own websockets and falcon); a failure fails the build
RUN python3 /srv/twin_door_selftest.py
# a level set on an input pin HOLDS across the firmware's port writes (the real twin, a firmware compiled here); a failure fails the build
RUN python3 /srv/twin_hold_selftest.py
# sim-0: 9832-9863 = twin sessions (a tcp + a ctl port each; 9840/9850/9860 stay with the other hw-engines workers)
# td-0: 9864 = the twin door (twin_door.py), served by its own container of this image
EXPOSE 9830 9831 9832-9863 9864
CMD ["gunicorn", "-b", "0.0.0.0:9830", "-w", "1", "-t", "600", "--chdir", "/srv", "board_engines_service:app"]
