/*
 * twin_scenario_io.c — sc-1: the host side of the wire and the power rail, forced in cycles (see twin_scenario_io.h).
 * Everything here is a function of the flags and --seed: no wall clock, no host timing.
 */
#include "twin_scenario_io.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include <sim_irq.h>
#include <sim_io.h>
#include <avr_uart.h>
#include <avr_eeprom.h>

#define MAX_UNITS 4096
#define MAX_RESP 4
#define MAX_PAT 16
#define MAX_RESETS 32
#define MCUSR_ADDR 0x54u      /* ATmega328P: MCUSR = I/O 0x34 + 0x20; WDRF = bit 3, EXTRF 1, PORF 0 */

typedef struct {
    uint8_t *b;
    size_t len, pos;
    uint64_t due, started;
    int kind;                 /* 0 inject, 1 reply */
    int idx;                  /* 1-based feed order, assigned when it starts */
    int dropped;
} unit_t;

typedef struct {
    uint8_t pat[MAX_PAT], mask[MAX_PAT];
    int plen;
    uint8_t *reply;
    size_t rlen;
    uint64_t delay;
    int max, matched, queued;
} resp_t;

static int g_on;
static avr_t *g_av;
static avr_irq_t *g_uart_in;
static int g_xon = 1;
static uint64_t g_byte_cycles = 1388;   /* 10 bits at 115200 Bd, 16 MHz (recomputed in init) */
static uint64_t g_next_feed;

static unit_t *g_units[MAX_UNITS];
static int g_nunits, g_head;            /* g_head: first unit not finished */
static int g_started;
static int g_drop[8], g_ndrop;
static int g_dropped_idx[8], g_ndropped;

static resp_t g_resp[MAX_RESP];
static int g_nresp;
static uint8_t g_txring[MAX_PAT];
static int g_txn;

static double g_ber;
static uint64_t g_rng;
static unsigned long g_bits_flipped, g_bytes_corrupted, g_fe, g_lost, g_bytes_fed, g_unit_bytes;

static double g_noise_rate;
static uint8_t g_noise_byte;
static uint64_t g_rng_noise, g_next_noise;
static unsigned long g_noise_n;

static FILE *g_txlog, *g_rxlog;
static const char *g_txlog_path, *g_rxlog_path;

static int g_reset_on, g_reset_by_pc, g_reset_nth = 1, g_reset_hits, g_reset_done;
static uint32_t g_reset_pc;
static uint64_t g_reset_cycle, g_reset_after, g_reset_done_cycle;
static uint32_t g_reset_done_pc;

static int g_jump_on, g_jump_done;
static uint64_t g_jump_cycle, g_jump_done_cycle;
static uint32_t g_jump_pc, g_jump_from_pc;

static struct { uint16_t addr; uint8_t b[64]; int n; } g_eset[4];
static int g_neset;
static int g_edump_on;
static uint16_t g_edump_addr, g_edump_len;
static uint8_t g_edump_at_reset[64];
static int g_edump_at_reset_ok;

static struct { uint64_t cycle; uint8_t mcusr; uint32_t last_pc; int forced; } g_resets[MAX_RESETS];
static int g_nresets, g_reset_pending, g_in_forced_reset;
static uint32_t g_last_pc;
static void (*g_prev_reset)(struct avr_t *);

/* avr->reset is called by avr_reset() — every reset, whoever asked (the watchdog, --reset-at). MCUSR is read at the next
 * instruction boundary: the watchdog restores WDRF after avr_reset() returns. */
static void on_reset(struct avr_t *avr)
{
    if (g_prev_reset) g_prev_reset(avr);
    if (g_nresets < MAX_RESETS) {
        g_resets[g_nresets].cycle = avr->cycle;
        g_resets[g_nresets].last_pc = g_last_pc;
        g_resets[g_nresets].forced = g_in_forced_reset;
        g_nresets++;
        g_reset_pending = 1;
    }
}

static uint64_t splitmix(uint64_t *s)
{
    uint64_t z = (*s += 0x9E3779B97F4A7C15ull);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
    return z ^ (z >> 31);
}

static double urand(uint64_t *s) { return (double)(splitmix(s) >> 11) * (1.0 / 9007199254740992.0); }

static uint8_t *slurp(const char *path, size_t *n)
{
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *b = malloc(sz > 0 ? (size_t)sz : 1u);
    if (b && sz > 0 && fread(b, 1, (size_t)sz, f) != (size_t)sz) { free(b); b = NULL; }
    fclose(f);
    *n = sz > 0 ? (size_t)sz : 0u;
    return b;
}

static int add_unit(uint8_t *b, size_t len, uint64_t due, int kind)
{
    if (g_nunits >= MAX_UNITS) return -1;
    unit_t *u = calloc(1, sizeof *u);
    if (!u) return -1;
    u->b = b; u->len = len; u->due = due; u->kind = kind;
    /* keep the not-yet-started tail sorted by due (stable) */
    int k = g_nunits;
    while (k > g_head && g_units[k - 1]->due > due && g_units[k - 1]->pos == 0) { g_units[k] = g_units[k - 1]; k--; }
    g_units[k] = u;
    g_nunits++;
    return 0;
}

static int hexpat(const char *s, resp_t *r)
{
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) s += 2;
    r->plen = 0;
    while (s[0] && s[1] && r->plen < MAX_PAT) {
        if (s[0] == '?' && s[1] == '?') { r->pat[r->plen] = 0; r->mask[r->plen] = 0; }
        else {
            char t[3] = { s[0], s[1], 0 };
            char *e;
            unsigned long v = strtoul(t, &e, 16);
            if (*e) return -1;
            r->pat[r->plen] = (uint8_t)v; r->mask[r->plen] = 0xFF;
        }
        r->plen++;
        s += 2;
    }
    return (r->plen > 0 && !s[0]) ? 0 : -1;
}

int sio_parse_arg(int argc, char **argv, int *i)
{
    const char *a = argv[*i];
    const char *v = (*i + 1 < argc) ? argv[*i + 1] : NULL;
    if (!v) return 0;
    if (!strcmp(a, "--inject")) {
        char path[512];
        unsigned long long cyc = 0;
        const char *at = strrchr(v, '@');
        if (!at || (size_t)(at - v) >= sizeof path) return -1;
        memcpy(path, v, (size_t)(at - v)); path[at - v] = 0;
        cyc = strtoull(at + 1, NULL, 0);
        size_t n = 0;
        uint8_t *b = slurp(path, &n);
        if (!b || add_unit(b, n, cyc, 0) < 0) return -1;
        (*i)++; g_on = 1; return 1;
    }
    if (!strcmp(a, "--respond")) {
        if (g_nresp >= MAX_RESP) return -1;
        resp_t *r = &g_resp[g_nresp];
        char buf[640], *eq, *opt;
        strncpy(buf, v, sizeof buf - 1); buf[sizeof buf - 1] = 0;
        r->max = 1 << 30;
        if (!(eq = strchr(buf, '='))) return -1;
        *eq = 0;
        if (hexpat(buf, r) < 0) return -1;
        opt = strchr(eq + 1, ',');
        if (opt) *opt++ = 0;
        if (!(r->reply = slurp(eq + 1, &r->rlen)) || !r->rlen) return -1;
        for (char *tok = opt ? strtok(opt, ",") : NULL; tok; tok = strtok(NULL, ",")) {
            if (!strncmp(tok, "delay=", 6)) r->delay = strtoull(tok + 6, NULL, 0);
            else if (!strncmp(tok, "max=", 4)) r->max = atoi(tok + 4);
            else return -1;
        }
        g_nresp++; (*i)++; g_on = 1; return 1;
    }
    if (!strcmp(a, "--drop-frame")) {
        if (strncmp(v, "rx:", 3) || g_ndrop >= 8) return -1;   /* tx: not needed by any scenario yet — refused, not ignored */
        g_drop[g_ndrop++] = atoi(v + 3);
        (*i)++; g_on = 1; return 1;
    }
    if (!strcmp(a, "--uart-ber")) { g_ber = atof(v); if (g_ber < 0 || g_ber > 0.5) return -1; (*i)++; g_on = 1; return 1; }
    if (!strcmp(a, "--rx-noise")) {
        char buf[64], *c;
        strncpy(buf, v, sizeof buf - 1); buf[sizeof buf - 1] = 0;
        g_noise_byte = 0x00;
        if ((c = strchr(buf, ','))) { *c++ = 0; if (!strncmp(c, "byte=", 5)) g_noise_byte = (uint8_t)strtoul(c + 5, NULL, 0); else return -1; }
        g_noise_rate = atof(buf);
        if (g_noise_rate <= 0) return -1;
        (*i)++; g_on = 1; return 1;
    }
    if (!strcmp(a, "--uart-tx-log")) { g_txlog_path = v; (*i)++; g_on = 1; return 1; }
    if (!strcmp(a, "--uart-rx-log")) { g_rxlog_path = v; (*i)++; g_on = 1; return 1; }
    if (!strcmp(a, "--reset-at")) {
        char buf[128];
        strncpy(buf, v, sizeof buf - 1); buf[sizeof buf - 1] = 0;
        for (char *tok = strtok(buf, ","); tok; tok = strtok(NULL, ",")) {
            if (!strncmp(tok, "cycle=", 6)) { g_reset_by_pc = 0; g_reset_cycle = strtoull(tok + 6, NULL, 0); }
            else if (!strncmp(tok, "pc=", 3)) { g_reset_by_pc = 1; g_reset_pc = (uint32_t)strtoul(tok + 3, NULL, 0); }
            else if (!strncmp(tok, "nth=", 4)) g_reset_nth = atoi(tok + 4);
            else if (!strncmp(tok, "after=", 6)) g_reset_after = strtoull(tok + 6, NULL, 0);
            else return -1;
        }
        if (g_reset_nth < 1) return -1;
        g_reset_on = 1; (*i)++; g_on = 1; return 1;
    }
    if (!strcmp(a, "--jump-at")) {
        char buf[128];
        int have_pc = 0;
        strncpy(buf, v, sizeof buf - 1); buf[sizeof buf - 1] = 0;
        for (char *tok = strtok(buf, ","); tok; tok = strtok(NULL, ",")) {
            if (!strncmp(tok, "cycle=", 6)) g_jump_cycle = strtoull(tok + 6, NULL, 0);
            else if (!strncmp(tok, "pc=", 3)) { g_jump_pc = (uint32_t)strtoul(tok + 3, NULL, 0); have_pc = 1; }
            else return -1;
        }
        if (!have_pc) return -1;
        g_jump_on = 1; (*i)++; g_on = 1; return 1;
    }
    if (!strcmp(a, "--eeprom-set")) {
        const char *eq = strchr(v, '=');
        if (!eq || g_neset >= 4) return -1;
        g_eset[g_neset].addr = (uint16_t)strtoul(v, NULL, 0);
        const char *h = eq + 1;
        int n = 0;
        while (h[0] && h[1] && n < 64) { char t[3] = { h[0], h[1], 0 }; g_eset[g_neset].b[n++] = (uint8_t)strtoul(t, NULL, 16); h += 2; }
        if (!n || h[0]) return -1;
        g_eset[g_neset].n = n; g_neset++;
        (*i)++; g_on = 1; return 1;
    }
    if (!strcmp(a, "--eeprom-dump")) {
        unsigned ad = 0, ln = 0;
        if (sscanf(v, "%i:%u", (int *)&ad, &ln) != 2 || !ln || ln > 64) return -1;
        g_edump_on = 1; g_edump_addr = (uint16_t)ad; g_edump_len = (uint16_t)ln;
        (*i)++; g_on = 1; return 1;
    }
    return 0;
}

const char *sio_usage(void)
{
    return "       scenario (sc-1): [--inject FILE@CYCLE] [--respond 0xPAT=FILE[,delay=CYC][,max=K]] [--drop-frame rx:N] [--uart-ber P]\n"
           "       [--rx-noise RATE[,byte=0xBB]] [--uart-tx-log FILE] [--uart-rx-log FILE] [--reset-at cycle=N | pc=0xADDR[,nth=K][,after=C]]\n"
           "       [--jump-at cycle=N,pc=0xADDR] [--eeprom-set 0xADDR=HEX] [--eeprom-dump 0xADDR:LEN]\n";
}

int sio_active(void) { return g_on; }

static void on_xon(struct avr_irq_t *irq, uint32_t value, void *param) { (void)irq; (void)value; (void)param; g_xon = 1; }
static void on_xoff(struct avr_irq_t *irq, uint32_t value, void *param) { (void)irq; (void)value; (void)param; g_xon = 0; }

static int eeprom_read(avr_t *avr, uint16_t addr, uint16_t len, uint8_t *out)
{
    uint8_t tmp[64];
    avr_eeprom_desc_t d = { .ee = tmp, .offset = addr, .size = len };
    /* simavr 1.6's eeprom ioctl returns -1 even when it handled the call (only -2 = a bad argument) */
    if (avr_ioctl(avr, AVR_IOCTL_EEPROM_GET, &d) == -2 || !d.ee) return -1;
    memcpy(out, d.ee, len);   /* simavr 1.6 either copies into .ee or points .ee at its own bytes — read .ee either way */
    return 0;
}

int sio_init(avr_t *avr)
{
    g_av = avr;
    if (!g_on) return 0;
    g_byte_cycles = (uint64_t)(avr->frequency ? avr->frequency : 16000000u) * 10u / 115200u;
    g_uart_in = avr_io_getirq(avr, AVR_IOCTL_UART_GETIRQ('0'), UART_IRQ_INPUT);
    avr_irq_register_notify(avr_io_getirq(avr, AVR_IOCTL_UART_GETIRQ('0'), UART_IRQ_OUT_XON), on_xon, NULL);
    avr_irq_register_notify(avr_io_getirq(avr, AVR_IOCTL_UART_GETIRQ('0'), UART_IRQ_OUT_XOFF), on_xoff, NULL);
    g_prev_reset = avr->reset;
    avr->reset = on_reset;
    if (g_txlog_path && !(g_txlog = fopen(g_txlog_path, "wb"))) return -1;
    if (g_rxlog_path && !(g_rxlog = fopen(g_rxlog_path, "wb"))) return -1;
    for (int k = 0; k < g_neset; k++) {
        avr_eeprom_desc_t d = { .ee = g_eset[k].b, .offset = g_eset[k].addr, .size = (uint32_t)g_eset[k].n };
        if (avr_ioctl(avr, AVR_IOCTL_EEPROM_SET, &d) == -2) { fprintf(stderr, "--eeprom-set refused by simavr\n"); return -1; }
    }
    return 0;
}

void sio_set_seed(unsigned long long seed)
{
    g_rng = seed ^ 0x5EED0BE12ull;
    g_rng_noise = seed ^ 0xA5A5C0FFEEull;
}

static void log_rx(uint64_t cyc, uint8_t b, uint8_t flags)
{
    if (!g_rxlog) return;
    uint8_t rec[10];
    for (int k = 0; k < 8; k++) rec[k] = (uint8_t)(cyc >> (8 * k));
    rec[8] = b; rec[9] = flags;
    fwrite(rec, 1, sizeof rec, g_rxlog);
}

/* one host→board byte through the line: BER → (data XOR | framing error | lost) → the UART */
static void deliver(avr_t *avr, uint8_t b, int is_unit)
{
    uint32_t v = b;
    uint8_t flags = 0;
    if (is_unit && g_ber > 0) {
        int lost = 0, fe = 0;
        uint8_t x = 0;
        for (int bit = 0; bit < 10; bit++) {
            if (urand(&g_rng) >= g_ber) continue;
            g_bits_flipped++;
            if (bit == 0) lost = 1;                  /* start bit: no start seen — the byte never arrives */
            else if (bit == 9) fe = 1;               /* stop bit read as 0: a framing error */
            else x ^= (uint8_t)(1u << (bit - 1));    /* data bits, LSB first on the line */
        }
        if (lost || fe || x) g_bytes_corrupted++;
        if (lost) { g_lost++; log_rx(avr->cycle, b, 0x80); return; }
        v = (uint8_t)(b ^ x);
        if (fe) { v |= UART_INPUT_FE; g_fe++; flags |= 0x01; }
        if (x) flags |= 0x02;
    }
    log_rx(avr->cycle, (uint8_t)v, flags);
    avr_raise_irq(g_uart_in, v);
    g_bytes_fed++;
}

void sio_step(avr_t *avr)
{
    if (!g_on) return;
    if (g_reset_pending) { g_resets[g_nresets - 1].mcusr = avr->data[MCUSR_ADDR]; g_reset_pending = 0; }
    g_last_pc = avr->pc;
    if (g_jump_on && !g_jump_done && avr->cycle >= g_jump_cycle) {
        g_jump_done = 1; g_jump_done_cycle = avr->cycle; g_jump_from_pc = avr->pc;
        avr->pc = g_jump_pc;
    }
    if (g_reset_on && !g_reset_done) {
        int go = 0;
        if (!g_reset_by_pc) go = avr->cycle >= g_reset_cycle;
        else if (avr->pc == g_reset_pc && avr->cycle >= g_reset_after) go = (++g_reset_hits >= g_reset_nth);
        if (go) {
            g_reset_done = 1; g_reset_done_cycle = avr->cycle; g_reset_done_pc = avr->pc;
            if (g_edump_on) g_edump_at_reset_ok = eeprom_read(avr, g_edump_addr, g_edump_len, g_edump_at_reset) == 0;
            g_in_forced_reset = 1;
            avr_reset(avr);
            g_in_forced_reset = 0;
        }
    }
    /* the host→board line: one byte per byte time, only while the UART has room (XON) */
    if (!g_xon || avr->cycle < g_next_feed) return;
    while (g_head < g_nunits) {
        unit_t *u = g_units[g_head];
        if (u->due > avr->cycle) break;
        if (u->pos == 0 && !u->idx) {
            u->idx = ++g_started;
            u->started = avr->cycle;
            for (int k = 0; k < g_ndrop; k++)
                if (g_drop[k] == u->idx) { u->dropped = 1; if (g_ndropped < 8) g_dropped_idx[g_ndropped++] = u->idx; }
        }
        if (u->dropped || u->pos >= u->len) { g_head++; continue; }
        deliver(avr, u->b[u->pos++], 1);
        g_unit_bytes++;
        if (u->pos >= u->len) g_head++;
        g_next_feed = avr->cycle + g_byte_cycles;
        return;
    }
    if (g_noise_rate > 0) {
        if (!g_next_noise) g_next_noise = avr->cycle + (uint64_t)(-log(1.0 - urand(&g_rng_noise)) / g_noise_rate * (double)avr->frequency);
        if (avr->cycle >= g_next_noise) {
            deliver(avr, g_noise_byte, 0);
            g_noise_n++;
            g_next_feed = avr->cycle + g_byte_cycles;
            g_next_noise = avr->cycle + 1 + (uint64_t)(-log(1.0 - urand(&g_rng_noise)) / g_noise_rate * (double)avr->frequency);
        }
    }
}

void sio_tx_byte(avr_t *avr, uint8_t b)
{
    if (!g_on) return;
    if (g_txlog) {
        uint8_t rec[9];
        for (int k = 0; k < 8; k++) rec[k] = (uint8_t)(avr->cycle >> (8 * k));
        rec[8] = b;
        fwrite(rec, 1, sizeof rec, g_txlog);
    }
    memmove(g_txring, g_txring + 1, MAX_PAT - 1);
    g_txring[MAX_PAT - 1] = b;
    if (g_txn < MAX_PAT) g_txn++;
    for (int k = 0; k < g_nresp; k++) {
        resp_t *r = &g_resp[k];
        if (g_txn < r->plen) continue;
        int ok = 1;
        for (int j = 0; j < r->plen && ok; j++) ok = ((g_txring[MAX_PAT - r->plen + j] & r->mask[j]) == r->pat[j]);
        if (!ok) continue;
        r->matched++;
        if (r->queued >= r->max) continue;
        uint8_t *copy = malloc(r->rlen);
        if (!copy) continue;
        memcpy(copy, r->reply, r->rlen);
        if (add_unit(copy, r->rlen, avr->cycle + r->delay, 1) == 0) r->queued++;
    }
}

static void hexout(const uint8_t *b, int n)
{
    putchar('"');
    for (int k = 0; k < n; k++) printf("%02x", b[k]);
    putchar('"');
}

void sio_finish_json(avr_t *avr)
{
    if (!g_on) return;
    if (g_txlog) { fclose(g_txlog); g_txlog = NULL; }
    if (g_rxlog) { fclose(g_rxlog); g_rxlog = NULL; }
    printf(",\"resets\":{\"n\":%d,\"list\":[", g_nresets);
    for (int k = 0; k < g_nresets; k++)
        printf("%s{\"cycle\":%llu,\"mcusr\":%u,\"wdrf\":%d,\"forced\":%d,\"last_pc\":%u}", k ? "," : "", (unsigned long long)g_resets[k].cycle,
               g_resets[k].mcusr, (g_resets[k].mcusr >> 3) & 1, g_resets[k].forced, g_resets[k].last_pc);
    printf("]}");
    if (g_reset_on)
        printf(",\"reset_at\":{\"by\":\"%s\",\"target_pc\":%u,\"nth\":%d,\"after\":%llu,\"target_cycle\":%llu,\"hits\":%d,\"done\":%d,\"cycle\":%llu,\"pc\":%u,"
               "\"note\":\"simavr 1.6 has no brown-out detector model: a droop mid-write is approximated by avr_reset() at this boundary\"}",
               g_reset_by_pc ? "pc" : "cycle", g_reset_pc, g_reset_nth, (unsigned long long)g_reset_after, (unsigned long long)g_reset_cycle,
               g_reset_hits, g_reset_done, (unsigned long long)g_reset_done_cycle, g_reset_done_pc);
    if (g_jump_on)
        printf(",\"jump_at\":{\"target_cycle\":%llu,\"to_pc\":%u,\"done\":%d,\"cycle\":%llu,\"from_pc\":%u}", (unsigned long long)g_jump_cycle,
               g_jump_pc, g_jump_done, (unsigned long long)g_jump_done_cycle, g_jump_from_pc);
    printf(",\"rx\":{\"units\":%d,\"units_started\":%d,\"unit_bytes\":%lu,\"bytes_fed\":%lu,\"dropped_units\":[", g_nunits, g_started, g_unit_bytes, g_bytes_fed);
    for (int k = 0; k < g_ndropped; k++) printf("%s%d", k ? "," : "", g_dropped_idx[k]);
    printf("],\"ber\":%.3g,\"bits_flipped\":%lu,\"bytes_corrupted\":%lu,\"framing_errors\":%lu,\"bytes_lost\":%lu,\"noise_bytes\":%lu,\"noise_rate\":%g,"
           "\"unit_starts\":[", g_ber, g_bits_flipped, g_bytes_corrupted, g_fe, g_lost, g_noise_n, g_noise_rate);
    int shown = 0;
    for (int k = 0; k < g_nunits && shown < 32; k++)
        if (g_units[k]->idx) printf("%s{\"idx\":%d,\"kind\":\"%s\",\"cycle\":%llu,\"dropped\":%d,\"len\":%zu}", shown++ ? "," : "", g_units[k]->idx,
                                    g_units[k]->kind ? "reply" : "inject", (unsigned long long)g_units[k]->started, g_units[k]->dropped, g_units[k]->len);
    printf("]}");
    if (g_nresp) {
        printf(",\"responder\":[");
        for (int k = 0; k < g_nresp; k++)
            printf("%s{\"pattern_len\":%d,\"matched\":%d,\"queued\":%d,\"delay\":%llu}", k ? "," : "", g_resp[k].plen, g_resp[k].matched, g_resp[k].queued,
                   (unsigned long long)g_resp[k].delay);
        printf("]");
    }
    if (g_edump_on) {
        uint8_t now[64];
        printf(",\"eeprom\":{\"addr\":%u,\"len\":%u,\"at_exit\":", g_edump_addr, g_edump_len);
        if (eeprom_read(avr, g_edump_addr, g_edump_len, now) == 0) hexout(now, g_edump_len); else printf("null");
        printf(",\"at_reset\":");
        if (g_edump_at_reset_ok) hexout(g_edump_at_reset, g_edump_len); else printf("null");
        printf("}");
    }
}
