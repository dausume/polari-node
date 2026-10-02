# prf-formal-engines — the firmware FORMAL + STATIC tier's engines worker (sc-2b, AI-Notes/plans/FIRMWARE_SCENARIO_PLAN.md
# §5 tier 2, D-sc-6): bounded model checking and static rules on the firmware's C, from Debian 13 (trixie) packages only,
# on the SAME base the board engines pin by digest.
#
#   cbmc (+ goto-cc, goto-instrument)   BSD-4-clause style (diffblue/cbmc LICENSE: an ADVERTISING clause → GPL-incompatible to
#                                       LINK; run here as a SEPARATE PROCESS engine, never linked into Polari — plan §2d)
#   cppcheck 2.17 (+ its addons)        GPL-3.0 — the static rules engine; built-in checks + the threadsafety addon. The MISRA
#                                       addon is NOT run: its rule TEXTS need the non-free MISRA C document (--rule-texts)
#   python3 (a cppcheck dependency)     PSF — the two wrappers below and the worker
#   python3-falcon + gunicorn           Apache-2.0 / MIT — the /capability + /run worker (the board engines' protocol)
#
#   polari-cbmc-check   (polari_cbmc_check.py, ours)   goto-cc → goto-instrument (the ISR / volatile-read model) → cbmc --json-ui;
#                                                       writes result.json (verdict, properties, the counterexample trace,
#                                                       each step's wall + peak RSS from wait4) and trace.json
#   polari-cppcheck-run (polari_cppcheck_run.py, ours) cppcheck --xml over a project → findings.json (never fails a build)
#
#   docker build -t prf-formal-engines:trixie .
#   docker run --rm -p 9840:9840 prf-formal-engines:trixie                          # the worker
#   docker run --rm -v $PWD:/w -w /w prf-formal-engines:trixie cbmc --version       # one engine, CLI style
# Versions are printed at build time (the last RUN) and served by GET /capability.
ARG BASE=debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a

FROM ${BASE}
RUN apt-get update && apt-get install -y --no-install-recommends cbmc cppcheck python3-falcon gunicorn \
    && rm -rf /var/lib/apt/lists/* \
    && dpkg-query -W -f '${Package} ${Version}\n' cbmc cppcheck python3-falcon gunicorn
COPY polari_cbmc_check.py /usr/local/bin/polari-cbmc-check
COPY polari_cppcheck_run.py /usr/local/bin/polari-cppcheck-run
COPY formal_engines_service.py /srv/formal_engines_service.py
COPY cost.json /srv/cost.json
RUN chmod 0755 /usr/local/bin/polari-cbmc-check /usr/local/bin/polari-cppcheck-run \
    && useradd -m -u 1000 worker && cbmc --version && cppcheck --version
USER worker
WORKDIR /w
EXPOSE 9840
CMD ["gunicorn", "-b", "0.0.0.0:9840", "-w", "1", "-t", "900", "--chdir", "/srv", "formal_engines_service:app"]
