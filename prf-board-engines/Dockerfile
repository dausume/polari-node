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
#   pyvcd 0.5.0 + polari-vcd-window     MIT (pyvcd; pure Python, no deps)                                 sc-0: the engine-side VCD reader (the "cycles around the fault" rows);
#                                                                                                         trixie has no python3-pyvcd, so the PyPI wheel is installed BY HASH in the build stage
#   make (GPL-3.0+) so the template Makefile also runs in the image
#   python3-falcon + gunicorn           Apache-2.0 / MIT                                                  the /capability + /run worker
#
#   docker build -t prf-board-engines:trixie .
#   docker run --rm -p 9830:9830 prf-board-engines:trixie                         # the worker
#   docker run --rm -v $PWD:/w -w /w prf-board-engines:trixie avr-gcc --version   # one engine, CLI style
# Versions are printed at build time (the last RUN) and served by GET /capability.
ARG BASE=debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a

FROM ${BASE} AS twin-build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev libsimavr-dev libelf-dev python3-pip \
    && rm -rf /var/lib/apt/lists/*
# sc-0: pyvcd pinned by sha256 (pip --require-hashes refuses anything else)
RUN printf 'pyvcd==0.5.0 --hash=sha256:dec595b73328a198ccb9981c6a9e9cd09b3c9b60cd42fba12105db9c41ce894d\n' > /tmp/req.txt \
    && python3 -m pip install --no-deps --require-hashes --no-compile --target /opt/pyvcd -r /tmp/req.txt
COPY polari_avr_twin.c twin_forcing.c twin_forcing.h twin_scenario_io.c twin_scenario_io.h /src/
RUN gcc -O2 -Wall -Wextra -o /src/polari-avr-twin /src/polari_avr_twin.c /src/twin_forcing.c /src/twin_scenario_io.c -I/usr/include/simavr -lsimavr -lelf -lm

FROM ${BASE}
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc-avr binutils-avr avr-libc avrdude simavr libsimavr2 make python3-falcon gunicorn \
    && rm -rf /var/lib/apt/lists/* \
    && dpkg-query -W -f '${Package} ${Version}\n' gcc-avr binutils-avr avr-libc avrdude simavr libsimavr2 python3-falcon gunicorn
COPY --from=twin-build /src/polari-avr-twin /usr/local/bin/polari-avr-twin
COPY --from=twin-build /opt/pyvcd/ /usr/lib/python3/dist-packages/
COPY polari_vcd_window.py /usr/local/bin/polari-vcd-window
COPY board_engines_service.py /srv/board_engines_service.py
COPY cost.json /srv/cost.json
RUN useradd -m -u 1000 worker && avr-gcc --version | head -1 && polari-avr-twin 2>&1 | head -1 || true
USER worker
WORKDIR /w
EXPOSE 9830 9831
CMD ["gunicorn", "-b", "0.0.0.0:9830", "-w", "1", "-t", "600", "--chdir", "/srv", "board_engines_service:app"]
