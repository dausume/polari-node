/*
 * twin_scenario_io.h — the sc-1 half of polari-avr-twin's scenario flags (AI-Notes/plans/FIRMWARE_SCENARIO_PLAN.md §3a):
 * what the HOST side of the wire and the POWER rail do to the board, deterministically, in cycles —
 *
 *   --inject FILE@CYCLE           host→board bytes fed from CYCLE at one byte time each (115200 Bd), only while the
 *                                 UART raises XON; several allowed; each one is a UNIT (for --drop-frame)
 *   --respond 0xPATTERN=FILE[,delay=CYC][,max=K]   a scripted host: whenever the board's TX stream ends with PATTERN
 *                                 (hex, `??` = any byte), queue FILE's bytes as one reply unit DELAY cycles later
 *   --drop-frame rx:N             the Nth host→board unit (1-based, in feed order) never reaches the board (the lost ack)
 *   --uart-ber P                  every host→board bit flips with probability P, drawn from --seed: a data bit → the byte
 *                                 XORed; the stop bit → UART_INPUT_FE (a framing error, the byte still delivered); the
 *                                 start bit → the byte is lost (the receiver never saw a start)
 *   --rx-noise RATE[,byte=0xBB]   asynchronous single bytes at RATE per second (exponential gaps, from --seed) — the
 *                                 phase-randomising traffic of the statistics tier
 *   --uart-tx-log FILE / --uart-rx-log FILE   every byte with its cycle (u64 LE cycle + u8 byte; rx: + u8 flags)
 *   --reset-at cycle=N | pc=0xADDR[,nth=K][,after=C]   avr_reset() once: simavr has NO brown-out model, so a supply
 *                                 droop mid-write is approximated by stopping the core there and resetting it
 *   --jump-at cycle=N,pc=0xADDR   a runaway: the PC is set to ADDR once (e.g. _exit = cli + a self-loop: a hang)
 *   --eeprom-set 0xADDR=HEX       preload EEPROM bytes before reset; --eeprom-dump 0xADDR:LEN read back at a reset + exit
 * Resets are counted whatever caused them (the PC reaching the reset vector), with MCUSR at that moment (WDRF = the
 * watchdog). C, links libsimavr 1.6 (GPL-3.0).
 */
#ifndef POLARI_TWIN_SCENARIO_IO_H
#define POLARI_TWIN_SCENARIO_IO_H

#include <stdint.h>
#include <sim_avr.h>

int sio_parse_arg(int argc, char **argv, int *i);   /* 1 consumed, 0 not ours, -1 bad value */
const char *sio_usage(void);
int sio_active(void);
int sio_init(avr_t *avr);
void sio_set_seed(unsigned long long seed);         /* the BER and noise draws (separate streams) */
void sio_step(avr_t *avr);
void sio_tx_byte(avr_t *avr, uint8_t b);
void sio_finish_json(avr_t *avr);                     /* prints ,"key":… fields into the final scenario line */

#endif
