/*
 * polari-avr-twin — the UNO's twin (brd-1): the SAME .hex a real UNO is flashed with, run in simavr (libsimavr,
 * GPL-3.0, github.com/buserror/simavr) as an ATmega328P at 16 MHz, with
 *
 *   - USART0 bridged to ONE TCP client (raw bytes both ways; the host side turns it into a pty for the bridge —
 *     board.custom.twin_pty). simavr 1.6's `simavr` CLI has NO uart-pty flag (its uart_pty lives in the example
 *     parts library and makes a pty INSIDE the process's /dev/pts, unreachable from a host when containerised), so
 *     this harness owns the UART through its IRQs: UART_IRQ_OUTPUT → the client, the client → UART_IRQ_INPUT, paced
 *     at one byte time (10 bits at 115200 Bd) and only while the UART raises XON (its input fifo has room).
 *   - ADC0 driven from OUTSIDE (simavr's ADC_IRQ_ADC0 carries millivolts): a fixed value (--adc0-mv) or a
 *     triangle ramp (--adc0-ramp lo,hi,period_ms) — the TMP36's output, 750 mV = 25 °C. brd-fi: ADC1..ADC5 held at
 *     fixed millivolts too (--adc-mv CH=MV, repeatable) — the uno-adc-sweep variant reads A0..A2.
 *   - PORTB5 (D13, the LED) observed through its ioport IRQ: every edge is a JSON line on stdout; OCR0A (the PWM
 *     compare, data address 0x47) is read on every status line.
 *   - real-time pacing by default (sim time tracks wall time, so 10 Hz telemetry is 10 Hz on the wire);
 *     --bench N free-runs N cycles and prints cycles/s (the BoardSimCost measurement), --state-size prints the
 *     simulator's own state sizes.
 *
 * C only (RULE 2 is about the device side; this is the simulator, written in the same language as simavr itself).
 * Output: JSON lines on stdout ({"t":"ready"|"status"|"pb5"|"bench"|"state"|"exit", ...}).
 */
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include <sim_avr.h>
#include <sim_elf.h>
#include <sim_hex.h>
#include <sim_irq.h>
#include <sim_io.h>
#include <sim_time.h>
#include <avr_uart.h>
#include <avr_adc.h>
#include <avr_ioport.h>

#define OCR0A_ADDR 0x47u   /* ATmega328P data-space address of OCR0A (I/O 0x27 + 0x20) */
#define TXBUF 8192

static volatile sig_atomic_t g_run = 1;
static avr_t *g_avr;
static int g_listen = -1, g_client = -1;
static uint8_t g_tx[TXBUF];
static size_t g_tx_len;
static uint8_t g_rx[TXBUF];
static size_t g_rx_head, g_rx_len;
static int g_xon = 1;
static unsigned long g_uart_tx, g_uart_rx, g_tx_dropped, g_pb5_edges;
static int g_pb5 = -1;

static void on_sig(int s) { (void)s; g_run = 0; }

static double now_s(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + (double)t.tv_nsec * 1e-9;
}

static double sim_ms(void) { return (double)avr_cycles_to_nsec(g_avr, g_avr->cycle) / 1e6; }

static void uart_out(struct avr_irq_t *irq, uint32_t value, void *param)
{
    (void)irq; (void)param;
    g_uart_tx++;
    if (g_client < 0) return;            /* nobody attached: the byte is gone, like a UART with no cable */
    if (g_tx_len < TXBUF) g_tx[g_tx_len++] = (uint8_t)value; else g_tx_dropped++;
}

static void uart_xon(struct avr_irq_t *irq, uint32_t value, void *param) { (void)irq; (void)value; (void)param; g_xon = 1; }
static void uart_xoff(struct avr_irq_t *irq, uint32_t value, void *param) { (void)irq; (void)value; (void)param; g_xon = 0; }

static void pb5(struct avr_irq_t *irq, uint32_t value, void *param)
{
    (void)irq; (void)param;
    int v = value ? 1 : 0;
    if (v == g_pb5) return;
    g_pb5 = v;
    g_pb5_edges++;
    printf("{\"t\":\"pb5\",\"v\":%d,\"cycle\":%llu,\"sim_ms\":%.3f}\n", v, (unsigned long long)g_avr->cycle, sim_ms());
    fflush(stdout);
}

static int listen_tcp(int port)
{
    int s = socket(AF_INET, SOCK_STREAM, 0), one = 1;
    struct sockaddr_in a;
    if (s < 0) return -1;
    setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_port = htons((uint16_t)port);
    a.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(s, (struct sockaddr *)&a, sizeof a) < 0 || listen(s, 1) < 0) { close(s); return -1; }
    fcntl(s, F_SETFL, O_NONBLOCK);
    return s;
}

static void poll_tcp(void)
{
    if (g_listen >= 0 && g_client < 0) {
        int c = accept(g_listen, NULL, NULL);
        if (c >= 0) {
            int one = 1;
            fcntl(c, F_SETFL, O_NONBLOCK);
            setsockopt(c, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
            g_client = c;
            printf("{\"t\":\"client\",\"attached\":1,\"sim_ms\":%.3f}\n", sim_ms());
            fflush(stdout);
        }
    }
    if (g_client < 0) return;
    if (g_tx_len) {
        ssize_t n = send(g_client, g_tx, g_tx_len, MSG_NOSIGNAL);
        if (n > 0) { memmove(g_tx, g_tx + n, g_tx_len - (size_t)n); g_tx_len -= (size_t)n; }
        else if (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK) goto gone;
    }
    if (g_rx_len < TXBUF) {
        uint8_t tmp[512];
        size_t room = TXBUF - g_rx_len;
        ssize_t n = recv(g_client, tmp, room < sizeof tmp ? room : sizeof tmp, 0);
        if (n == 0) goto gone;
        if (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK) goto gone;
        for (ssize_t i = 0; i < n; i++) g_rx[(g_rx_head + g_rx_len++) % TXBUF] = tmp[i];
    }
    return;
gone:
    close(g_client);
    g_client = -1;
    g_tx_len = 0;
    printf("{\"t\":\"client\",\"attached\":0,\"sim_ms\":%.3f}\n", sim_ms());
    fflush(stdout);
}

static long peak_rss_kb(void)
{
    struct rusage r;
    getrusage(RUSAGE_SELF, &r);
    return r.ru_maxrss;
}

static void status(const char *t, uint32_t adc_mv, double wall0)
{
    double w = now_s() - wall0;
    printf("{\"t\":\"%s\",\"cycle\":%llu,\"sim_ms\":%.1f,\"wall_s\":%.3f,\"pb5\":%d,\"pb5_edges\":%lu,\"ocr0a\":%u,"
           "\"adc0_mv\":%u,\"uart_tx_bytes\":%lu,\"uart_rx_bytes\":%lu,\"tx_dropped\":%lu,\"client\":%d,\"peak_rss_kb\":%ld}\n",
           t, (unsigned long long)g_avr->cycle, sim_ms(), w, g_pb5, g_pb5_edges, (unsigned)g_avr->data[OCR0A_ADDR],
           adc_mv, g_uart_tx, g_uart_rx, g_tx_dropped, g_client >= 0, peak_rss_kb());
    fflush(stdout);
}

static void usage(void)
{
    fprintf(stderr, "usage: polari-avr-twin --hex FW.hex [--mcu atmega328p] [--freq 16000000] [--tcp PORT]\n"
                    "       [--adc0-mv MV | --adc0-ramp LO,HI,PERIOD_MS] [--adc-mv CH=MV ...] [--free] [--status-ms MS] [--seconds S]\n"
                    "       [--bench CYCLES] [--state-size]\n");
}

int main(int argc, char **argv)
{
    const char *hex = NULL, *mcu = "atmega328p";
    uint32_t freq = 16000000u, adc_mv = 750u, ramp_lo = 0, ramp_hi = 0, ramp_ms = 0, status_ms = 1000u;
    int port = 0, realtime = 1, state_size = 0;
    unsigned long long bench = 0;
    double seconds = 0;
    uint32_t adc_ch_mv[6] = { 0 };   /* brd-fi: ADC1..ADC5 (index = channel; ADC0 has its own flags) */
    int adc_ch_set[6] = { 0 };
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--hex") && i + 1 < argc) hex = argv[++i];
        else if (!strcmp(argv[i], "--mcu") && i + 1 < argc) mcu = argv[++i];
        else if (!strcmp(argv[i], "--freq") && i + 1 < argc) freq = (uint32_t)strtoul(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--tcp") && i + 1 < argc) port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--adc0-mv") && i + 1 < argc) adc_mv = (uint32_t)strtoul(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--adc0-ramp") && i + 1 < argc) {
            if (sscanf(argv[++i], "%u,%u,%u", &ramp_lo, &ramp_hi, &ramp_ms) != 3 || !ramp_ms) { usage(); return 2; }
        }
        else if (!strcmp(argv[i], "--adc-mv") && i + 1 < argc) {
            unsigned ch = 0, mv = 0;
            if (sscanf(argv[++i], "%u=%u", &ch, &mv) != 2 || ch < 1u || ch > 5u) { usage(); return 2; }
            adc_ch_mv[ch] = mv;
            adc_ch_set[ch] = 1;
        }
        else if (!strcmp(argv[i], "--free")) realtime = 0;
        else if (!strcmp(argv[i], "--status-ms") && i + 1 < argc) status_ms = (uint32_t)strtoul(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--seconds") && i + 1 < argc) seconds = atof(argv[++i]);
        else if (!strcmp(argv[i], "--bench") && i + 1 < argc) bench = strtoull(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--state-size")) state_size = 1;
        else { usage(); return 2; }
    }
    if (!hex) { usage(); return 2; }

    elf_firmware_t f;
    memset(&f, 0, sizeof f);
    ihex_chunk_p chunk = NULL;
    int cnt = read_ihex_chunks(hex, &chunk);
    if (cnt <= 0) { fprintf(stderr, "cannot read %s as Intel HEX\n", hex); return 1; }
    for (int ci = 0; ci < cnt; ci++)
        if (chunk[ci].baseaddr < (1u * 1024u * 1024u)) {   /* flash; EEPROM chunks sit at 0x810000 */
            f.flash = chunk[ci].data;
            f.flashsize = chunk[ci].size;
            f.flashbase = chunk[ci].baseaddr;
        }
    strncpy(f.mmcu, mcu, sizeof f.mmcu - 1);
    f.frequency = freq;
    g_avr = avr_make_mcu_by_name(f.mmcu);
    if (!g_avr) { fprintf(stderr, "simavr knows no mcu %s\n", mcu); return 1; }
    avr_init(g_avr);
    avr_load_firmware(g_avr, &f);
    if (f.flashbase) g_avr->pc = f.flashbase;
    g_avr->frequency = freq;
    g_avr->vcc = g_avr->avcc = g_avr->aref = 5000;   /* the UNO's 5 V rail; ADMUX REFS0 selects AVcc */
    g_avr->log = 1;                                  /* warnings only */

    if (state_size) {
        printf("{\"t\":\"state\",\"mcu\":\"%s\",\"sizeof_avr_t\":%zu,\"data_bytes\":%u,\"flash_bytes\":%u,\"eeprom_bytes\":%u,"
               "\"ioend\":%u,\"note\":\"core state = avr_t + data space (registers+I/O+SRAM) + flash + eeprom; the per-peripheral "
               "structs live in simavr's mcu_t beside avr_t and are bounded by the process RSS\"}\n",
               mcu, sizeof(avr_t), (unsigned)g_avr->ramend + 1u, (unsigned)g_avr->flashend + 1u, (unsigned)g_avr->e2end + 1u,
               (unsigned)g_avr->ioend);
        fflush(stdout);
        if (!bench && !port) return 0;
    }

    /* the UART is ours: no stdio echo, no poll-sleep (a polling firmware must not slow the wall clock) */
    uint32_t fl = 0;
    avr_ioctl(g_avr, AVR_IOCTL_UART_GET_FLAGS('0'), &fl);
    fl &= ~(uint32_t)(AVR_UART_FLAG_STDIO | AVR_UART_FLAG_POLL_SLEEP);
    avr_ioctl(g_avr, AVR_IOCTL_UART_SET_FLAGS('0'), &fl);
    avr_irq_register_notify(avr_io_getirq(g_avr, AVR_IOCTL_UART_GETIRQ('0'), UART_IRQ_OUTPUT), uart_out, NULL);
    avr_irq_register_notify(avr_io_getirq(g_avr, AVR_IOCTL_UART_GETIRQ('0'), UART_IRQ_OUT_XON), uart_xon, NULL);
    avr_irq_register_notify(avr_io_getirq(g_avr, AVR_IOCTL_UART_GETIRQ('0'), UART_IRQ_OUT_XOFF), uart_xoff, NULL);
    avr_irq_t *uart_in = avr_io_getirq(g_avr, AVR_IOCTL_UART_GETIRQ('0'), UART_IRQ_INPUT);
    avr_irq_t *adc0 = avr_io_getirq(g_avr, AVR_IOCTL_ADC_GETIRQ, ADC_IRQ_ADC0);
    avr_irq_register_notify(avr_io_getirq(g_avr, AVR_IOCTL_IOPORT_GETIRQ('B'), 5), pb5, NULL);
    avr_raise_irq(adc0, ramp_ms ? ramp_lo : adc_mv);
    for (unsigned ch = 1u; ch <= 5u; ch++)
        if (adc_ch_set[ch]) avr_raise_irq(avr_io_getirq(g_avr, AVR_IOCTL_ADC_GETIRQ, ADC_IRQ_ADC0 + ch), adc_ch_mv[ch]);

    signal(SIGINT, on_sig);
    signal(SIGTERM, on_sig);
    signal(SIGPIPE, SIG_IGN);

    double wall0 = now_s();
    if (bench) {
        int st = cpu_Running;
        while (g_avr->cycle < bench && st != cpu_Done && st != cpu_Crashed && g_run) st = avr_run(g_avr);
        double w = now_s() - wall0;
        printf("{\"t\":\"bench\",\"cycles\":%llu,\"wall_s\":%.4f,\"cycles_per_s\":%.0f,\"realtime_factor\":%.3f,"
               "\"uart_tx_bytes\":%lu,\"peak_rss_kb\":%ld}\n",
               (unsigned long long)g_avr->cycle, w, (double)g_avr->cycle / w, (double)g_avr->cycle / w / (double)freq,
               g_uart_tx, peak_rss_kb());
        return 0;
    }
    if (port) {
        g_listen = listen_tcp(port);
        if (g_listen < 0) { fprintf(stderr, "cannot listen on tcp %d: %s\n", port, strerror(errno)); return 1; }
    }
    printf("{\"t\":\"ready\",\"mcu\":\"%s\",\"freq\":%u,\"hex\":\"%s\",\"flash_bytes_loaded\":%u,\"tcp\":%d,\"realtime\":%d,"
           "\"adc0\":\"%s\",\"adc1_mv\":%u,\"adc2_mv\":%u}\n", mcu, freq, hex, f.flashsize, port, realtime, ramp_ms ? "ramp" : "fixed",
           adc_ch_mv[1], adc_ch_mv[2]);
    fflush(stdout);

    const avr_cycle_count_t poll_every = freq / 10000u;          /* 100 us of sim time */
    const avr_cycle_count_t byte_cycles = (avr_cycle_count_t)freq * 10u / 115200u;
    avr_cycle_count_t next_poll = 0, next_feed = 0, next_status = (avr_cycle_count_t)freq / 1000u * status_ms;
    uint32_t cur_mv = ramp_ms ? ramp_lo : adc_mv;
    int st = cpu_Running;
    while (g_run && st != cpu_Done && st != cpu_Crashed) {
        st = avr_run(g_avr);
        if (g_avr->cycle < next_poll) continue;
        next_poll = g_avr->cycle + poll_every;
        poll_tcp();
        if (g_rx_len && g_xon && g_avr->cycle >= next_feed) {
            avr_raise_irq(uart_in, g_rx[g_rx_head]);
            g_rx_head = (g_rx_head + 1) % TXBUF;
            g_rx_len--;
            g_uart_rx++;
            next_feed = g_avr->cycle + byte_cycles;
        }
        if (ramp_ms) {   /* triangle lo → hi → lo over period_ms of SIM time */
            uint32_t ms = (uint32_t)(sim_ms()) % ramp_ms, half = ramp_ms / 2u;
            uint32_t span = ramp_hi > ramp_lo ? ramp_hi - ramp_lo : 0u;
            uint32_t mv = ramp_lo + (uint32_t)((uint64_t)span * (ms < half ? ms : ramp_ms - ms) / (half ? half : 1u));
            if (mv != cur_mv) { cur_mv = mv; avr_raise_irq(adc0, mv); }
        }
        if (realtime) {
            double ahead = sim_ms() / 1000.0 - (now_s() - wall0);
            if (ahead > 0.002) {
                struct timespec t = { 0, (long)(ahead * 1e9) };
                nanosleep(&t, NULL);
            }
        }
        if (status_ms && g_avr->cycle >= next_status) {
            next_status += (avr_cycle_count_t)freq / 1000u * status_ms;
            status("status", cur_mv, wall0);
        }
        if (seconds > 0 && sim_ms() >= seconds * 1000.0) break;
    }
    status("exit", cur_mv, wall0);
    if (g_client >= 0) close(g_client);
    if (g_listen >= 0) close(g_listen);
    return st == cpu_Crashed ? 3 : 0;
}
