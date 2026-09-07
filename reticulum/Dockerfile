# ==============================================================================
# pol-reticulum — the Reticulum mesh sidecar (ret-2, RETICULUM_TRANSPORT_PLAN)
# ==============================================================================
# THE LICENCE BOUNDARY LIVES HERE (RETICULUM_LICENCE_GATE.md, 2026-08-13):
# rns 0.9.4 + lxmf 0.6.3 are the LAST MIT RELEASES — later versions carry a
# restricted, GPLv3-INCOMPATIBLE licence (field-of-use clauses added
# 2025-04-15). The pins below are licence pins, never `>=`; bumping them
# requires re-running the gate. RNS is imported ONLY in this image — the
# Polari backend holds rows about the mesh and talks to this sidecar over
# HTTP; the process boundary is the licence boundary.
#
# SOURCE = OUR FORKS (2026-09-07, Dustin's go): dausume/reticulum and
# dausume/lxmf, frozen at the last MIT commits (Reticulum 0.9.4 =
# bfe5b876…, LXMF 0.6.3 = 326c0eed…) with POLARI-FORK.md stating the
# rules — no upstream merges ever. Pinned by COMMIT (never a branch);
# GitHub archive tarballs need no git in the image. rns is installed
# first so lxmf's "rns>=0.9.3" resolves to it instead of PyPI.
FROM python:3.12-slim

ARG RNS_FORK_COMMIT=40a1edc6953ab58f7a6f60268e33faeb4a826dde
ARG LXMF_FORK_COMMIT=d67419288397a535c084fcfc55835dcc0b506502
RUN pip install --no-cache-dir \
        "https://github.com/dausume/reticulum/archive/${RNS_FORK_COMMIT}.tar.gz" \
    && pip install --no-cache-dir \
        "https://github.com/dausume/lxmf/archive/${LXMF_FORK_COMMIT}.tar.gz" \
    && python3 -c "import RNS, LXMF; assert RNS.__version__ == '0.9.4', RNS.__version__; print('rns', RNS.__version__, 'lxmf', LXMF.__version__)"

COPY sidecar.py /opt/sidecar.py
COPY lxmf_messaging.py /opt/lxmf_messaging.py
COPY config.template /opt/config.template

# Identity + RNS storage live in a named volume (no host bind mount —
# nothing root-owned ever lands on the host; the standing gotcha).
VOLUME /var/reticulum

EXPOSE 4242 4285
CMD ["python3", "/opt/sidecar.py"]
