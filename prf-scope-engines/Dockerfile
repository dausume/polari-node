# prf-scope-engines — the oscilloscope EMULATOR worker (osc-5, AI-Notes/plans/OSCILLOSCOPE_PLAN.md §3f): the DHO804 emulator from
# polari-framework/modules/instruments/custom (vendored by build.sh into vendor/ — one source of truth, never a copy kept by hand)
# behind one JSON control API (:9880) and five raw SCPI sockets (:5555-:5559), on the same digest-pinned Debian 13 base as the other
# engine workers. Only Debian packages:
#
#   python3-numpy                   BSD-3                 the sample arrays
#   python3-falcon + gunicorn       Apache-2.0 / MIT      the control API
#
#   prf-scope-engines/build.sh                                      # stages vendor/ then docker build -t prf-scope-engines:trixie
#   docker run --rm -p 9880:9880 -p 5555-5559:5555-5559 prf-scope-engines:trixie
# Versions are printed at build time (the dpkg-query RUN) and served by GET /capability.
ARG BASE=debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a
FROM ${BASE}
RUN apt-get update && apt-get install -y --no-install-recommends python3-numpy python3-falcon gunicorn \
    && rm -rf /var/lib/apt/lists/* \
    && dpkg-query -W -f '${Package} ${Version}\n' python3-numpy python3-falcon gunicorn python3
COPY vendor/ /srv/vendor/
COPY scope_engines_service.py cost.json /srv/
ENV SCOPE_VENDOR=/srv/vendor SCOPE_SCPI_HOST=0.0.0.0 SCOPE_SCPI_PORT_FIRST=5555 SCOPE_SCPI_PORTS=5
WORKDIR /srv
EXPOSE 9880 5555 5556 5557 5558 5559
# one worker process: the emulators are threads of it (their state is per process); gthread so a long SCPI read never blocks the control API
CMD ["gunicorn", "--bind", "0.0.0.0:9880", "--workers", "1", "--threads", "4", "--timeout", "120", "scope_engines_service:app"]
