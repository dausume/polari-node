# torch-engines — licence audit (D5, 2026-09-26)

A separate process behind a JSON API; nothing here is linked into Polari (GPL-3.0-or-later). Every component is a tool the
framework calls over HTTP.

| Component | Version / pin | Licence | Verified where | Verdict |
|---|---|---|---|---|
| PyTorch (CPU wheel) | `torch==2.14.0+cpu` from https://download.pytorch.org/whl/cpu (ARG TORCH_VERSION) | BSD-3-Clause | pytorch/pytorch LICENSE ("From PyTorch: Copyright (c) 2016- Facebook, Inc … BSD-style") | tool — fine; GPLv3-compatible |
| falcon, gunicorn, psutil (the worker's HTTP + system-info) | pip, latest at build | Apache-2.0 / MIT / BSD-3 | PyPI | fine |
| python:3.12-slim-bookworm base | Docker official image | PSF-2.0 (Python) + Debian | — | base image |

What the worker does NOT do: run arbitrary code (einsum only), touch the network, hold state. Operand size is capped
(TORCH_MAX_ELEMENTS).
