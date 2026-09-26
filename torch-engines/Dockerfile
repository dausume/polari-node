# torch-engines — the PyTorch engine WORKER for tensormath (D5, 2026-09-26): ONE pinned CPU wheel behind one JSON API.
# glibc base on purpose: PyTorch publishes no musl wheel, so the Alpine framework image cannot carry it (as with z3) —
# the worker IS the way torch runs on the node stack. Licences: LICENSES.md (PyTorch BSD-3-Clause).
#
#   docker build -t polari-torch-engines:bookworm .
#   docker run --rm -p 9820:9820 polari-torch-engines:bookworm
FROM python:3.12-slim-bookworm
ARG TORCH_VERSION=2.14.0
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch==${TORCH_VERSION}" \
    && pip install --no-cache-dir falcon gunicorn psutil \
    && python -c "import torch; print('torch', torch.__version__)"
COPY torch_engines_service.py /srv/torch_engines_service.py
RUN useradd -m -u 1000 worker
USER worker
WORKDIR /srv
EXPOSE 9820
CMD ["gunicorn", "-b", "0.0.0.0:9820", "-w", "1", "-t", "300", "--chdir", "/srv", "torch_engines_service:app"]
