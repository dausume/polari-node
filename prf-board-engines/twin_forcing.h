/*
 * twin_forcing.h — the SCENARIO half of polari-avr-twin (sc-0, AI-Notes/plans/FIRMWARE_SCENARIO_PLAN.md §2a): force ONE
 * interleaving or ONE corruption on purpose, at an exact PC or cycle, and record what the firmware did — the cycle,
 * the PC the forced interrupt landed on, a cycle-exact VCD window around it, the stack high-water and the ISR latency.
 * C, like simavr itself (GPL-3.0, links libsimavr 1.6). Every flag is deterministic: a run is a function of the
 * firmware, these flags and --seed (recorded; nothing is drawn from it yet).
 *
 * The simavr 1.6 hooks used (headers read 2026-10-02, libsimavr-dev 1.6+dfsg-3+b3):
 *   avr_raise_interrupt / avr_service_interrupts / avr->interrupts.vector[]   sim_interrupts.h  (irq-at-pc, irq-at-cycle)
 *   avr_get_interrupt_irq + AVR_INT_IRQ_PENDING / AVR_INT_IRQ_RUNNING         sim_interrupts.h  (ISR latency, landed PC)
 *   avr_core_watch_write                                                      sim_avr.h:419     (poke = corrupt-word)
 *   avr->pc, avr->cycle, avr->sreg[S_I], avr->data[R_SPL..R_SPH]             sim_avr.h         (the per-instruction step)
 * NOT used: simavr's own VCD writer (sim_vcd_file.h). Its signals must be IRQs, its log FIFO holds 256 changes and is
 * flushed by a usec-period timer — a per-INSTRUCTION PC signal would overflow it. This file writes its own VCD
 * (IEEE 1364 §18 four-state text, $timescale 1ps, one sample per instruction) inside a bounded window.
 */
#ifndef POLARI_TWIN_FORCING_H
#define POLARI_TWIN_FORCING_H

#include <stdint.h>
#include <sim_avr.h>

/* 1 = consumed argv[*i] (and maybe argv[*i+1]); 0 = not a forcing flag; -1 = a forcing flag with a bad value */
int forcing_parse_arg(int argc, char **argv, int *i);
const char *forcing_usage(void);
int forcing_active(void);
/* after avr_load_firmware: stack paint, IRQ notifies, output files */
int forcing_init(avr_t *avr);
/* after EVERY avr_run(): triggers, pokes, SP watch, function cycles, the trace ring */
void forcing_step(avr_t *avr);
/* every byte USART0 transmits (the frames the runner decodes) */
void forcing_uart_byte(uint8_t b);
/* sc-2: one byte as the HOST sees it (uart-out, the TX log, the responder) — after the --drop-frame tx: filter */
void forcing_uart_emit(uint8_t b, uint64_t cycle);
/* at exit: the VCD window and the final {"t":"scenario", ...} JSON line */
void forcing_finish(avr_t *avr, double wall_s);

#endif
