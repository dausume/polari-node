#!/usr/bin/env python3
"""
polari-idf-build — ONE ESP-IDF build of a generated C3 project, as a file-in / files-out engine (sc-3; the board
engines' /run protocol: basenames in the job dir, argv only).

    polari-idf-build project.tar

  in   project.tar          the project `pol board gen c3` rendered (CMakeLists.txt, sdkconfig.defaults, partitions.csv,
                            main/…) — extracted into ./proj (absolute paths and `..` refused)
  out  firmware.elf         the app ELF
       app.bin              the app image (offset 0x10000)
       bootloader.bin       (0x0) · partition-table.bin (0x8000) · flash_args.txt (idf.py's own offsets/flags)
       flash_image.bin      `esptool.py --chip esp32c3 merge_bin --fill-flash-size 4MB @flash_args` — what the twin boots
       size.json            `idf.py size --format json2` (flash / DIRAM per section)
       build.json           {ok, idf_version, steps: [{step, rc, wall_s, cpu_s, peak_rss_mb}], sha256: {file: …}}
       build.log            the build's stdout+stderr (the last 200 000 characters)

Each step's cost comes from wait4() on that step's process: CPU-s = user+sys of it and every descendant it reaped,
peak RSS = the LARGEST single process in that tree (Linux ru_maxrss semantics — not a sum). The build dir starts empty
every time (no ccache, IDF_CCACHE_ENABLE=0), so wall/CPU are a from-scratch build's.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time

PROJ = 'proj'
OUTS = {'firmware.elf': 'polari_c3.elf', 'app.bin': 'polari_c3.bin', 'bootloader.bin': 'bootloader/bootloader.bin',
        'partition-table.bin': 'partition_table/partition-table.bin', 'flash_args.txt': 'flash_args'}


def run_step(name, argv, cwd, log):
    """Popen + wait4 so the rusage is THIS step's tree (not cumulative over the earlier steps)."""
    t0 = time.perf_counter()
    with open('step.out', 'w') as fo:
        p = subprocess.Popen(argv, cwd=cwd, stdout=fo, stderr=subprocess.STDOUT)
        _, status, ru = os.wait4(p.pid, 0)
    out = open('step.out').read()
    os.remove('step.out')
    rc = os.waitstatus_to_exitcode(status)
    log.append('$ %s\n%s' % (' '.join(argv), out))
    return out, {'step': name, 'rc': rc, 'wall_s': round(time.perf_counter() - t0, 2),
                 'cpu_s': round(ru.ru_utime + ru.ru_stime, 2), 'peak_rss_mb': round(ru.ru_maxrss / 1024.0, 1)}


def safe_extract(tar_path, dest):
    with tarfile.open(tar_path) as t:
        for m in t.getmembers():
            if m.name.startswith('/') or '..' in m.name.split('/') or not (m.isfile() or m.isdir()):
                raise SystemExit('polari-idf-build: refused member %r (absolute, .. or not a plain file)' % m.name)
        t.extractall(dest)


def sha(path):
    return hashlib.sha256(open(path, 'rb').read()).hexdigest()


def main(argv):
    if len(argv) != 1 or not argv[0].endswith('.tar'):
        print(__doc__)
        return 2
    shutil.rmtree(PROJ, ignore_errors=True)
    safe_extract(argv[0], PROJ)
    log, steps = [], []
    ver, _ = run_step('idf-version', ['esp-env', 'idf.py', '--version'], '.', log)
    out, s = run_step('build', ['esp-env', 'idf.py', '-C', PROJ, '-B', '%s/build' % PROJ, 'build'], '.', log)
    steps.append(s)
    ok = s['rc'] == 0
    res = {'ok': ok, 'idf_version': ver.strip().splitlines()[-1] if ver.strip() else '', 'steps': steps, 'sha256': {}}
    if ok:
        b = os.path.join(PROJ, 'build')
        for dst, src in OUTS.items():
            shutil.copy(os.path.join(b, src), dst)
        out, s = run_step('size', ['esp-env', 'idf.py', '-C', PROJ, '-B', b, 'size', '--format', 'json2'], '.', log)
        steps.append(s)
        j = out[out.find('{'):out.rfind('}') + 1] if '{' in out else '{}'
        try:
            json.loads(j)
            open('size.json', 'w').write(j)
        except ValueError:
            res['size_error'] = out[-2000:]
        out, s = run_step('merge_bin', ['esp-env', 'esptool.py', '--chip', 'esp32c3', 'merge_bin', '--fill-flash-size', '4MB',
                                        '-o', os.path.abspath('flash_image.bin'), '@flash_args'], b, log)
        steps.append(s)
        res['ok'] = ok = s['rc'] == 0 and os.path.isfile('flash_image.bin')
        for f in list(OUTS) + ['flash_image.bin', 'size.json']:
            if os.path.isfile(f):
                res['sha256'][f] = sha(f)
    json.dump(res, open('build.json', 'w'), indent=1)
    open('build.log', 'w').write('\n'.join(log)[-200000:])
    shutil.rmtree(PROJ, ignore_errors=True)
    print(json.dumps({k: res[k] for k in ('ok', 'idf_version', 'steps')}))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
