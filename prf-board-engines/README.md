# prf-board-engines

The board arc's engines worker (brd-1, `AI-Notes/plans/BOARD_PROGRAMMING_PLAN.md` §3/§5): the Arduino UNO's open
toolchain, its flasher and its twin in ONE image, from Debian 13 (trixie) packages on a base pinned by digest.

| piece | version (Debian) | licence | role |
|---|---|---|---|
| `gcc-avr` / `binutils-avr` / `avr-libc` | 1:14.2.0-2 / 2.43.50.20250108-1 / 1:2.2.1-1 | GPL-3.0+ (runtime exception) / GPL-3.0+ / modified BSD | the C compiler (RULE 2: plain C, no Arduino core) |
| `avrdude` | 7.1+dfsg-3+b2 | GPL-2.0 | the flasher — only ever on the host holding the USB port |
| `simavr` + `libsimavr2` | 1.6+dfsg-3+b3 | GPL-3.0 | the AVR simulator |
| `polari-avr-twin` (built here, `polari_avr_twin.c` + `twin_forcing.c`) | — | GPL-3.0 (links libsimavr) | simavr with USART0 ↔ TCP, the ADC0 stimulus (mV; brd-fi: ADC1..5 held via `--adc-mv CH=MV`), the PORTB5 trace, `--bench`, `--state-size`; **sc-0**: the scenario flags (below) |
| `pyvcd` 0.5.0 + `polari-vcd-window` (`polari_vcd_window.py`) | PyPI wheel, sha256 `dec595b7…894d` | MIT | sc-0: reads the twin's VCD into the "cycles around the fault" rows (trixie has no `python3-pyvcd`; the wheel is installed by hash in the build stage) |
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

**Scenario flags (sc-0, `AI-Notes/plans/FIRMWARE_SCENARIO_PLAN.md` §2a; `twin_forcing.c`):** `--irq-at pc=0x…,vec=N[,when=0xADDR/W&0xMASK=0xVAL][,shots=K]`
(raise vector N when the CPU is about to execute that PC and service it at once if SREG.I is set — the ISR returns to that PC;
with I clear it stays pending and the run records where it landed), `--irq-at cycle=N,vec=V`, `--poke 0xADDR/W=0xVAL@CYCLE`,
`--watch 0xADDR/W=name`, `--trace-vcd FILE --trace-window B:A` (our own cycle-exact VCD: simavr's writer needs IRQ signals and a
256-entry FIFO), `--sp-watch`, `--stack-fill 0xBSS_END`, `--isr-latency`, `--fn-cycles 0xSTART:0xEND`, `--uart-out FILE`, `--seed N`,
`--list-vectors`, and a final `{"t":"scenario", …}` JSON line. **Finding:** `avr->interrupts.vector[]` is in REGISTRATION order, not
indexed by vector number (index 7 holds vector 5 on the atmega328p core) — the harness always looks a vector up by its number.

**Measured (pol-core, 2026-10-01; `cost.json` is served in `/capability`'s `resources` block):** image 534.7 MB (base
78.8 MB; **534.8 MB after sc-0, +0.12 MB**); `docker build --no-cache` 46 s with the base local (**72.5 s after sc-0**: pip in the build stage); one UNO compile 0.11 CPU-s / 30.5 MB peak RSS; the twin
78.6 M cycles/s (4.9x real time) at 11.2 MB peak RSS. Full ledger: `polari-framework/modules/board/COST.md`.
