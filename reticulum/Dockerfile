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
FROM python:3.12-slim

RUN pip install --no-cache-dir rns==0.9.4 lxmf==0.6.3

COPY sidecar.py /opt/sidecar.py
COPY lxmf_messaging.py /opt/lxmf_messaging.py
COPY config.template /opt/config.template

# Identity + RNS storage live in a named volume (no host bind mount —
# nothing root-owned ever lands on the host; the standing gotcha).
VOLUME /var/reticulum

EXPOSE 4242 4285
CMD ["python3", "/opt/sidecar.py"]
