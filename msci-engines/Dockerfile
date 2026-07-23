# ==============================================================================
# msci-engines — the materials-science computation worker (its own module)
# ==============================================================================
# The prf-backend base image is Alpine/musl: pure-Python science libs ride
# there (scikit-fem, ASE), but every compiled-extension library (pyscf,
# pymatgen, sfepy) needs manylinux wheels — hence this Debian-based worker.
# The backend's materialsScience/engines/ delegate to it over HTTP when a
# capability isn't available locally (MSCI_ENGINES_URL knob).
#
# Quantum ESPRESSO is a BUILD-ARG KNOB (default off — ~1 GB):
#   docker compose build --build-arg WITH_QE=1 msci-engines
# ==============================================================================
FROM python:3.12-slim

ARG WITH_QE=0

# gcc/g++ only for sfepy's source build (no cp312 wheel); kept slim otherwise.
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends gcc g++ \
    && if [ "$WITH_QE" = "1" ]; then \
         apt-get install -y -qq --no-install-recommends quantum-espresso; fi \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        pyscf==2.13.1 pymatgen ase==3.24.0 scikit-fem==10.0.2 sfepy \
        falcon gunicorn

COPY engines_service.py /srv/engines_service.py
COPY darcy_solver.py /srv/darcy_solver.py
COPY structure_service.py /srv/structure_service.py

EXPOSE 9500
CMD ["gunicorn", "--chdir", "/srv", "--bind", "0.0.0.0:9500", \
     "--workers", "2", "--timeout", "300", "engines_service:app"]
