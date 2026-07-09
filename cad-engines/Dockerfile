# ==============================================================================
# cad-engines — the CAD import/export worker (mirrors msci-engines)
# ==============================================================================
# The prf-backend base image is Alpine/musl: trimesh (STL/OBJ/GLTF + the
# three.js JSON export) rides there, but FreeCAD (OpenCASCADE, a large C++
# stack) + STEP toolkits need a Debian userland — hence this worker. The
# backend's mathshapes/cad_remote delegates over HTTP (CAD_ENGINES_URL).
#
# FreeCAD is a BUILD-ARG KNOB (default off — ~1.5 GB of OpenCASCADE):
#   docker compose build --build-arg WITH_FREECAD=1 cad-engines
# ==============================================================================
FROM python:3.12-slim

ARG WITH_FREECAD=0

# trimesh needs no compiler for the baseline; FreeCAD comes from apt when
# the build-arg is set (freecad ships FreeCAD/Part/Mesh python modules).
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
       libgl1 libglu1-mesa \
    && if [ "$WITH_FREECAD" = "1" ]; then \
         apt-get install -y -qq --no-install-recommends freecad; fi \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        "trimesh[easy]==4.5.3" \
        falcon gunicorn

COPY cad_service.py /srv/cad_service.py

# FreeCAD's python modules live outside site-packages — expose them.
ENV PYTHONPATH=/usr/lib/freecad/lib:/usr/lib/freecad-python3/lib:/usr/share/freecad/Mod

EXPOSE 9600
CMD ["gunicorn", "--chdir", "/srv", "--bind", "0.0.0.0:9600", \
     "--workers", "2", "--timeout", "300", "cad_service:app"]
