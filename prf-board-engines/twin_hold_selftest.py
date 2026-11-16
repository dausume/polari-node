"""twin_hold_selftest - a level driven onto an input pin HOLDS across the firmware's port writes (the REAL polari-avr-twin, a real firmware).

Found live 2026-11-15 (pol's Sim bench proof through the twin door): a button on PD2 with its pull-up on, pressed with {"set":"PD2","level":0}, read 1
again at the firmware's next PORTD write. simavr recomputes every input pin of a port on each PORTx/DDRx write, and a pulled-up pin goes back to 1
unless the port knows an external level stands on it (twin_forcing.c, drive_pin).

    python3 /srv/twin_hold_selftest.py        (needs avr-gcc, avr-objcopy and polari-avr-twin on PATH: the board-engines image)
"""
import json, os, shutil, socket, subprocess, sys, tempfile, time

FW = r'''
#include <avr/io.h>
/* PD2: an input with its pull-up on (a button to ground). PD6 follows it: 1 while PD2 reads 0. Each change of PD6 is a write to PORTD. */
int main(void){
  DDRD = (1<<6); PORTD = (1<<2);
  for(;;){ if (PIND & (1<<2)) PORTD &= ~(1<<6); else PORTD |= (1<<6); }
}
'''
TOKEN = 'hold-selftest-token'


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


class Ctl:
    def __init__(self, port):
        end = time.time() + 5
        while True:
            try:
                self.s = socket.create_connection(('127.0.0.1', port), timeout=1); break
            except OSError:
                if time.time() > end: raise
                time.sleep(0.05)
        self.buf = b''

    def say(self, obj):
        self.s.sendall((json.dumps(obj) + '\n').encode())

    def hear(self, seconds):
        """every JSON line that arrives within `seconds`"""
        end, out = time.time() + seconds, []
        while time.time() < end:
            self.s.settimeout(max(0.01, end - time.time()))
            try:
                d = self.s.recv(4096)
            except socket.timeout:
                break
            if not d: break
            self.buf += d
            while b'\n' in self.buf:
                line, self.buf = self.buf.split(b'\n', 1)
                if line.strip(): out.append(json.loads(line))
        return out


def pins(msgs, pin):
    return [m['level'] for m in msgs if m.get('t') == 'pin' and m.get('pin') == pin]


def run(check):
    tmp = tempfile.mkdtemp(prefix='twin-hold-')
    procs = []
    try:
        open(os.path.join(tmp, 'fw.c'), 'w').write(FW)
        subprocess.run(['avr-gcc', '-mmcu=atmega328p', '-DF_CPU=16000000UL', '-Os', '-o', 'fw.elf', 'fw.c'], cwd=tmp, check=True)
        subprocess.run(['avr-objcopy', '-O', 'ihex', 'fw.elf', 'fw.hex'], cwd=tmp, check=True)

        def twin(*more):
            port = free_port()
            p = subprocess.Popen(['polari-avr-twin', '--hex', os.path.join(tmp, 'fw.hex'), '--mcu', 'atmega328p', '--ctl', str(port), '--ctl-token', TOKEN,
                                  '--seconds', '20'] + list(more), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            procs.append(p)
            c = Ctl(port)
            c.say({'token': TOKEN}); c.say({'watch': ['PD2', 'PD6']})
            acks = [m.get('of') for m in c.hear(0.5) if m.get('t') == 'ack']
            return c, acks

        # ---- the control channel's set
        c, acks = twin()
        check('hold: the real twin runs the pull-up firmware and takes token + watch', acks == ['token', 'watch'], acks)
        c.say({'set': 'PD2', 'level': 0}); got = c.hear(0.6)
        check('hold PRESS: set PD2=0 is acked and PD2 reads 0 - and STAYS 0 through the firmware\'s PORTD write (no edge back to 1)',
              any(m.get('of') == 'set' for m in got) and pins(got, 'PD2') == [0], pins(got, 'PD2'))
        check('hold PRESS: the firmware saw the press - PD6 went to 1 and stayed (one edge, not a 6-cycle blip)', pins(got, 'PD6') == [1], pins(got, 'PD6'))
        quiet = c.hear(1.0)
        check('hold PRESS: a second later still nothing moved (the level stands until the next set)', not pins(quiet, 'PD2') and not pins(quiet, 'PD6'), quiet)
        c.say({'set': 'PD2', 'level': 1}); got = c.hear(0.6)
        check('hold RELEASE: set PD2=1 brings PD2 to 1 and the firmware turns PD6 off', pins(got, 'PD2') == [1] and pins(got, 'PD6') == [0], got)
        c.say({'set': 'PD2', 'level': 0}); got = c.hear(0.6)
        check('hold AGAIN: a second press holds like the first', pins(got, 'PD2') == [0] and pins(got, 'PD6') == [1], got)
        c.say({'set': 'PX9', 'level': 0}); got = c.hear(0.4)
        check('hold: a name that is not a pin is still refused in words', any(m.get('t') == 'refused' or 'refus' in json.dumps(m) for m in got), got)

        # ---- --pin-at takes the same path
        c2, acks = twin('--pin-at', 'cycle=24000000,pin=PD2,level=0')   # 1.5 s of simulated time: after the watch is in place
        got = c2.hear(3.0)
        check('hold --pin-at: the scheduled level lands, PD6 answers once and PD2 does not spring back',
              acks == ['token', 'watch'] and pins(got, 'PD2') == [0] and pins(got, 'PD6') == [1], (acks, got))
    finally:
        for p in procs:
            p.kill(); p.wait()
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    fails = []

    def check(label, cond, extra=''):
        print('  [%s] %s%s' % ('PASS' if cond else 'FAIL', label, '' if cond else '  <- %r' % (extra,)), flush=True)
        if not cond:
            fails.append(label)
    run(check)
    print('twin hold selftest: %d failure(s)' % len(fails))
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main())
