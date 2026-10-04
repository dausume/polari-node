# prf-pcb-engines — the PCB arc's engines worker (pcb-0, AI-Notes/plans/PCB_FROM_SCRATCH_PLAN.md §0/§6/§7): KiCad as the
# RELAY ENGINE — headless `kicad-cli` for ERC, DRC and the fabrication exports — on the SAME digest-pinned Debian 13 (trixie)
# base the board / formal engines use. Polari holds the design as rows and writes .kicad_sch/.kicad_pcb itself (D-pcb-1);
# this image only checks and exports. It never places or routes (a person does, D-pcb-2).
#
#   kicad 9.0.2+dfsg-1            GPL-3.0+ (Debian copyright: Files: * GPL-3+)          kicad-cli (sch erc / export netlist|bom|svg;
#                                                                                         pcb drc / export gerbers|drill|pos|svg|step)
#   kicad-symbols 9.0.2-1         CC-BY-SA-4.0 + the design exception                   the official symbol libraries
#   kicad-footprints 9.0.2-1      CC-BY-SA-4.0 + the design exception                   the official footprint libraries
#                                 (https://www.kicad.org/libraries/license/: the holder "waives article 3 of the license with
#                                 respect to these designs and any generated files" → our boards and Gerbers are free of the
#                                 share-alike; the libraries themselves stay CC-BY-SA when redistributed — this image does)
#   NOT kicad-packages3d (4.9 GB installed) — 3D models are fetched per footprint later (plan §4, the cost rule); a STEP
#   export without them carries the board body and pads only (kicad-cli warns per missing model).
#   libfaketime                   GPL-2.0+                                              REPRODUCIBLE OUTPUTS: kicad-cli stamps the wall clock into
#                                                                                         every Gerber (%TF.CreationDate, G04 … date), the drill file,
#                                                                                         the job file, SVG titles, the STEP header and the netlist, and
#                                                                                         ignores SOURCE_DATE_EPOCH (tried, 2026-10-03) — so /run takes
#                                                                                         `source_date` and runs kicad-cli under libfaketime with the
#                                                                                         clock FROZEN at it: the same inputs → the same bytes (measured)
#   python3-falcon + gunicorn     Apache-2.0 / MIT                                      the /capability + /run worker
#
# The display question (plan §6 "verify at pcb-0"): kicad-cli runs with NO X display and no Xvfb in this image — measured
# on isle-core 2026-10-03 (ERC, DRC, gerbers, drill, pos, svg, step all exit 0 with DISPLAY unset); xvfb is NOT installed.
#
#   tar -C prf-pcb-engines -cf - . | ssh isle-core 'docker build -t prf-pcb-engines:trixie -'
#   docker run -d --restart unless-stopped --name prf-pcb-engines -p 9860:9860 --memory 1024m prf-pcb-engines:trixie
#   docker run --rm -v $PWD:/w -w /w prf-pcb-engines:trixie kicad-cli version      # one engine, CLI style
# Versions are printed at build time (the last RUN) and served by GET /capability.
ARG BASE=debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a

FROM ${BASE}
RUN apt-get update && apt-get install -y --no-install-recommends \
        kicad=9.0.2+dfsg-1 kicad-symbols=9.0.2-1 kicad-footprints=9.0.2-1 libfaketime python3-falcon gunicorn \
    && rm -rf /var/lib/apt/lists/* /usr/share/doc/kicad/help \
    && dpkg-query -W -f '${Package} ${Version}\n' kicad kicad-symbols kicad-footprints libfaketime python3-falcon gunicorn
COPY pcb_engines_service.py /srv/pcb_engines_service.py
COPY cost.json /srv/cost.json
LABEL org.polari.engines="kicad-cli"
RUN useradd -m -u 1000 worker && kicad-cli version
USER worker
ENV KICAD9_SYMBOL_DIR=/usr/share/kicad/symbols KICAD9_FOOTPRINT_DIR=/usr/share/kicad/footprints \
    KICAD9_TEMPLATE_DIR=/usr/share/kicad/template
WORKDIR /w
EXPOSE 9860
CMD ["gunicorn", "-b", "0.0.0.0:9860", "-w", "1", "-t", "900", "--chdir", "/srv", "pcb_engines_service:app"]
