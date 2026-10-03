# prf-formal-engines

The firmware scenario arc's FORMAL + STATIC tier worker (sc-2b, `AI-Notes/plans/FIRMWARE_SCENARIO_PLAN.md` §5 tier 2, D-sc-6):
bounded model checking and static rules on the firmware's C, from Debian 13 (trixie) packages on the SAME digest-pinned base as
`prf-board-engines`.

| piece | version (Debian) | licence | role |
|---|---|---|---|
| `cbmc` (+ `goto-cc`, `goto-instrument`) | 6.6.0-4 | BSD-4-clause style (diffblue/cbmc LICENSE) — an advertising clause makes it GPL-incompatible to LINK; here it only ever runs as a separate process (plan §2d) | the bounded model checker |
| `cppcheck` (+ its addons) | 2.17.1-2 | GPL-3.0 | static rules: built-in checks + the `threadsafety` addon. The `misra` addon is NOT run — its rule texts need the non-free MISRA C document |
| `polari-cbmc-check` (`polari_cbmc_check.py`, ours) | — | the worker's | goto-cc (`--16`/`--32`) → goto-instrument (`--nondet-volatile-model VAR:FN`, `--isr FN`) → `cbmc --function --unwind K --unwinding-assertions --json-ui --trace`; each step measured with `wait4` (wall, CPU-s, peak RSS); verdict holds / refuted / bound-too-small / refused-at-compile / timeout / error; `result.json` + `trace.json` |
| `polari-cppcheck-run` (`polari_cppcheck_run.py`, ours) | — | the worker's | `cppcheck --xml` over a project → `findings.json` (run information such as missing system includes kept apart); exits 0 whenever cppcheck ran — a finding is never a failure |
| `python3-falcon` + `gunicorn` | trixie | Apache-2.0 / MIT | the `/capability` + `/run` worker on :9840 |

Base: `debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a`.

    docker compose -p formal-engines -f ../docker-compose.formal-engines.yml build     # the image (enough for a local device)
    docker compose -p formal-engines -f ../docker-compose.formal-engines.yml up -d     # the worker on :9840, for other devices

The framework resolves it through `polari-framework/modules/firmwarefaults/custom/formal_engines.py`: `FORMAL_ENGINES_URL` →
a local binary → this image on the local docker → the topology provider `firmwarefaults.formal` → a refusal naming the knob.
Unlike the board worker, `/run` keeps input files' RELATIVE paths (a model's include tree, `stubs/avr/io.h`), never absolute and
never with `..`; stdout/stderr come back WHOLE with their lengths (`stdout_chars`), a run past `WORKER_MAX_MB` (32 MB) is refused (413).

**Measured (pol-core, 2026-10-02):** see `cost.json` (served in `/capability`'s `resources` block) and
`polari-framework/modules/firmwarefaults/COST.md` (sc-2b).
