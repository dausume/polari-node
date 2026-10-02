# prf-formal-engines — the firmware FORMAL + STATIC tier's engines worker (sc-2b, AI-Notes/plans/FIRMWARE_SCENARIO_PLAN.md
# §5 tier 2, D-sc-6): bounded model checking, static rules and (sc-2c) Mthread's race analysis on the firmware's C, on the
# SAME digest-pinned Debian 13 (trixie) base the board engines use.
#
#   cbmc (+ goto-cc, goto-instrument)   BSD-4-clause style (diffblue/cbmc LICENSE: an ADVERTISING clause → GPL-incompatible to
#                                       LINK; run here as a SEPARATE PROCESS engine, never linked into Polari — plan §2d)
#   cppcheck 2.17 (+ its addons)        GPL-3.0 — the static rules engine; built-in checks + the threadsafety addon. The MISRA
#                                       addon is NOT run: its rule TEXTS need the non-free MISRA C document (--rule-texts)
#   frama-c 33.0 "Arsenic" (sc-2c)      LGPL-2.1-only — kernel + Eva + its Mthread plugin (open since 31.0). Debian has NO
#                                       frama-c package (only 25.0 in unstable, older than Mthread's open release —
#                                       AI-Notes/evaluations/FRAMA_C_EVALUATION.md §1), so stage `frama-build` builds it with
#                                       opam from a PINNED opam-repository commit and stage `frama-prune` keeps the runtime
#                                       closure only (the measured delta, ~0.3 GB, is under the plan's ~1.5 GB fold-in line —
#                                       hence ONE image, not a second one; COST in the module's COST.md)
#     why3 1.8.2 (frama-c's opam dep)   LGPL-2.1-only — linked by frama-c's WP plugin; WP is NOT wired (the evaluation: WP
#                                       leaves the byte identity Unknown), so no SMT prover is configured: no CVC5, no Z3
#     alt-ergo-free 2.4.3               CeCILL-C — frama-c 33.0's opam REQUIRES ("alt-ergo-free" | "alt-ergo"); the FREE one is
#                                       pinned; the build FAILS if alt-ergo / alt-ergo-lib / alt-ergo-parsers (all
#                                       LicenseRef-OCamlpro-Non-Commercial) ever appear. Its binary is not copied to the image
#   python3 (a cppcheck dependency)     PSF — the three wrappers below and the worker
#   python3-falcon + gunicorn           Apache-2.0 / MIT — the /capability + /run worker (the board engines' protocol)
#
#   polari-cbmc-check    (polari_cbmc_check.py, ours)    goto-cc → goto-instrument (the ISR / volatile-read model) → cbmc --json-ui;
#                                                        writes result.json (verdict, properties, the counterexample trace,
#                                                        each step's wall + peak RSS from wait4) and trace.json
#   polari-cppcheck-run  (polari_cppcheck_run.py, ours)  cppcheck --xml over a project → findings.json (never fails a build)
#   polari-mthread-check (polari_mthread_check.py, ours) frama-c -machdep avr_16 -eva -mthread → result.json (Mthread's last race
#                                                        report as data: per shared variable each access, thread, line, lock)
#
#   docker build -t prf-formal-engines:trixie .
#   docker run --rm -p 9840:9840 prf-formal-engines:trixie                          # the worker
#   docker run --rm -v $PWD:/w -w /w prf-formal-engines:trixie cbmc --version       # one engine, CLI style
# Versions are printed at build time (the last RUN) and served by GET /capability; the engines this image carries are in
# the label org.polari.engines (the framework's local-image rung reads it, so an older image without Mthread is not used for it).
ARG BASE=debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a
#: opam-repository HEAD on 2026-10-02 — fixes frama-c's whole dependency closure (dune, zarith, ocamlgraph, why3, …)
ARG OPAM_REPO_COMMIT=ac27950e5eac6c981ad809dff370c937820b7893
ARG FRAMA_C_VERSION=33.0
ARG ALT_ERGO_FREE_VERSION=2.4.3

# ---- stage 1: Frama-C from opam (ocaml-system = Debian's OCaml 5.3) ----
FROM ${BASE} AS frama-build
ARG OPAM_REPO_COMMIT
ARG FRAMA_C_VERSION
ARG ALT_ERGO_FREE_VERSION
ENV OPAMROOT=/opt/opam OPAMYES=1 OPAMCONFIRMLEVEL=unsafe-yes OPAMJOBS=3 OPAMNODEPEXTS=1
RUN apt-get update && apt-get install -y --no-install-recommends opam ocaml-nox build-essential git ca-certificates curl unzip \
        m4 pkg-config libgmp-dev zlib1g-dev autoconf graphviz \
    && rm -rf /var/lib/apt/lists/*
RUN opam init --bare --disable-sandboxing -n default "git+https://github.com/ocaml/opam-repository.git#${OPAM_REPO_COMMIT}" \
    && opam switch create fc ocaml-system \
    && opam install --switch fc "frama-c.${FRAMA_C_VERSION}" "alt-ergo-free.${ALT_ERGO_FREE_VERSION}"
# the licence gate: only the free Alt-Ergo may be in the switch
RUN opam list --switch fc --installed --columns=name,version,license: > /opt/opam/fc-packages.txt \
    && cat /opt/opam/fc-packages.txt \
    && if opam list --switch fc --installed --short | grep -qE '^alt-ergo(-lib|-parsers)?$'; then echo 'alt-ergo (NC) in the switch — refused' >&2; exit 1; fi \
    && opam show --switch fc alt-ergo-free --field=license | grep -q 'CECILL-C' \
    && /opt/opam/fc/bin/frama-c -version

# ---- stage 2: the runtime closure only — frama-c + the OCaml libraries its plugins load (.cmxs/.so/META) + share/frama-c
#      (its libc, mt/); no sources, build trees, docs, bytecode, compile-time interfaces or static archives; the same for
#      Debian's OCaml stdlib dir (findlib's path names it)
FROM frama-build AS frama-prune
RUN mkdir -p /opt/fc/bin /opt/fc/lib /opt/fc/share \
    && cp -a /opt/opam/fc/bin/frama-c /opt/fc/bin/ \
    && cp -a /opt/opam/fc/lib/. /opt/fc/lib/ && cp -a /opt/opam/fc/share/frama-c /opt/fc/share/ \
    && cp -a /usr/lib/x86_64-linux-gnu/ocaml /opt/ocaml-stdlib \
    && find /opt/fc/lib /opt/ocaml-stdlib \( -name '*.cmt' -o -name '*.cmti' -o -name '*.ml' -o -name '*.mli' -o -name '*.cmx' \
            -o -name '*.cma' -o -name '*.cmo' -o -name '*.a' -o -name '*.cmxa' -o -name '*.mld' -o -name '*.md' -o -name '*.tgz' \) -type f -delete \
    && rm -rf /opt/fc/lib/menhir* /opt/fc/lib/ppx_* /opt/fc/lib/ppxlib /opt/fc/lib/cppo* /opt/fc/lib/ocamlbuild \
              /opt/fc/lib/ocaml-compiler-libs /opt/fc/lib/frama-c-e-acsl /opt/fc/lib/frama-c-markdown-report /opt/fc/lib/alt-ergo* \
              /opt/ocaml-stdlib/5.3.0/compiler-libs /opt/ocaml-stdlib/5.3.0/ocamldoc \
    && du -sh /opt/fc /opt/ocaml-stdlib

# ---- the worker ----
FROM ${BASE}
RUN apt-get update && apt-get install -y --no-install-recommends cbmc cppcheck python3-falcon gunicorn \
    && rm -rf /var/lib/apt/lists/* \
    && dpkg-query -W -f '${Package} ${Version}\n' cbmc cppcheck python3-falcon gunicorn
# sc-2c: frama-c at the path it was built for (dune-site + findlib.conf name /opt/opam/fc); its runtime needs (cpp, libgmp10,
# zlib1g) are already here from the cbmc layer
COPY --from=frama-prune /opt/fc /opt/opam/fc
COPY --from=frama-prune /opt/ocaml-stdlib /usr/lib/x86_64-linux-gnu/ocaml
COPY --from=frama-prune /opt/opam/fc-packages.txt /srv/opam-packages.txt
COPY polari_cbmc_check.py /usr/local/bin/polari-cbmc-check
COPY polari_cppcheck_run.py /usr/local/bin/polari-cppcheck-run
COPY polari_mthread_check.py /usr/local/bin/polari-mthread-check
COPY formal_engines_service.py /srv/formal_engines_service.py
COPY cost.json /srv/cost.json
ENV PATH=/opt/opam/fc/bin:$PATH
LABEL org.polari.engines="cbmc-check cppcheck-run cbmc goto-cc goto-instrument cppcheck mthread-check frama-c"
RUN chmod 0755 /usr/local/bin/polari-cbmc-check /usr/local/bin/polari-cppcheck-run /usr/local/bin/polari-mthread-check \
    && useradd -m -u 1000 worker && cbmc --version && cppcheck --version && frama-c -version \
    && frama-c -plugins 2>&1 | grep -E '^Mthread'
USER worker
WORKDIR /w
EXPOSE 9840
CMD ["gunicorn", "-b", "0.0.0.0:9840", "-w", "1", "-t", "900", "--chdir", "/srv", "formal_engines_service:app"]
