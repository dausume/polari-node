# ==============================================================================
# cnt-engines — the microchip (cntfet) engine worker (dist-1, 2026-08-26)
# ==============================================================================
# One container carrying every engine the cntfet module shells out to,
# so a swarm worker (isle-core / econ-core) can run the compute while the
# core keeps the rows:
#   ngspice-46   (built from the pinned tarball; OSDI 0.3+0.4 loaders)
#   OpenVAF 23.5 (the glibc-2.35-safe binary; openvaf-reloaded needs
#                 glibc >= 2.36 — Ubuntu 22.04 base rules it out)
#   OpenSTA      (the openroad/opensta image IS the base — same binary
#                 the host's ~/.local/bin/sta wrapper runs)
#   kwant 1.5.0  (own venv, numpy<2 — the F3 NEGF worker, kwant_worker.py
#                 COPIED from the framework at build: one source of truth)
#
# Build context = polari-rf-node (compose sets it) so kwant_worker.py is
# copied from polari-framework/modules/cntfet/. Vendor inputs land in
# cnt-engines/vendor/ via ./cnt-engines/fetch-vendor.sh (gitignored):
#   vendor/ngspice.tar.gz  ngspice-46 source
#   vendor/openvaf         OpenVAF 23.5.0 x86_64 binary
#
#   docker compose -p cnt-engines -f docker-compose.cnt-engines.yml up -d --build
#   swarm: pol swarm deploy cnt-engines   (image shipped by docker save|load)
# ==============================================================================
FROM openroad/opensta

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
         python3 python3-pip python3-venv python3-dev \
         build-essential bison flex autoconf automake libtool \
         libopenblas-dev gfortran ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ngspice-46 from source (the host build's exact configure line).
COPY cnt-engines/vendor/ngspice.tar.gz /tmp/ngspice.tar.gz
RUN cd /tmp && tar xzf ngspice.tar.gz && cd ngspice-46 \
    && ./configure --prefix=/opt/ngspice --with-x=no --disable-debug \
         --with-readline=no --with-editline=no >/tmp/ngspice-configure.log \
    && make -j"$(nproc)" >/tmp/ngspice-make.log 2>&1 \
    && make install >/dev/null \
    && rm -rf /tmp/ngspice-46 /tmp/ngspice.tar.gz

COPY cnt-engines/vendor/openvaf /opt/openvaf/openvaf
RUN chmod +x /opt/openvaf/openvaf

# kwant in its own venv (numpy<2 — never touches the service python).
# kwant 1.5.0 ships as an sdist for cp310: its setup.py imports
# packaging + numpy + cython at metadata time, so stage those first and
# build without isolation (openblas/gfortran from apt above).
RUN python3 -m venv /opt/kwant-venv \
    && /opt/kwant-venv/bin/pip install --no-cache-dir -q \
         "pip<25" setuptools wheel packaging "cython<3.1" "numpy<2" \
    && /opt/kwant-venv/bin/pip install --no-cache-dir -q \
         --no-build-isolation "scipy<1.16" tinyarray==1.2.5 kwant==1.5.0

RUN pip3 install --no-cache-dir -q falcon gunicorn

COPY cnt-engines/cnt_engines_service.py /srv/cnt_engines_service.py
COPY polari-framework/modules/cntfet/kwant_worker.py /srv/kwant_worker.py

ENV PATH=/opt/ngspice/bin:/opt/openvaf:/OpenSTA/build:$PATH \
    CNT_ENGINES_WORKERS=3
RUN mkdir -p /var/cache/cnt-osdi

EXPOSE 9700
ENTRYPOINT []
CMD ["sh", "-c", "exec gunicorn --chdir /srv --bind 0.0.0.0:9700 --workers ${CNT_ENGINES_WORKERS} --timeout 3600 cnt_engines_service:app"]
