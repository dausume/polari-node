# prf-formal-engines

The firmware scenario arc's FORMAL + STATIC tier worker (sc-2b, `AI-Notes/plans/FIRMWARE_SCENARIO_PLAN.md` §5 tier 2, D-sc-6):
bounded model checking and static rules on the firmware's C, from Debian 13 (trixie) packages on the SAME digest-pinned base as
`prf-board-engines` — and since **sc-2c** (D-sc-6 ruled 2026-10-02: "Mthread can join the formal engines") Frama-C 33.0's Mthread
race analysis, opam-built in two extra stages (Debian has no frama-c package) and pruned to its runtime closure.

| piece | version (Debian) | licence | role |
|---|---|---|---|
| `cbmc` (+ `goto-cc`, `goto-instrument`) | 6.6.0-4 | BSD-4-clause style (diffblue/cbmc LICENSE) — an advertising clause makes it GPL-incompatible to LINK; here it only ever runs as a separate process (plan §2d) | the bounded model checker |
| `cppcheck` (+ its addons) | 2.17.1-2 | GPL-3.0 | static rules: built-in checks + the `threadsafety` addon. The `misra` addon is NOT run — its rule texts need the non-free MISRA C document |
| `polari-cbmc-check` (`polari_cbmc_check.py`, ours) | — | the worker's | goto-cc (`--16`/`--32`) → goto-instrument (`--nondet-volatile-model VAR:FN`, `--isr FN`) → `cbmc --function --unwind K --unwinding-assertions --json-ui --trace`; each step measured with `wait4` (wall, CPU-s, peak RSS); verdict holds / refuted / bound-too-small / refused-at-compile / timeout / error; `result.json` + `trace.json` |
| `polari-cppcheck-run` (`polari_cppcheck_run.py`, ours) | — | the worker's | `cppcheck --xml` over a project → `findings.json` (run information such as missing system includes kept apart); exits 0 whenever cppcheck ran — a finding is never a failure |
| `frama-c` 33.0 "Arsenic" (kernel + Eva + Mthread) | opam, `opam-repository@ac27950e` (2026-10-02 HEAD), `ocaml-system` = Debian's OCaml 5.3 | LGPL-2.1-only (opam `license`; the Mthread headers carry SPDX LGPL-2.1, CEA) | sc-2c: the ISR-vs-main-loop race analysis (`-machdep avr_16 -eva -eva-slevel 15 -mthread -mt-threads-lib builtins-only`) |
| `why3` 1.8.2 (frama-c's opam dependency) | opam | LGPL-2.1-only | linked by frama-c's WP plugin; **WP is not wired** (the evaluation: WP leaves the byte identity Unknown) — no SMT prover configured (no CVC5, no Z3) |
| `alt-ergo-free` 2.4.3 (frama-c's opam dependency) | opam | CeCILL-C | frama-c 33.0's opam requires `("alt-ergo-free" \| "alt-ergo")`: the FREE one is pinned; the build **fails** if `alt-ergo` / `alt-ergo-lib` / `alt-ergo-parsers` (LicenseRef-OCamlpro-Non-Commercial) appear; its files are not copied into the image |
| `polari-mthread-check` (`polari_mthread_check.py`, ours) | — | the worker's | frama-c → Mthread's LAST race report (one per iteration; the last = the fixed point) as data: per shared variable each access, thread, line, mutexes held (`(?)` = maybe) + the sizes the harness prints via Eva; verdict analysed / refused-at-compile / timeout / error. It decides nothing — the framework's `formal_mthread.classify` does |
| `python3-falcon` + `gunicorn` | trixie | Apache-2.0 / MIT | the `/capability` + `/run` worker on :9840 |

Base: `debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a`.

    docker compose -p formal-engines -f ../docker-compose.formal-engines.yml build     # the image (enough for a local device)
    docker compose -p formal-engines -f ../docker-compose.formal-engines.yml up -d     # the worker on :9840, for other devices

The framework resolves it through `polari-framework/modules/firmwarefaults/custom/formal_engines.py`: `FORMAL_ENGINES_URL` →
a local binary → this image on the local docker → the topology provider `firmwarefaults.formal` → a refusal naming the knob.
Unlike the board worker, `/run` keeps input files' RELATIVE paths (a model's include tree, `stubs/avr/io.h`), never absolute and
never with `..`; stdout/stderr come back WHOLE with their lengths (`stdout_chars`), a run past `WORKER_MAX_MB` (32 MB) is refused (413).

Build stages: `frama-build` (apt + `opam install frama-c.33.0 alt-ergo-free.2.4.3` + the licence gate; **8 min 48 s** from
scratch on pol-core), `frama-prune` (copies `bin/frama-c`, `lib/` without sources / bytecode / `.cmx` / `.a` / build-only packages,
`share/frama-c`, and Debian's OCaml stdlib dir likewise: **179 MB + 5.6 MB**), then the worker (CBMC layer unchanged; **39.9 s** with
the opam stages cached). The image carries the label `org.polari.engines` (the framework's local-image rung reads it, so an image
built before sc-2c is never handed an Mthread run). Frama-C puts its own libc (`share/libc`) and `share/mt` on the include path
BEFORE the harness's `-I`s, so `<stdint.h>` is Frama-C's, sized by the `avr_16` machdep (int 16, long 32, pointers 16, little endian).

**Measured (pol-core, 2026-10-02):** see `cost.json` (served in `/capability`'s `resources` block) and
`polari-framework/modules/firmwarefaults/COST.md` (sc-2b).
