/* twin_ctl.c — the control channel of polari-avr-twin (see twin_ctl.h for the line protocol). */
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include <sim_irq.h>
#include <sim_io.h>
#include <avr_ioport.h>

#include "twin_ctl.h"
#include "twin_forcing.h"

#define OUTBUF 16384
#define INBUF 4096

static int g_listen = -1, g_client = -1, g_authed, g_paused;   /* g_authed: 1 ok, 0 waiting for the token, -1 hang up */
static const char *g_token;
static char g_in[INBUF], g_out[OUTBUF];
static size_t g_in_len, g_out_len;
static unsigned long g_dropped;

typedef struct { avr_t *avr; char name[4]; int registered, watched, last; } wpin_t;
static wpin_t g_wp[3][8];   /* port B, C, D x bit */

static void out_raw(const char *s, size_t n)
{
    if (g_client < 0) return;
    if (g_out_len + n > OUTBUF) { g_dropped += n; return; }
    memcpy(g_out + g_out_len, s, n);
    g_out_len += n;
}

static void outf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void outf(const char *fmt, ...)
{
    char b[1024];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b - 1, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if ((size_t)n > sizeof b - 2) n = (int)sizeof b - 2;
    b[n++] = '\n';
    out_raw(b, (size_t)n);
}

static void refuse(const char *why) { outf("{\"t\":\"refused\",\"why\":\"%s\"}", why); }

static void refuse_pin(const ctl_host_t *h, const char *pin)
{
    char w[128];
    snprintf(w, sizeof w, "%.8s is not a pin of %s", pin, h->mcu);
    refuse(w);
}

/* the value text after "key": in a one-line object, or NULL (strings carry no escapes) */
static const char *jfind(const char *l, const char *key)
{
    char pat[48];
    snprintf(pat, sizeof pat, "\"%s\"", key);
    const char *p = strstr(l, pat);
    if (!p) return NULL;
    p += strlen(pat);
    while (*p == ' ') p++;
    if (*p != ':') return NULL;
    p++;
    while (*p == ' ') p++;
    return p;
}

static int jstr(const char *l, const char *key, char *buf, size_t n)
{
    const char *p = jfind(l, key);
    size_t i = 0;
    if (!p || *p != '"') return -1;
    for (p++; *p && *p != '"'; p++) {
        if (*p == '\\' || i + 1 >= n) return -1;
        buf[i++] = *p;
    }
    if (*p != '"') return -1;
    buf[i] = 0;
    return 0;
}

static int jint(const char *l, const char *key, long *v)
{
    const char *p = jfind(l, key);
    char *e;
    if (!p) return -1;
    *v = strtol(p, &e, 10);
    return e == p ? -1 : 0;
}

static int jbool(const char *l, const char *key, int *v)
{
    const char *p = jfind(l, key);
    long n;
    if (!p) return -1;
    if (!strncmp(p, "true", 4)) { *v = 1; return 0; }
    if (!strncmp(p, "false", 5)) { *v = 0; return 0; }
    if (jint(l, key, &n) == 0) { *v = n != 0; return 0; }
    return -1;
}

static wpin_t *wp_of(const char *spec)
{
    if (strlen(spec) != 3 || spec[0] != 'P' || spec[2] < '0' || spec[2] > '7') return NULL;
    int p = spec[1] == 'B' ? 0 : spec[1] == 'C' ? 1 : spec[1] == 'D' ? 2 : -1;
    return p < 0 ? NULL : &g_wp[p][spec[2] - '0'];
}

static void on_pin(struct avr_irq_t *irq, uint32_t value, void *param)
{
    wpin_t *w = param;
    (void)irq;
    int v = value ? 1 : 0;
    if (!w->watched || v == w->last) return;
    w->last = v;
    if (g_authed == 1) outf("{\"t\":\"pin\",\"pin\":\"%s\",\"level\":%d,\"cycle\":%llu}", w->name, v, (unsigned long long)w->avr->cycle);
}

static void cmd_watch(const ctl_host_t *h, const char *l)
{
    const char *p = jfind(l, "watch");
    char names[16][4];
    int n = 0;
    if (!p || *p != '[') { refuse("watch takes a list of pins, like [\"PD6\",\"PB5\"]"); return; }
    for (p++; *p && *p != ']'; p++) {
        if (*p != '"') continue;
        const char *e = strchr(p + 1, '"');
        if (!e || e - p - 1 != 3 || n >= 16) { refuse("a pin name is three characters like PD6 (at most 16 pins)"); return; }
        memcpy(names[n], p + 1, 3);
        names[n++][3] = 0;
        p = e;
    }
    for (int i = 0; i < n; i++)
        if (!wp_of(names[i]) || !forcing_pin_irq(h->avr, names[i])) { refuse_pin(h, names[i]); return; }
    for (int a = 0; a < 3; a++) for (int b = 0; b < 8; b++) g_wp[a][b].watched = 0;
    char list[160] = "";
    for (int i = 0; i < n; i++) {
        wpin_t *w = wp_of(names[i]);
        if (!w->registered) {
            w->avr = h->avr;
            memcpy(w->name, names[i], 4);
            avr_irq_register_notify(forcing_pin_irq(h->avr, names[i]), on_pin, w);
            w->registered = 1;
        }
        w->watched = 1;
        w->last = -1;
        snprintf(list + strlen(list), sizeof list - strlen(list), "%s\"%s\"", i ? "," : "", names[i]);
    }
    outf("{\"t\":\"ack\",\"of\":\"watch\",\"watch\":[%s]}", list);
}

static void handle(const ctl_host_t *h, const char *l)
{
    char s[64];
    long v, v2;
    int b, did = 0;
    if (jstr(l, "token", s, sizeof s) == 0) {
        did++;
        if (g_token && strcmp(s, g_token)) { refuse("the token is wrong"); g_authed = -1; return; }
        g_authed = 1;
        outf("{\"t\":\"ack\",\"of\":\"token\"}");
    }
    if (g_authed != 1) { refuse("this twin needs its token as the first line: {\"token\":\"...\"}"); g_authed = -1; return; }
    if (jstr(l, "set", s, sizeof s) == 0) {
        did++;
        if (jint(l, "level", &v) || (v != 0 && v != 1)) refuse("set needs \"level\": 0 or 1");
        else if (!forcing_pin_irq(h->avr, s)) refuse_pin(h, s);
        else {
            avr_raise_irq(forcing_pin_irq(h->avr, s), (uint32_t)v);
            outf("{\"t\":\"ack\",\"of\":\"set\",\"pin\":\"%s\",\"level\":%ld,\"cycle\":%llu}", s, v, (unsigned long long)h->avr->cycle);
        }
    }
    if (jint(l, "adc", &v) == 0) {
        did++;
        if (v < 0 || v > 5) refuse("adc channel must be 0..5");
        else if (jint(l, "mv", &v2) || v2 < 0 || v2 > 5000) refuse("adc needs \"mv\": 0..5000 (the 5 V rail)");
        else { h->set_adc((unsigned)v, (uint32_t)v2); outf("{\"t\":\"ack\",\"of\":\"adc\",\"adc\":%ld,\"mv\":%ld}", v, v2); }
    }
    if (jfind(l, "watch")) { did++; cmd_watch(h, l); }
    if (jbool(l, "run", &b) == 0) { did++; g_paused = !b; outf("{\"t\":\"ack\",\"of\":\"run\",\"run\":%s}", b ? "true" : "false"); }
    if (jfind(l, "status")) {
        char st[768];
        did++;
        h->status_json(st, sizeof st);
        outf("%s", st);
    }
    if (jfind(l, "reset")) {
        did++;
        avr_reset(h->avr);
        outf("{\"t\":\"ack\",\"of\":\"reset\",\"cycle\":%llu}", (unsigned long long)h->avr->cycle);
    }
    if (!did) refuse("no command in that line (token, set, adc, watch, run, status, reset)");
}

static void drop_client(void)
{
    if (g_client >= 0) close(g_client);
    g_client = -1;
    g_authed = 0;
    g_in_len = g_out_len = 0;
    g_paused = 0;   /* a vanished controller must not leave the twin frozen */
    for (int a = 0; a < 3; a++) for (int b = 0; b < 8; b++) g_wp[a][b].watched = 0;
}

int ctl_listen(int port, const char *token)
{
    int s = socket(AF_INET, SOCK_STREAM, 0), one = 1;
    struct sockaddr_in a;
    g_token = (token && *token) ? token : NULL;
    if (s < 0) return -1;
    setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_port = htons((uint16_t)port);
    a.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(s, (struct sockaddr *)&a, sizeof a) < 0 || listen(s, 2) < 0) { close(s); return -1; }
    fcntl(s, F_SETFL, O_NONBLOCK);
    g_listen = s;
    return 0;
}

void ctl_poll(const ctl_host_t *h)
{
    if (g_listen < 0) return;
    int c = accept(g_listen, NULL, NULL);
    if (c >= 0) {
        if (g_client >= 0) {
            static const char m[] = "{\"t\":\"refused\",\"why\":\"another control client is attached (one at a time)\"}\n";
            send(c, m, sizeof m - 1, MSG_NOSIGNAL);
            close(c);
        } else {
            int one = 1;
            fcntl(c, F_SETFL, O_NONBLOCK);
            setsockopt(c, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
            g_client = c;
            g_authed = g_token ? 0 : 1;
            g_in_len = g_out_len = 0;
        }
    }
    if (g_client < 0) return;
    ssize_t n = recv(g_client, g_in + g_in_len, INBUF - g_in_len, 0);
    if (n == 0 || (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK)) { drop_client(); return; }
    if (n > 0) g_in_len += (size_t)n;
    char *nl;
    while (g_client >= 0 && (nl = memchr(g_in, '\n', g_in_len))) {
        *nl = 0;
        handle(h, g_in);
        size_t used = (size_t)(nl - g_in) + 1;
        memmove(g_in, g_in + used, g_in_len - used);
        g_in_len -= used;
        if (g_authed < 0) {   /* flush the refusal, then hang up */
            if (g_out_len) send(g_client, g_out, g_out_len, MSG_NOSIGNAL);
            drop_client();
            return;
        }
    }
    if (g_in_len == INBUF) { refuse("a command line is longer than 4095 bytes"); g_in_len = 0; }
    if (g_client >= 0 && g_out_len) {
        ssize_t w = send(g_client, g_out, g_out_len, MSG_NOSIGNAL);
        if (w > 0) { memmove(g_out, g_out + w, g_out_len - (size_t)w); g_out_len -= (size_t)w; }
        else if (w < 0 && errno != EAGAIN && errno != EWOULDBLOCK) drop_client();
    }
}

int ctl_paused(void) { return g_paused; }
int ctl_attached(void) { return g_client >= 0 && g_authed == 1; }
unsigned long ctl_dropped(void) { return g_dropped; }

void ctl_close(void)
{
    drop_client();
    if (g_listen >= 0) close(g_listen);
    g_listen = -1;
}
