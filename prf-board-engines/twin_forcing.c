/*
 * twin_forcing.c — the scenario flags of polari-avr-twin (sc-0). See twin_forcing.h for the hooks and why the VCD is
 * our own. Flags (all optional; any of them turns the per-instruction step on):
 *
 *   --irq-at pc=0xADDR,vec=N[,when=0xADDR/W&0xMASK=0xVAL][,shots=K]
 *        when the CPU is ABOUT TO EXECUTE the instruction at byte address ADDR (and the condition on data memory
 *        holds), raise vector N and service it at once if SREG.I is set — the ISR then runs with ADDR as its return
 *        address, i.e. BETWEEN the previous instruction and ADDR. With SREG.I clear the vector stays pending and lands
 *        wherever the core takes it (the run records landed_pc; it never assumes it). shots: how many times (default 1).
 *   --irq-at cycle=N,vec=V      raise vector V at the first instruction boundary at or after cycle N (the core services it)
 *   --poke 0xADDR[/W]=0xVAL@CYCLE   write W little-endian bytes (default 1) at the first boundary >= CYCLE (corrupt-word)
 *   --watch 0xADDR/W[=name]     one data-memory word recorded in the VCD and the final line (e.g. g_ms)
 *   --trace-vcd FILE [--trace-window B:A]   a cycle-exact VCD of B instructions before and A after the FIRST forced
 *                               event (default 64:192): pc, sp, SREG.I, the running vector, r22..r25, the watched word
 *   --sp-watch                  the lowest SP seen (exact stack high-water from SPL/SPH, per instruction)
 *   --stack-fill 0xADDR         paint 0xA5 from ADDR (the end of .bss) to RAMEND before reset; read back at exit
 *   --isr-latency               per vector: cycles from the flag raised (PENDING) to the vector taken (RUNNING)
 *   --fn-cycles 0xSTART:0xEND   cycles from reaching START to reaching END (e.g. hal_millis → its ret): min/max/n
 *   --fn-cycles 0xSTART:ret     sc-1: … to the RETURN, whichever `ret` takes it (SP rises above its value at entry), the
 *                               ret's own cycles included — for functions with several exits
 *   --uart-out FILE             every byte USART0 transmits, raw
 *   --seed N                    recorded; scenario runs draw nothing from it yet (start-phase / BER draws are sc-1/2)
 *   --list-vectors              print the interrupt table (index → vector number): it is in REGISTRATION order
 * sc-1:
 *   --watch may be given up to 8 times (the first is the VCD's watched word; all are in "watches" at exit)
 *   --isr-latency also measures each vector's ISR length (RUNNING raised → RUNNING lowered at reti): "isr_cycles"
 *   --ret-hist VEC:FILE         every time vector VEC is taken, count its return address (the main-loop PC it
 *                               interrupted) — "pc count" lines: the PHASE of that interrupt against the loop
 *   --ret-log VEC:FILE          the same, one line per time taken: "cycle return_pc first_watch_word" (which tick landed where)
 * sc-2:
 *   --align-at-pc pc=0xADDR,vec=N[,when=…]   like --irq-at pc=, but WITHOUT the extra tick: the vector is serviced at that PC
 *                               (the same PC-equality service) and the NEXT genuine raise of that vector is swallowed
 *                               (avr_clear_interrupt) — the forced one IS the genuine one, moved earlier. The final line says
 *                               by how many cycles it was advanced; if the genuine raise had merged into the forced pending
 *                               (it came within one natural period), nothing is swallowed and the event says "merged"
 *   --flip-bit 0xADDR:BIT@CYCLE  XOR one bit of one data byte at the first boundary >= CYCLE (flip-bit-at-cycle; a single
 *                               event upset), recorded with the byte before and after
 *   --irq-at may be given up to 64 times (the statistics tier's edge trains)
 *   the host-side / power-rail flags live in twin_scenario_io.c (--inject, --respond, --drop-frame, --uart-ber,
 *   --rx-noise, --reset-at, --jump-at, --eeprom-set/-dump, --uart-tx-log/-rx-log)
 * ucd-0d (UNO_CORE_DEMO_PLAN.md §5e Q7, §5g): PIN-LEVEL forcing, not vector injection — the button's edge is a LEVEL on
 * the pin, and avr_extint (wired to the ioport pin IRQs) decides whether EICRA's ISCn + EIMSK turn it into a vector:
 *   --pin-at cycle=N,pin=PD2,level=0|1   (repeatable) raise the ioport PIN irq of that port/bit at the first boundary
 *                               >= N — the SAME cycle scheduler as --irq-at/--poke, reused (a pinat_t beside irq_at_t
 *                               and poke_t, serviced in forcing_step). Prints {"t":"pin-at",...} when it fires.
 *   --wire PSRC:PDST              (repeatable) connect the OUTPUT state of PSRC to the INPUT of PDST: a notify on the
 *                               source pin's ioport IRQ (the same bidirectional IRQ a driven pin uses to tell the rest
 *                               of simavr its new level) that raises the destination pin's IRQ with that level — a real
 *                               wire, not an injected vector. Prints {"t":"wire",...} once at startup and
 *                               {"t":"wire-edge",...} per propagated edge. If the destination's own DDR bit is later
 *                               set (the firmware also drives it as an output) the wire and the firmware disagree on
 *                               who drives that pin: {"t":"wire-conflict",...} is printed (checked every forcing_step,
 *                               not a one-shot) and the run continues — the negative proof reads this line.
 */
#include "twin_forcing.h"
#include "twin_scenario_io.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <avr_ioport.h>
#include <sim_interrupts.h>
#include <sim_irq.h>

#define MAX_IRQ_AT 64
#define MAX_POKES 8
#define MAX_VEC 64
#define PAINT 0xA5u
#define MAX_WATCH 8
#define MAX_PINAT 32   /* ucd-0d: --pin-at, repeatable (a few presses is plenty; irq-at's 64 is for edge trains) */
#define MAX_WIRE 8     /* ucd-0d: --wire, repeatable */

typedef struct {
    int by_pc;                    /* 1: pc trigger; 0: cycle trigger */
    uint32_t pc;
    uint64_t cycle;
    int vec;
    int has_when;
    uint16_t when_addr;
    int when_w;
    uint32_t when_mask, when_val;
    int shots, fired;
    /* what happened (the first shot) */
    uint64_t ev_cycle;
    uint32_t ev_pc;
    int ev_raised, ev_i, ev_serviced_now;
    int landed;                   /* the ISR for this event has started */
    uint32_t landed_pc;
    uint64_t landed_cycle;
    uint32_t watch_at_event;
    /* sc-2 align-at-pc: the next genuine raise is swallowed (no extra tick) */
    int align, swallow_done;      /* swallow_done: 0 waiting, 1 swallowed, 2 merged (the genuine raise fell into the forced pending) */
    uint64_t swallow_cycle, period;
} irq_at_t;

typedef struct {
    uint16_t addr;
    int w;
    uint32_t val;
    uint64_t cycle;
    int done;
    uint64_t done_cycle;
    uint32_t done_pc;
    int flip_bit;                 /* sc-2: -1 = write the value; 0..7 = XOR this bit (flip-bit-at-cycle) */
    uint8_t before, after;
} poke_t;

typedef struct {
    uint64_t cycle;
    uint32_t pc;
    uint16_t sp;
    uint8_t i, vec, forced;
    uint8_t r[4];
    uint32_t watch;
} sample_t;

/* ucd-0d: --pin-at — a LEVEL forced onto a port pin at a cycle (the same scheduling shape as poke_t, but raising the
 * ioport pin IRQ instead of writing data memory, so avr_extint/avr_ioport's own PCINT logic sees a real pin edge) */
typedef struct {
    uint64_t cycle;
    char port;            /* 'B' | 'C' | 'D' */
    int bit;               /* 0..7 */
    int level;             /* 0 | 1 */
    struct avr_irq_t *irq;
    int done;
    uint64_t done_cycle;
} pinat_t;

/* ucd-0d: --wire PSRC:PDST — the source pin's driven-output IRQ connected to the destination pin's input IRQ */
typedef struct {
    char src_port; int src_bit;
    char dst_port; int dst_bit;
    struct avr_irq_t *src_irq, *dst_irq;
    int valid;
    int last_level;        /* -1 = not yet observed */
    unsigned long edges;
    int conflict_warned;   /* re-arms: 1 while the destination DDR bit is currently an output */
    int conflict_seen;     /* sticky, for the final summary */
} wire_t;

static irq_at_t g_irq[MAX_IRQ_AT];
static int g_nirq;
static poke_t g_poke[MAX_POKES];
static int g_npoke;
static pinat_t g_pinat[MAX_PINAT];
static int g_npinat;
static wire_t g_wire[MAX_WIRE];
static int g_nwire;
static int g_watch_on, g_watch_w;
static uint16_t g_watch_addr;
static char g_watch_name[32] = "watch";
static struct { uint16_t addr; int w; char name[32]; } g_watches[MAX_WATCH];
static int g_nwatch;
static uint64_t g_run_start[MAX_VEC], g_isr_max[MAX_VEC], g_isr_min[MAX_VEC], g_isr_n[MAX_VEC], g_isr_sum[MAX_VEC];
static int g_hist_vec;
static const char *g_hist_path;
static uint32_t *g_hist;
static int g_log_vec;
static const char *g_log_path;
static FILE *g_log_f;
static unsigned long g_log_n;
static const char *g_vcd_path, *g_uart_path;
static int g_before = 64, g_after = 192;
static int g_sp_watch, g_isr_lat;
static uint16_t g_min_sp = 0xFFFF;
static int g_fill_on;
static uint16_t g_fill_from;
static int g_fn_on;
static uint32_t g_fn_start, g_fn_end;
static uint64_t g_fn_t0, g_fn_min = UINT64_MAX, g_fn_max, g_fn_n;
static int g_fn_in, g_fn_by_sp;
static uint16_t g_fn_sp;
static unsigned long long g_seed;
static int g_active;
static FILE *g_uart_f;
static unsigned long g_uart_bytes;

/* the trace ring: B samples before the trigger, then A after */
static sample_t *g_ring;
static int g_ring_n, g_ring_head, g_ring_count;
static int g_trig = 0, g_after_left = 0, g_trace_done = 0;
static uint64_t g_trig_cycle;
static sample_t *g_out;
static int g_out_n;
static int g_forced_now;

/* ISR latency per vector */
static uint64_t g_pend_cycle[MAX_VEC];
static uint64_t g_pend_prev[MAX_VEC], g_period[MAX_VEC];   /* sc-2: the natural period of each vector (last two raises) */
static int g_pend_forced[MAX_VEC];
static uint64_t g_lat_max[MAX_VEC], g_lat_min[MAX_VEC], g_lat_n[MAX_VEC], g_lat_sum[MAX_VEC];
static avr_t *g_av;
static int g_list_vec;

static uint32_t rd(avr_t *avr, uint16_t a, int w)
{
    uint32_t v = 0;
    for (int k = 0; k < w; k++) v |= (uint32_t)avr->data[a + k] << (8 * k);
    return v;
}

static uint16_t sp_of(avr_t *avr) { return (uint16_t)(avr->data[R_SPL] | (avr->data[R_SPH] << 8)); }

static int running_vec(avr_t *avr)
{
    int p = avr->interrupts.running_ptr;
    return (p > 0 && avr->interrupts.running[p - 1]) ? avr->interrupts.running[p - 1]->vector : 0;
}

/* avr->interrupts.vector[] is in REGISTRATION order, not indexed by vector number (verified 2026-10-02 with
 * --list-vectors on the atmega328p core: index 7 holds vector 5, vector 7 sits at index 21) — always look a vector up */
static avr_int_vector_t *find_vec(avr_t *avr, int n)
{
    for (int k = 0; k < avr->interrupts.vector_count && k < MAX_VEC; k++)
        if (avr->interrupts.vector[k] && avr->interrupts.vector[k]->vector == n) return avr->interrupts.vector[k];
    return NULL;
}

/* ucd-0d: the DDR data-memory address of a port — hardcoded exactly as OCR0A_ADDR is in polari_avr_twin.c (I/O addr +
 * 0x20; DDRB/DDRC/DDRD are 0x04/0x07/0x0a in I/O space, DS40002061B §14.4): used only for the --wire conflict check
 * (did the firmware ALSO configure the destination pin as an output?), never for the pin-level forcing itself. */
static uint16_t ddr_addr(char port)
{
    switch (port) {
    case 'B': return 0x24u;
    case 'C': return 0x27u;
    case 'D': return 0x2au;
    default: return 0u;
    }
}

static struct avr_irq_t *port_irq(avr_t *avr, char port, int bit)
{
    return avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ((uint8_t)port), bit);
}

/* "PXn" (X = B|C|D, n = 0..7), exactly 3 characters, nothing after */
static int parse_pin_spec(const char *s, char *port, int *bit)
{
    if (!s[0] || !s[1] || !s[2] || s[3] || s[0] != 'P' || (s[1] != 'B' && s[1] != 'C' && s[1] != 'D') || s[2] < '0' || s[2] > '7') return -1;
    *port = s[1];
    *bit = s[2] - '0';
    return 0;
}

static int parse_pinat(const char *s, pinat_t *q)
{
    char buf[128];
    memset(q, 0, sizeof *q);
    q->level = -1;
    strncpy(buf, s, sizeof buf - 1);
    buf[sizeof buf - 1] = 0;
    for (char *tok = strtok(buf, ","); tok; tok = strtok(NULL, ",")) {
        if (!strncmp(tok, "cycle=", 6)) q->cycle = strtoull(tok + 6, NULL, 0);
        else if (!strncmp(tok, "pin=", 4)) { if (parse_pin_spec(tok + 4, &q->port, &q->bit) < 0) return -1; }
        else if (!strncmp(tok, "level=", 6)) q->level = atoi(tok + 6);
        else return -1;
    }
    return (q->port && (q->level == 0 || q->level == 1)) ? 0 : -1;
}

static int parse_wire(const char *s, wire_t *w)
{
    char buf[32];
    memset(w, 0, sizeof *w);
    w->last_level = -1;
    strncpy(buf, s, sizeof buf - 1);
    buf[sizeof buf - 1] = 0;
    char *c = strchr(buf, ':');
    if (!c) return -1;
    *c = 0;
    if (parse_pin_spec(buf, &w->src_port, &w->src_bit) < 0) return -1;
    if (parse_pin_spec(c + 1, &w->dst_port, &w->dst_bit) < 0) return -1;
    if (w->src_port == w->dst_port && w->src_bit == w->dst_bit) return -1;   /* the destination must be a DIFFERENT pin */
    return 0;
}

static int parse_kv_irq(const char *s, irq_at_t *q)
{
    char buf[256];
    memset(q, 0, sizeof *q);
    q->shots = 1;
    q->vec = -1;
    strncpy(buf, s, sizeof buf - 1);
    buf[sizeof buf - 1] = 0;
    for (char *tok = strtok(buf, ","); tok; tok = strtok(NULL, ",")) {
        if (!strncmp(tok, "pc=", 3)) { q->by_pc = 1; q->pc = (uint32_t)strtoul(tok + 3, NULL, 0); }
        else if (!strncmp(tok, "cycle=", 6)) { q->by_pc = 0; q->cycle = strtoull(tok + 6, NULL, 0); }
        else if (!strncmp(tok, "vec=", 4)) q->vec = atoi(tok + 4);
        else if (!strncmp(tok, "shots=", 6)) q->shots = atoi(tok + 6);
        else if (!strncmp(tok, "when=", 5)) {
            unsigned a, w, m, v;
            if (sscanf(tok + 5, "%i/%u&%i=%i", (int *)&a, &w, (int *)&m, (int *)&v) != 4 || w < 1 || w > 4) return -1;
            q->has_when = 1; q->when_addr = (uint16_t)a; q->when_w = (int)w; q->when_mask = m; q->when_val = v;
        }
        else return -1;
    }
    return (q->vec > 0 && q->vec < MAX_VEC && q->shots > 0) ? 0 : -1;
}

int forcing_parse_arg(int argc, char **argv, int *i)
{
    const char *a = argv[*i];
    const char *v = (*i + 1 < argc) ? argv[*i + 1] : NULL;
    if (!strcmp(a, "--irq-at") && v) {
        if (g_nirq >= MAX_IRQ_AT || parse_kv_irq(v, &g_irq[g_nirq]) < 0) return -1;
        g_nirq++; (*i)++; g_active = 1; return 1;
    }
    if (!strcmp(a, "--align-at-pc") && v) {
        if (g_nirq >= MAX_IRQ_AT || parse_kv_irq(v, &g_irq[g_nirq]) < 0 || !g_irq[g_nirq].by_pc) return -1;
        g_irq[g_nirq].align = 1;
        g_nirq++; (*i)++; g_active = 1; return 1;
    }
    if (!strcmp(a, "--flip-bit") && v) {
        poke_t *p = &g_poke[g_npoke];
        unsigned addr = 0, bit = 0;
        unsigned long long cyc = 0;
        if (g_npoke >= MAX_POKES || sscanf(v, "%i:%u@%lli", (int *)&addr, &bit, (long long *)&cyc) != 3 || bit > 7) return -1;
        p->addr = (uint16_t)addr; p->w = 1; p->val = 0; p->cycle = cyc; p->flip_bit = (int)bit;
        g_npoke++; (*i)++; g_active = 1; return 1;
    }
    if (!strcmp(a, "--poke") && v) {
        poke_t *p = &g_poke[g_npoke];
        unsigned addr = 0, w = 1, val = 0;
        unsigned long long cyc = 0;
        if (g_npoke >= MAX_POKES) return -1;
        if (sscanf(v, "%i/%u=%i@%lli", (int *)&addr, &w, (int *)&val, (long long *)&cyc) != 4) {
            w = 1;
            if (sscanf(v, "%i=%i@%lli", (int *)&addr, (int *)&val, (long long *)&cyc) != 3) return -1;
        }
        if (w < 1 || w > 4) return -1;
        p->addr = (uint16_t)addr; p->w = (int)w; p->val = val; p->cycle = cyc; p->flip_bit = -1;
        g_npoke++; (*i)++; g_active = 1; return 1;
    }
    if (!strcmp(a, "--pin-at") && v) {
        if (g_npinat >= MAX_PINAT || parse_pinat(v, &g_pinat[g_npinat]) < 0) return -1;
        g_npinat++; (*i)++; g_active = 1; return 1;
    }
    if (!strcmp(a, "--wire") && v) {
        if (g_nwire >= MAX_WIRE || parse_wire(v, &g_wire[g_nwire]) < 0) return -1;
        g_nwire++; (*i)++; g_active = 1; return 1;
    }
    if (!strcmp(a, "--watch") && v) {
        unsigned addr = 0, w = 0;
        const char *eq = strchr(v, '=');
        if (sscanf(v, "%i/%u", (int *)&addr, &w) != 2 || w < 1 || w > 4 || g_nwatch >= MAX_WATCH) return -1;
        if (!g_watch_on) {
            g_watch_on = 1; g_watch_addr = (uint16_t)addr; g_watch_w = (int)w;
            if (eq) { strncpy(g_watch_name, eq + 1, sizeof g_watch_name - 1); }
        }
        g_watches[g_nwatch].addr = (uint16_t)addr; g_watches[g_nwatch].w = (int)w;
        snprintf(g_watches[g_nwatch].name, sizeof g_watches[g_nwatch].name, "%s", eq ? eq + 1 : "watch");
        g_nwatch++;
        (*i)++; g_active = 1; return 1;
    }
    if (!strcmp(a, "--trace-vcd") && v) { g_vcd_path = v; (*i)++; g_active = 1; return 1; }
    if (!strcmp(a, "--trace-window") && v) {
        if (sscanf(v, "%d:%d", &g_before, &g_after) != 2 || g_before < 1 || g_after < 1 || g_before > 100000 || g_after > 100000) return -1;
        (*i)++; return 1;
    }
    if (!strcmp(a, "--sp-watch")) { g_sp_watch = 1; g_active = 1; return 1; }
    if (!strcmp(a, "--isr-latency")) { g_isr_lat = 1; g_active = 1; return 1; }
    if (!strcmp(a, "--stack-fill") && v) { g_fill_on = 1; g_fill_from = (uint16_t)strtoul(v, NULL, 0); (*i)++; g_active = 1; return 1; }
    if (!strcmp(a, "--fn-cycles") && v) {
        unsigned s = 0, e = 0;
        const char *c = strchr(v, ':');
        if (c && !strcmp(c + 1, "ret")) {
            s = (unsigned)strtoul(v, NULL, 0);
            g_fn_by_sp = 1;
        } else if (sscanf(v, "%i:%i", (int *)&s, (int *)&e) != 2) return -1;
        g_fn_on = 1; g_fn_start = s; g_fn_end = e; (*i)++; g_active = 1; return 1;
    }
    if (!strcmp(a, "--uart-out") && v) { g_uart_path = v; (*i)++; g_active = 1; return 1; }
    if (!strcmp(a, "--list-vectors")) { g_list_vec = 1; g_active = 1; return 1; }
    if (!strcmp(a, "--seed") && v) { g_seed = strtoull(v, NULL, 0); sio_set_seed(g_seed); (*i)++; return 1; }
    if (!strcmp(a, "--ret-hist") && v) {
        static char path[512];
        const char *c = strchr(v, ':');
        if (!c || (g_hist_vec = atoi(v)) <= 0 || g_hist_vec >= MAX_VEC) return -1;
        snprintf(path, sizeof path, "%s", c + 1);
        g_hist_path = path; g_isr_lat = 1; (*i)++; g_active = 1; return 1;
    }
    if (!strcmp(a, "--ret-log") && v) {
        static char lpath[512];
        const char *c = strchr(v, ':');
        if (!c || (g_log_vec = atoi(v)) <= 0 || g_log_vec >= MAX_VEC) return -1;
        snprintf(lpath, sizeof lpath, "%s", c + 1);
        g_log_path = lpath; g_isr_lat = 1; (*i)++; g_active = 1; return 1;
    }
    {
        int r = sio_parse_arg(argc, argv, i);
        if (r) { if (r > 0) g_active = 1; return r; }
    }
    return 0;
}

const char *forcing_usage(void)
{
    static char buf[2048];
    snprintf(buf, sizeof buf, "       scenario (sc-0): [--irq-at pc=0xADDR,vec=N[,when=0xADDR/W&0xMASK=0xVAL][,shots=K] | --irq-at cycle=N,vec=V]\n"
           "       [--poke 0xADDR[/W]=0xVAL@CYCLE] [--watch 0xADDR/W[=name]] [--trace-vcd FILE [--trace-window B:A]] [--sp-watch]\n"
           "       [--stack-fill 0xBSS_END] [--isr-latency] [--fn-cycles 0xSTART:0xEND] [--uart-out FILE] [--seed N] [--list-vectors]\n"
           "       [--ret-hist VEC:FILE] [--ret-log VEC:FILE]\n"
           "       scenario (sc-2): [--align-at-pc pc=0xADDR,vec=N[,when=...]] [--flip-bit 0xADDR:BIT@CYCLE]\n"
           "       ucd-0d: [--pin-at cycle=N,pin=PDn,level=0|1] [--wire PSRCn:PDSTn]\n%s", sio_usage());
    return buf;
}

int forcing_active(void) { return g_active; }

void forcing_uart_emit(uint8_t b, uint64_t cycle)
{
    g_uart_bytes++;
    if (g_uart_f) fputc(b, g_uart_f);
    if (g_av) sio_tx_byte_at(g_av, b, cycle);
}

void forcing_uart_byte(uint8_t b)
{
    if (g_av && sio_tx_drop_active()) { sio_tx_drop_push(g_av, b); return; }   /* sc-2: --drop-frame tx: holds and filters */
    forcing_uart_emit(b, g_av ? g_av->cycle : 0);
}

/* sc-2 align-at-pc: the genuine raise the forced one stood in for. This notify runs INSIDE avr_raise_interrupt, before
 * simavr marks the vector pending (and a raise is serviced within the same avr_run, so it is never visible pending at a
 * step boundary while I is set) — so the swallow clears the vector's ENABLE bit for this one raise (simavr then does not
 * latch it) and the next step restores the enable bit and clears the raised flag (OCF2A), as if the compare had not
 * fired. A raise more than one natural period after the forced one means the due raise had merged into the forced
 * pending (simavr drops a raise while pending, without a notify): nothing is swallowed. */
static int g_reenable_vec, g_in_forced_raise;

static void swallow_check(int v)
{
    for (int k = 0; k < g_nirq; k++) {
        irq_at_t *q = &g_irq[k];
        if (!q->align || q->vec != v || !q->landed || q->swallow_done || g_av->cycle <= q->landed_cycle) continue;
        if (q->period && g_av->cycle - q->ev_cycle >= q->period) { q->swallow_done = 2; return; }
        avr_int_vector_t *vec = find_vec(g_av, v);
        if (!vec || !vec->enable.reg) return;
        g_av->data[vec->enable.reg] &= (uint8_t)~(vec->enable.mask << vec->enable.bit);
        g_reenable_vec = v;
        q->swallow_done = 1;
        q->swallow_cycle = g_av->cycle;
        g_pend_cycle[v] = 0;
        return;
    }
}

static void on_pending(struct avr_irq_t *irq, uint32_t value, void *param)
{
    (void)irq;
    int v = (int)(intptr_t)param;
    if (value) {
        g_pend_cycle[v] = g_av->cycle;
        if (g_in_forced_raise) return;          /* the forced raise is neither a period sample nor a genuine raise */
        if (g_pend_prev[v] && g_av->cycle > g_pend_prev[v]) g_period[v] = g_av->cycle - g_pend_prev[v];
        g_pend_prev[v] = g_av->cycle;
        swallow_check(v);
    }
}

static void on_running(struct avr_irq_t *irq, uint32_t value, void *param)
{
    (void)irq;
    int v = (int)(intptr_t)param;
    if (!value) {                         /* reti: the ISR's length */
        if (g_run_start[v]) {
            uint64_t d = g_av->cycle - g_run_start[v];
            if (d > g_isr_max[v]) g_isr_max[v] = d;
            if (!g_isr_n[v] || d < g_isr_min[v]) g_isr_min[v] = d;
            g_isr_n[v]++;
            g_isr_sum[v] += d;
            g_run_start[v] = 0;
        }
        return;
    }
    g_run_start[v] = g_av->cycle;
    /* the return address the core just pushed: low byte at the old SP, high at old SP-1 (simavr _avr_push_addr) */
    uint16_t sp = sp_of(g_av);
    uint32_t ret = ((uint32_t)g_av->data[sp + 1] << 8 | g_av->data[sp + 2]) << 1;
    if (g_hist && v == g_hist_vec && (ret >> 1) < 16384u) g_hist[ret >> 1]++;
    if (g_log_f && v == g_log_vec) {
        fprintf(g_log_f, "%llu 0x%04x %u\n", (unsigned long long)g_av->cycle, ret, g_watch_on ? rd(g_av, g_watch_addr, g_watch_w) : 0u);
        g_log_n++;
    }
    for (int k = 0; k < g_nirq; k++) {
        irq_at_t *q = &g_irq[k];
        if (q->fired && !q->landed && q->vec == v) { q->landed = 1; q->landed_pc = ret; q->landed_cycle = g_av->cycle; }
    }
    if (g_pend_cycle[v]) {
        uint64_t lat = g_av->cycle - g_pend_cycle[v];
        if (!g_pend_forced[v]) {          /* the forced raise is reported on its event, not in the natural maximum */
            if (lat > g_lat_max[v]) g_lat_max[v] = lat;
            if (!g_lat_n[v] || lat < g_lat_min[v]) g_lat_min[v] = lat;
            g_lat_n[v]++;
            g_lat_sum[v] += lat;
        }
        g_pend_cycle[v] = 0;
        g_pend_forced[v] = 0;
    }
}

/* ucd-0d: the source pin's own driven-output IRQ fired (simavr's ioport pin IRQs are bidirectional: the same index
 * carries an external level IN and the port's driven level OUT) — propagate it onto the destination pin, exactly as a
 * real wire would, and say so on stdout (the proof reads this line). */
static void wire_notify(struct avr_irq_t *irq, uint32_t value, void *param)
{
    (void)irq;
    int idx = (int)(intptr_t)param;
    wire_t *w = &g_wire[idx];
    int v = value ? 1 : 0;
    if (v == w->last_level) return;
    w->last_level = v;
    w->edges++;
    avr_raise_irq(w->dst_irq, (uint32_t)v);
    printf("{\"t\":\"wire-edge\",\"src\":\"P%c%d\",\"dst\":\"P%c%d\",\"v\":%d,\"cycle\":%llu}\n",
           w->src_port, w->src_bit, w->dst_port, w->dst_bit, v, (unsigned long long)g_av->cycle);
    fflush(stdout);
}

int forcing_init(avr_t *avr)
{
    g_av = avr;
    for (int k = 0; k < g_npinat; k++)
        g_pinat[k].irq = port_irq(avr, g_pinat[k].port, g_pinat[k].bit);
    for (int k = 0; k < g_nwire; k++) {
        wire_t *w = &g_wire[k];
        w->src_irq = port_irq(avr, w->src_port, w->src_bit);
        w->dst_irq = port_irq(avr, w->dst_port, w->dst_bit);
        if (!w->src_irq || !w->dst_irq) { fprintf(stderr, "--wire P%c%d:P%c%d: no such ioport pin\n", w->src_port, w->src_bit, w->dst_port, w->dst_bit); return -1; }
        avr_irq_register_notify(w->src_irq, wire_notify, (void *)(intptr_t)k);
        w->valid = 1;
        printf("{\"t\":\"wire\",\"src\":\"P%c%d\",\"dst\":\"P%c%d\",\"applied\":1}\n", w->src_port, w->src_bit, w->dst_port, w->dst_bit);
    }
    if (g_npinat || g_nwire) fflush(stdout);
    if (g_uart_path && !(g_uart_f = fopen(g_uart_path, "wb"))) { fprintf(stderr, "cannot write %s\n", g_uart_path); return -1; }
    if (g_vcd_path) {
        g_ring_n = g_before;
        g_ring = calloc((size_t)g_ring_n, sizeof *g_ring);
        g_out = calloc((size_t)(g_before + g_after + 1), sizeof *g_out);
        if (!g_ring || !g_out) return -1;
    }
    if (g_hist_path && !(g_hist = calloc(16384u, sizeof *g_hist))) return -1;
    if (g_log_path && !(g_log_f = fopen(g_log_path, "w"))) return -1;
    if (g_fill_on) {
        if (g_fill_from < 0x100 || g_fill_from > avr->ramend) { fprintf(stderr, "--stack-fill 0x%x is outside SRAM\n", g_fill_from); return -1; }
        for (uint32_t a = g_fill_from; a <= avr->ramend; a++) avr->data[a] = PAINT;
    }
    if (g_list_vec) {
        printf("{\"t\":\"vectors\",\"vector_count\":%d,\"vector_size\":%d,\"table\":[", avr->interrupts.vector_count, avr->vector_size);
        for (int v = 0; v < MAX_VEC && v < avr->interrupts.vector_count; v++) {
            avr_int_vector_t *vec = avr->interrupts.vector[v];
            if (vec) printf("%s{\"index\":%d,\"vector\":%d}", v ? "," : "", v, vec->vector);
        }
        printf("]}\n");
    }
    if (g_isr_lat || g_nirq) {
        for (int k = 0; k < MAX_VEC && k < avr->interrupts.vector_count; k++) {
            avr_int_vector_t *vec = avr->interrupts.vector[k];
            if (!vec || !vec->vector || vec->vector >= MAX_VEC) continue;
            avr_irq_register_notify(vec->irq + AVR_INT_IRQ_PENDING, on_pending, (void *)(intptr_t)vec->vector);
            avr_irq_register_notify(vec->irq + AVR_INT_IRQ_RUNNING, on_running, (void *)(intptr_t)vec->vector);
        }
    }
    return sio_init(avr);
}

static void sample(avr_t *avr, sample_t *s)
{
    s->cycle = avr->cycle;
    s->pc = avr->pc;
    s->sp = sp_of(avr);
    s->i = avr->sreg[S_I];
    s->vec = (uint8_t)running_vec(avr);
    s->forced = (uint8_t)g_forced_now;
    for (int k = 0; k < 4; k++) s->r[k] = avr->data[22 + k];
    s->watch = g_watch_on ? rd(avr, g_watch_addr, g_watch_w) : 0;
}

static void trigger(avr_t *avr)
{
    if (!g_vcd_path || g_trig) return;
    g_trig = 1;
    g_trig_cycle = avr->cycle;
    g_after_left = g_after;
    /* the ring, oldest first, becomes the head of the window */
    int start = g_ring_count < g_ring_n ? 0 : g_ring_head;
    for (int k = 0; k < g_ring_count; k++) g_out[g_out_n++] = g_ring[(start + k) % g_ring_n];
}

void forcing_step(avr_t *avr)
{
    g_forced_now = 0;
    sio_step(avr);
    if (g_reenable_vec) {                /* sc-2 align-at-pc: the swallowed raise is over — the enable bit back, its flag cleared */
        avr_int_vector_t *vec = find_vec(avr, g_reenable_vec);
        if (vec) {
            avr->data[vec->enable.reg] |= (uint8_t)(vec->enable.mask << vec->enable.bit);
            if (vec->raised.reg) avr->data[vec->raised.reg] &= (uint8_t)~(vec->raised.mask << vec->raised.bit);
            if (avr_is_interrupt_pending(avr, vec)) avr_clear_interrupt(avr, vec);
        }
        g_reenable_vec = 0;
    }
    for (int k = 0; k < g_npoke; k++) {
        poke_t *p = &g_poke[k];
        if (p->done || avr->cycle < p->cycle) continue;
        p->before = avr->data[p->addr];
        if (p->flip_bit >= 0)
            avr_core_watch_write(avr, p->addr, (uint8_t)(avr->data[p->addr] ^ (1u << p->flip_bit)));
        else
            for (int b = 0; b < p->w; b++) avr_core_watch_write(avr, (uint16_t)(p->addr + b), (uint8_t)(p->val >> (8 * b)));
        p->after = avr->data[p->addr];
        p->done = 1; p->done_cycle = avr->cycle; p->done_pc = avr->pc;
        g_forced_now = 1;
        trigger(avr);
    }
    for (int k = 0; k < g_npinat; k++) {   /* ucd-0d: --pin-at — a level forced onto a port pin, not a vector */
        pinat_t *q = &g_pinat[k];
        if (q->done || avr->cycle < q->cycle) continue;
        avr_raise_irq(q->irq, (uint32_t)q->level);
        q->done = 1; q->done_cycle = avr->cycle;
        g_forced_now = 1;
        trigger(avr);
        printf("{\"t\":\"pin-at\",\"pin\":\"P%c%d\",\"level\":%d,\"cycle\":%llu}\n", q->port, q->bit, q->level, (unsigned long long)avr->cycle);
        fflush(stdout);
    }
    for (int k = 0; k < g_nwire; k++) {    /* ucd-0d: did the firmware ALSO drive the wire's destination pin? */
        wire_t *w = &g_wire[k];
        if (!w->valid) continue;
        uint16_t da = ddr_addr(w->dst_port);
        int as_output = da ? ((avr->data[da] >> w->dst_bit) & 1u) : 0;
        if (as_output && !w->conflict_warned) {
            w->conflict_warned = 1;
            w->conflict_seen = 1;
            printf("{\"t\":\"wire-conflict\",\"src\":\"P%c%d\",\"dst\":\"P%c%d\",\"cycle\":%llu}\n",
                   w->src_port, w->src_bit, w->dst_port, w->dst_bit, (unsigned long long)avr->cycle);
            fflush(stdout);
        } else if (!as_output) {
            w->conflict_warned = 0;   /* the firmware let go of the pin again: re-arm, a later conflict still fires */
        }
    }
    for (int k = 0; k < g_nirq; k++) {
        irq_at_t *q = &g_irq[k];
        if (q->fired >= q->shots) continue;
        if (q->by_pc ? (avr->pc != q->pc) : (avr->cycle < q->cycle)) continue;
        if (q->has_when && (rd(avr, q->when_addr, q->when_w) & q->when_mask) != q->when_val) continue;
        avr_int_vector_t *vec = find_vec(avr, q->vec);
        if (!vec) continue;
        int first = !q->fired;
        q->fired++;
        uint64_t c0 = avr->cycle;
        uint32_t pc0 = avr->pc;
        int i0 = avr->sreg[S_I];
        uint32_t w0 = g_watch_on ? rd(avr, g_watch_addr, g_watch_w) : 0;
        g_forced_now = 1;
        trigger(avr);
        sample_t s0;
        sample(avr, &s0);                    /* the boundary it fired on, before the vector is taken */
        g_in_forced_raise = 1;
        int raised = avr_raise_interrupt(avr, vec);
        g_in_forced_raise = 0;
        if (raised) g_pend_forced[q->vec] = 1;
        int now = 0;
        if (q->by_pc && raised && i0) {      /* take it HERE: the ISR returns to q->pc, the instruction not yet run */
            avr_service_interrupts(avr);
            now = (avr->pc == (uint32_t)q->vec * avr->vector_size);
        }
        /* serviced at once: keep the pre-vector boundary in the window (the regular sample below shows the vector);
         * left pending: the regular sample IS this boundary — no duplicate */
        if (now && g_vcd_path && g_after_left > 0 && g_out_n < g_before + g_after + 1) { g_out[g_out_n++] = s0; g_after_left--; }
        if (first) { q->ev_cycle = c0; q->ev_pc = pc0; q->ev_raised = raised; q->ev_i = i0; q->ev_serviced_now = now; q->watch_at_event = w0;
                     q->period = g_period[q->vec]; }
    }
    if (g_sp_watch) {
        uint16_t sp = sp_of(avr);
        if (sp < g_min_sp && sp > 0x100) g_min_sp = sp;
    }
    if (g_fn_on && g_fn_by_sp) {
        if (avr->pc == g_fn_start && !running_vec(avr) && !g_fn_in) { g_fn_t0 = avr->cycle; g_fn_in = 1; g_fn_sp = sp_of(avr); }
        else if (g_fn_in && !running_vec(avr) && sp_of(avr) > g_fn_sp) {
            uint64_t d = avr->cycle - g_fn_t0;
            if (d < g_fn_min) g_fn_min = d;
            if (d > g_fn_max) g_fn_max = d;
            g_fn_n++;
            g_fn_in = 0;
        }
    } else if (g_fn_on) {
        if (avr->pc == g_fn_start && !running_vec(avr)) { g_fn_t0 = avr->cycle; g_fn_in = 1; }
        else if (avr->pc == g_fn_end && g_fn_in && !running_vec(avr)) {
            uint64_t d = avr->cycle - g_fn_t0;
            if (d < g_fn_min) g_fn_min = d;
            if (d > g_fn_max) g_fn_max = d;
            g_fn_n++;
            g_fn_in = 0;
        }
    }
    if (g_vcd_path && !g_trace_done) {
        sample_t s;
        sample(avr, &s);
        if (!g_trig) {
            g_ring[g_ring_head] = s;
            g_ring_head = (g_ring_head + 1) % g_ring_n;
            if (g_ring_count < g_ring_n) g_ring_count++;
        } else if (g_after_left > 0) {
            g_out[g_out_n++] = s;
            if (--g_after_left == 0) g_trace_done = 1;
        }
    }
}

static void vbits(FILE *f, uint32_t v, int bits, char id)
{
    fputc('b', f);
    for (int b = bits - 1; b >= 0; b--) fputc((v >> b) & 1u ? '1' : '0', f);
    fprintf(f, " %c\n", id);
}

static int write_vcd(avr_t *avr)
{
    FILE *f = fopen(g_vcd_path, "w");
    if (!f) return -1;
    /* one AVR cycle in picoseconds; exact for any clock that divides 1e12 (16 MHz → 62500) */
    uint64_t ps = 1000000000000ull / (avr->frequency ? avr->frequency : 16000000u);
    fprintf(f, "$comment polari-avr-twin sc-0: one sample per instruction boundary; time = cycle x %llu ps; "
               "trigger cycle %llu; seed %llu $end\n", (unsigned long long)ps, (unsigned long long)g_trig_cycle, g_seed);
    fprintf(f, "$version polari-avr-twin (libsimavr 1.6) $end\n$timescale 1ps $end\n$scope module avr $end\n");
    fprintf(f, "$var wire 16 ! pc $end\n$var wire 16 \" sp $end\n$var wire 1 # sreg_i $end\n$var wire 8 $ isr_vector $end\n");
    fprintf(f, "$var wire 8 %% r22 $end\n$var wire 8 & r23 $end\n$var wire 8 ' r24 $end\n$var wire 8 ( r25 $end\n");
    fprintf(f, "$var wire %d ) %s $end\n$var wire 1 * forced $end\n", g_watch_on ? g_watch_w * 8 : 8, g_watch_name);
    fprintf(f, "$upscope $end\n$enddefinitions $end\n");
    sample_t prev;
    memset(&prev, 0xFF, sizeof prev);
    int ww = g_watch_on ? g_watch_w * 8 : 8;
    for (int k = 0; k < g_out_n; k++) {
        sample_t *s = &g_out[k];
        fprintf(f, "#%llu\n", (unsigned long long)(s->cycle * ps));
        int all = (k == 0);
        if (all || s->pc != prev.pc) vbits(f, s->pc, 16, '!');
        if (all || s->sp != prev.sp) vbits(f, s->sp, 16, '"');
        if (all || s->i != prev.i) fprintf(f, "%u#\n", s->i);
        if (all || s->vec != prev.vec) vbits(f, s->vec, 8, '$');
        for (int r = 0; r < 4; r++) if (all || s->r[r] != prev.r[r]) vbits(f, s->r[r], 8, (char)('%' + r));
        if (all || s->watch != prev.watch) vbits(f, s->watch, ww, ')');
        if (all || s->forced != prev.forced) fprintf(f, "%u*\n", s->forced);
        prev = *s;
    }
    fclose(f);
    return 0;
}

void forcing_finish(avr_t *avr, double wall_s)
{
    if (sio_tx_drop_active()) sio_tx_drop_flush(avr);
    if (g_uart_f) { fclose(g_uart_f); g_uart_f = NULL; }
    int vcd_ok = (g_vcd_path && g_trig) ? (write_vcd(avr) == 0) : 0;
    printf("{\"t\":\"scenario\",\"seed\":%llu,\"cycles\":%llu,\"wall_s\":%.4f,\"ramend\":%u,\"events\":[",
           g_seed, (unsigned long long)avr->cycle, wall_s, (unsigned)avr->ramend);
    for (int k = 0; k < g_nirq; k++) {
        irq_at_t *q = &g_irq[k];
        printf("%s{\"kind\":\"%s\",\"vec\":%d,\"target_pc\":%u,\"target_cycle\":%llu,\"fired\":%d,\"cycle\":%llu,\"pc\":%u,"
               "\"raised\":%d,\"sreg_i\":%d,\"serviced_at_pc\":%d,\"landed\":%d,\"landed_pc\":%u,\"landed_cycle\":%llu,"
               "\"latency_cycles\":%lld,\"watch_at_event\":%u,\"align\":%d,\"swallow\":\"%s\",\"swallow_cycle\":%llu,"
               "\"advanced_by_cycles\":%lld,\"period_cycles\":%llu}",
               k ? "," : "", q->align ? "align-at-pc" : q->by_pc ? "irq-at-pc" : "irq-at-cycle", q->vec, q->pc, (unsigned long long)q->cycle, q->fired,
               (unsigned long long)q->ev_cycle, q->ev_pc, q->ev_raised, q->ev_i, q->ev_serviced_now, q->landed, q->landed_pc,
               (unsigned long long)q->landed_cycle, q->landed ? (long long)(q->landed_cycle - q->ev_cycle) : -1LL, q->watch_at_event, q->align,
               !q->align ? "" : q->swallow_done == 1 ? "swallowed" : q->swallow_done == 2 ? "merged" : "pending-never-came",
               (unsigned long long)q->swallow_cycle, q->swallow_done == 1 ? (long long)(q->swallow_cycle - q->ev_cycle) : -1LL,
               (unsigned long long)q->period);
    }
    printf("],\"pokes\":[");
    for (int k = 0; k < g_npoke; k++) {
        poke_t *p = &g_poke[k];
        printf("%s{\"addr\":%u,\"w\":%d,\"val\":%u,\"flip_bit\":%d,\"at_cycle\":%llu,\"done\":%d,\"cycle\":%llu,\"pc\":%u,\"before\":%u,\"after\":%u}",
               k ? "," : "", p->addr, p->w, p->val, p->flip_bit, (unsigned long long)p->cycle, p->done, (unsigned long long)p->done_cycle, p->done_pc,
               p->before, p->after);
    }
    printf("]");
    if (g_npinat) {
        printf(",\"pin_at\":[");
        for (int k = 0; k < g_npinat; k++) {
            pinat_t *q = &g_pinat[k];
            printf("%s{\"pin\":\"P%c%d\",\"level\":%d,\"target_cycle\":%llu,\"fired\":%d,\"cycle\":%llu}",
                   k ? "," : "", q->port, q->bit, q->level, (unsigned long long)q->cycle, q->done, (unsigned long long)q->done_cycle);
        }
        printf("]");
    }
    if (g_nwire) {
        printf(",\"wire\":[");
        for (int k = 0; k < g_nwire; k++) {
            wire_t *w = &g_wire[k];
            printf("%s{\"src\":\"P%c%d\",\"dst\":\"P%c%d\",\"edges\":%lu,\"conflict\":%s}",
                   k ? "," : "", w->src_port, w->src_bit, w->dst_port, w->dst_bit, w->edges, w->conflict_seen ? "true" : "false");
        }
        printf("]");
    }
    if (g_sp_watch) printf(",\"min_sp\":%u,\"stack_high_water_sp\":%u", g_min_sp, g_min_sp <= avr->ramend ? avr->ramend - g_min_sp : 0);
    if (g_fill_on) {
        uint32_t low = avr->ramend + 1u;
        for (uint32_t a = g_fill_from; a <= avr->ramend; a++) if (avr->data[a] != PAINT) { low = a; break; }
        printf(",\"stack_fill\":{\"from\":%u,\"lowest_touched\":%u,\"high_water\":%u,\"headroom\":%u}", g_fill_from, low,
               avr->ramend + 1u - low, low - g_fill_from);
    }
    if (g_isr_lat) {
        printf(",\"isr_latency\":{");
        int first = 1;
        for (int v = 1; v < MAX_VEC; v++) {
            if (!g_lat_n[v]) continue;
            printf("%s\"%d\":{\"n\":%llu,\"min\":%llu,\"max\":%llu,\"mean\":%.3f}", first ? "" : ",", v, (unsigned long long)g_lat_n[v],
                   (unsigned long long)g_lat_min[v], (unsigned long long)g_lat_max[v], (double)g_lat_sum[v] / (double)g_lat_n[v]);
            first = 0;
        }
        printf("},\"isr_cycles\":{");
        first = 1;
        for (int v = 1; v < MAX_VEC; v++) {
            if (!g_isr_n[v]) continue;
            printf("%s\"%d\":{\"n\":%llu,\"min\":%llu,\"max\":%llu,\"mean\":%.3f}", first ? "" : ",", v, (unsigned long long)g_isr_n[v],
                   (unsigned long long)g_isr_min[v], (unsigned long long)g_isr_max[v], (double)g_isr_sum[v] / (double)g_isr_n[v]);
            first = 0;
        }
        printf("},\"isr_latency_note\":\"cycles from the vector's flag raised (AVR_INT_IRQ_PENDING) to the vector taken "
               "(AVR_INT_IRQ_RUNNING); the forced raise is excluded and reported on its event\"");
    }
    if (g_fn_on)
        printf(",\"fn_cycles\":{\"start\":%u,\"end\":%u,\"to\":\"%s\",\"n\":%llu,\"min\":%llu,\"max\":%llu}", g_fn_start, g_fn_end,
               g_fn_by_sp ? "return (SP above entry; ret included)" : "end pc", (unsigned long long)g_fn_n,
               g_fn_n ? (unsigned long long)g_fn_min : 0ull, (unsigned long long)g_fn_max);
    if (g_watch_on) printf(",\"watch\":{\"name\":\"%s\",\"addr\":%u,\"w\":%d,\"final\":%u}", g_watch_name, g_watch_addr, g_watch_w, rd(avr, g_watch_addr, g_watch_w));
    if (g_vcd_path)
        printf(",\"trace\":{\"file\":\"%s\",\"written\":%d,\"samples\":%d,\"trigger_cycle\":%llu,\"first_cycle\":%llu,\"last_cycle\":%llu,\"window\":\"%d:%d\"}",
               g_vcd_path, vcd_ok, vcd_ok ? g_out_n : 0, (unsigned long long)g_trig_cycle, g_out_n ? (unsigned long long)g_out[0].cycle : 0ull,
               g_out_n ? (unsigned long long)g_out[g_out_n - 1].cycle : 0ull, g_before, g_after);
    if (g_uart_path) printf(",\"uart_out\":{\"file\":\"%s\",\"bytes\":%lu}", g_uart_path, g_uart_bytes);
    if (g_nwatch) {
        printf(",\"watches\":[");
        for (int k = 0; k < g_nwatch; k++)
            printf("%s{\"name\":\"%s\",\"addr\":%u,\"w\":%d,\"final\":%u}", k ? "," : "", g_watches[k].name, g_watches[k].addr, g_watches[k].w,
                   rd(avr, g_watches[k].addr, g_watches[k].w));
        printf("]");
    }
    if (g_hist) {
        FILE *f = fopen(g_hist_path, "w");
        unsigned long tot = 0, distinct = 0;
        for (uint32_t k = 0; k < 16384u; k++) if (g_hist[k]) { tot += g_hist[k]; distinct++; if (f) fprintf(f, "0x%04x %u\n", k << 1, g_hist[k]); }
        if (f) fclose(f);
        printf(",\"ret_hist\":{\"vec\":%d,\"file\":\"%s\",\"taken\":%lu,\"distinct_pcs\":%lu}", g_hist_vec, g_hist_path, tot, distinct);
    }
    if (g_log_f) {
        fclose(g_log_f);
        g_log_f = NULL;
        printf(",\"ret_log\":{\"vec\":%d,\"file\":\"%s\",\"lines\":%lu}", g_log_vec, g_log_path, g_log_n);
    }
    sio_finish_json(avr);
    printf("}\n");
    fflush(stdout);
}
