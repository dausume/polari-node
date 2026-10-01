# prf-board-engines

The board arc's engines worker (brd-1, `AI-Notes/plans/BOARD_PROGRAMMING_PLAN.md` §3/§5): the Arduino UNO's open
toolchain, its flasher and its twin in ONE image, from Debian 13 (trixie) packages on a base pinned by digest.

| piece | version (Debian) | licence | role |
|---|---|---|---|
| `gcc-avr` / `binutils-avr` / `avr-libc` | 1:14.2.0-2 / 2.43.50.20250108-1 / 1:2.2.1-1 | GPL-3.0+ (runtime exception) / GPL-3.0+ / modified BSD | the C compiler (RULE 2: plain C, no Arduino core) |
| `avrdude` | 7.1+dfsg-3+b2 | GPL-2.0 | the flasher — only ever on the host holding the USB port |
| `simavr` + `libsimavr2` | 1.6+dfsg-3+b3 | GPL-3.0 | the AVR simulator |
| `polari-avr-twin` (built here, `polari_avr_twin.c`) | — | GPL-3.0 (links libsimavr) | simavr with USART0 ↔ TCP, the ADC0 stimulus (mV), the PORTB5 trace, `--bench`, `--state-size` |
| `make`, `python3-falcon`, `gunicorn` | trixie | GPL-3.0+ / Apache-2.0 / MIT | the template Makefile; the `/capability` + `/run` worker |

Base: `debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a`. Every tool is a
separate process the framework invokes; nothing is linked into Polari.

    docker compose -p board-engines -f ../docker-compose.board-engines.yml build     # the image (enough for a local device)
    docker compose -p board-engines -f ../docker-compose.board-engines.yml up -d     # the worker on :9830, for other devices

**Why a harness and not `simavr -u`:** simavr 1.6's `simavr` CLI has no UART-pty option (its `--help` lists `-f -m -t
-g -v -i -ff -ee -ti` only); the pty part (`uart_pty`) lives in the examples' parts library and opens the pty inside the
process's own `/dev/pts`, which a containerised twin cannot hand to the host. `polari-avr-twin` owns USART0 through
simavr's IRQs and serves it on TCP; the host side (`board.custom.twin_pty`) turns that into a pty link the Java bridge
opens with `source=serial`.

**Measured (pol-core, 2026-10-01; `cost.json` is served in `/capability`'s `resources` block):** image 534.7 MB (base
78.8 MB); `docker build --no-cache` 46 s with the base local; one UNO compile 0.11 CPU-s / 30.5 MB peak RSS; the twin
78.6 M cycles/s (4.9x real time) at 11.2 MB peak RSS. Full ledger: `polari-framework/modules/board/COST.md`.
