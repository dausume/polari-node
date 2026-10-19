/*
 * twin_ctl.h — sim-0 (AI-Notes/plans/HARDWARE_BENCH_CONVERGENCE_PLAN.md §3, §8): the CONTROL channel of polari-avr-twin.
 * A second TCP listener (--ctl PORT), JSON lines both ways, ONE client at a time, polled from the same avr_run loop as the
 * USART listener. In (one command per line, keys in any order):
 *   {"token":"T"}                      first line, when the twin was started with a token (--ctl-token / env POLARI_TWIN_CTL_TOKEN)
 *   {"set":"PD2","level":0|1}          force a level onto a port pin NOW (the --pin-at path: the ioport pin irq)
 *   {"adc":0..5,"mv":N}                hold ADC channel N at N millivolts (channel 0 also ends a --adc0-ramp)
 *   {"watch":["PD6","PB5"]}            replace the watch list; every edge of a watched pin is sent as an event
 *   {"run":true|false}                 resume / pause stepping (a paused twin does not advance; wall pacing is shifted)
 *   {"status":1}                       one status line
 *   {"reset":1}                        avr_reset() — the power-rail reset path
 * Out: {"t":"ack","of":"set",...} · {"t":"pin","pin":"PD6","level":1,"cycle":N} · {"t":"status",...} ·
 *      {"t":"refused","why":"PX9 is not a pin of atmega328p"} (the connection stays, except a bad token, which closes it).
 * Pin events also fire for levels forced by "set" on a watched pin (the pin irq is the same one).
 * C, links libsimavr 1.6 (GPL-3.0).
 */
#ifndef POLARI_TWIN_CTL_H
#define POLARI_TWIN_CTL_H

#include <stddef.h>
#include <stdint.h>
#include <sim_avr.h>

typedef struct {
    avr_t *avr;
    const char *mcu;
    void (*status_json)(char *buf, size_t n);          /* one {"t":"status",...} line, no newline */
    void (*set_adc)(unsigned ch, uint32_t mv);         /* hold channel ch (0..5) at mv */
} ctl_host_t;

int ctl_listen(int port, const char *token);          /* 0 ok, -1 cannot listen */
void ctl_poll(const ctl_host_t *h);                    /* accept / read / answer / flush; call about every 100 us of sim time */
int ctl_paused(void);
int ctl_attached(void);
unsigned long ctl_dropped(void);                       /* event bytes dropped because the client was too slow */
void ctl_close(void);

#endif
