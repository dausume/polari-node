"""
scope-engines — the oscilloscope EMULATOR worker (osc-5, OSCILLOSCOPE_PLAN.md §3f): the same emulator the backend can run in-process
(polari-framework/modules/instruments/custom/emulator.py, vendored into this image by build.sh — one source of truth), behind one JSON
control API (:9880) and up to five raw SCPI sockets (:5555-:5559, one per emulated instrument), so an isle node reaches an emulated DHO804
exactly as it would a real one on the LAN — by address and port — and the Polari client, record and chart are the same code either way.
The backend resolves this worker through modules/instruments/custom/scope_engines.py: SCOPE_ENGINES_URL knob → the EngineProviderBinding
row `scope` (written by `isle app deploy … --engine scope`, the catalog tile `scope-engines`) → in-process (the one engine with an honest
local rung) — a declared worker that is unreachable is a REFUSAL, never a silent fallback.

  GET    /capability                 {worker, profiles, scpi: {host, ports, free}, emulators, resources (cost.json), versions}
  GET    /system-info                process block (rss, cpu seconds, uptime)
  GET    /emulators                  the running emulators
  POST   /emulators                  {profile?, name?, strict_idn?, refuse_wav_data?, sources?: {channel: spec}} -> 201 {ok, id, port, session, idn}
  GET    /emulators/{id}             one emulator's state summary (channels on, scales, timebase, depth, sample rate, sources)
  DELETE /emulators/{id}             stop it, free its port
  POST   /emulators/{id}/source      {channel, source: {kind, ...}} -> 200
  POST   /emulators/{id}/feed        {channels: {channel: spec}, seconds, depth?, scale_v_div?} -> 200 (the backend resolves a stimulus or the
                                     twin's events into specs first — this image carries no board module)
  GET    /cost                       the measured cost block (cost.json)
Control-plane only: waveform DATA never crosses this API — it is read over SCPI like on a real instrument.
"""
import json
import os
import resource
import sys
import time

import falcon

sys.path.insert(0, os.environ.get('SCOPE_VENDOR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor')))
from instruments.custom.emulator import Emulator, PROFILES, EMU_VERSION   # noqa: E402
from instruments.custom import sources as SRC                               # noqa: E402

SCPI_HOST = os.environ.get('SCOPE_SCPI_HOST', '0.0.0.0')
_FIRST = int(os.environ.get('SCOPE_SCPI_PORT_FIRST', '5555'))
SCPI_PORTS = list(range(_FIRST, _FIRST + int(os.environ.get('SCOPE_SCPI_PORTS', '5'))))
EMUS = {}
_T0 = time.time()
_HERE = os.path.dirname(os.path.abspath(__file__))


def _cost():
    for p in ('/srv/cost.json', os.path.join(_HERE, 'cost.json')):
        try:
            return json.load(open(p))
        except (OSError, ValueError):
            continue
    return {'fidelity': 'unmeasured', 'note': 'no cost.json beside the service'}


def _process_block():
    ru = resource.getrusage(resource.RUSAGE_SELF)
    return {'rss_mb': round(ru.ru_maxrss / 1024.0, 1), 'cpu_s': round(ru.ru_utime + ru.ru_stime, 3), 'uptime_s': round(time.time() - _T0, 1), 'pid': os.getpid()}


def _bad(resp, msg, status=falcon.HTTP_400, **extra):
    resp.status = status
    resp.media = dict({'ok': False, 'error': msg}, **extra)


def _body(req):
    raw = req.bounded_stream.read()
    if not raw:
        return {}
    return json.loads(raw.decode('utf-8'))


def _summary(eid, rec):
    s = rec['emu'].state
    return {'id': eid, 'profile': s.profile, 'model': s.p['model'], 'port': rec['port'], 'host': SCPI_HOST, 'session': s.session, 'started': rec['started'],
            'idn': rec['emu'].idn(), 'channels_on': s.enabled(), 'scales_v_div': {n: c['scale'] for n, c in s.channels.items()},
            'timebase_s_div': s.tb_scale, 'timebase_offset_s': s.tb_offset, 'memory_depth_pts': int(s.mdepth), 'sample_rate_sps': s.srate(),
            'acquisitions': s.acquisitions, 'sources': {n: SRC.describe(sp) for n, sp in s.sources.items()},
            'strict_idn': rec['emu'].strict_idn, 'refuse_wav_data': rec['emu'].refuse_wav_data}


class Capability:
    def on_get(self, req, resp):
        used = {r['port'] for r in EMUS.values()}
        resp.media = {'ok': True, 'worker': 'scope-engines', 'version': EMU_VERSION,
                      'profiles': {k: {'vendor': v['vendor'], 'model': v['model'], 'channels': v['channels'], 'bits': v['bits'], 'basis': v['basis']} for k, v in PROFILES.items()},
                      'scpi': {'host': SCPI_HOST, 'ports': SCPI_PORTS, 'free': [p for p in SCPI_PORTS if p not in used]},
                      'emulators': {eid: _summary(eid, r) for eid, r in EMUS.items()},
                      'resources': _cost(), 'process': _process_block()}


class SystemInfo:
    def on_get(self, req, resp):
        import platform
        resp.media = {'ok': True, 'platform': platform.platform(), 'python': platform.python_version(), 'process': _process_block()}


class Cost:
    def on_get(self, req, resp):
        resp.media = dict(_cost(), ok=True, process=_process_block())


class Emulators:
    def on_get(self, req, resp):
        resp.media = {'ok': True, 'rows': [_summary(eid, r) for eid, r in EMUS.items()]}

    def on_post(self, req, resp):
        try:
            body = _body(req)
        except ValueError:
            return _bad(resp, 'the body is not JSON')
        profile = str(body.get('profile') or 'dho804')
        if profile not in PROFILES:
            return _bad(resp, 'no emulator profile %r' % profile, known=sorted(PROFILES))
        used = {r['port'] for r in EMUS.values()}
        free = [p for p in SCPI_PORTS if p not in used]
        if not free:
            return _bad(resp, 'every SCPI port is taken (%s) - stop an emulator first' % SCPI_PORTS, falcon.HTTP_409)
        eid = str(body.get('name') or 'emu-%s-%d' % (profile, len(EMUS) + 1))
        if eid in EMUS:
            return _bad(resp, 'an emulator named %r is already running here' % eid, falcon.HTTP_409)
        try:
            emu = Emulator(profile=profile, host=SCPI_HOST, port=free[0], strict_idn=bool(body.get('strict_idn')), refuse_wav_data=bool(body.get('refuse_wav_data')))
            for ch, spec in (body.get('sources') or {}).items():
                emu.set_source(int(ch), spec)
            emu.start()
        except (ValueError, OSError) as e:
            return _bad(resp, str(e))
        EMUS[eid] = {'emu': emu, 'port': emu.port, 'started': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        resp.status = falcon.HTTP_201
        resp.media = dict(_summary(eid, EMUS[eid]), ok=True)


class EmulatorOne:
    def on_get(self, req, resp, eid):
        if eid not in EMUS:
            return _bad(resp, 'no emulator %r here' % eid, falcon.HTTP_404, known=sorted(EMUS))
        resp.media = dict(_summary(eid, EMUS[eid]), ok=True)

    def on_delete(self, req, resp, eid):
        rec = EMUS.pop(eid, None)
        if rec is None:
            return _bad(resp, 'no emulator %r here' % eid, falcon.HTTP_404, known=sorted(EMUS))
        rec['emu'].stop()
        resp.media = {'ok': True, 'id': eid, 'port': rec['port'], 'stopped': True}


class Source:
    def on_post(self, req, resp, eid):
        if eid not in EMUS:
            return _bad(resp, 'no emulator %r here' % eid, falcon.HTTP_404)
        try:
            body = _body(req)
            ch = int(body.get('channel', 0))
            EMUS[eid]['emu'].set_source(ch, body.get('source'))
        except (ValueError, TypeError, KeyError) as e:
            return _bad(resp, str(e) or 'channel + source are required')
        resp.media = {'ok': True, 'id': eid, 'channel': ch, 'source': SRC.describe(body['source'])}


class Feed:
    def on_post(self, req, resp, eid):
        if eid not in EMUS:
            return _bad(resp, 'no emulator %r here' % eid, falcon.HTTP_404)
        try:
            body = _body(req)
            from instruments.custom.device_feed import feed
            desc = feed(EMUS[eid]['emu'], {int(k): v for k, v in (body.get('channels') or {}).items()}, body.get('seconds') or 0,
                        scale_v_div=body.get('scale_v_div') or 2.0, depth=body.get('depth'))
        except (ValueError, TypeError, KeyError) as e:
            return _bad(resp, str(e) or 'channels {n: spec} + seconds are required')
        resp.media = {'ok': True, 'id': eid, 'channels': {str(k): v for k, v in desc.items()}}


app = falcon.App() if hasattr(falcon, 'App') else falcon.API()
app.add_route('/capability', Capability())
app.add_route('/system-info', SystemInfo())
app.add_route('/cost', Cost())
app.add_route('/emulators', Emulators())
app.add_route('/emulators/{eid}', EmulatorOne())
app.add_route('/emulators/{eid}/source', Source())
app.add_route('/emulators/{eid}/feed', Feed())


def stop_all():
    for rec in list(EMUS.values()):
        rec['emu'].stop()
    EMUS.clear()
