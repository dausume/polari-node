# prf-scope-engines — the oscilloscope emulator worker (osc-5)

The DHO804 emulator of `polari-framework/modules/instruments/custom/emulator.py` as an isle app: one JSON control API on **:9880** and up to
five raw SCPI sockets on **:5555-:5559** (one per emulated instrument), so another isle node reaches an emulated scope by address and port
exactly as it would a real one. Waveform DATA never crosses the control API — it is read over SCPI, like on a real instrument.

- `build.sh` stages `vendor/` from the framework module (one source of truth; `vendor/SOURCES.sha256` records what was vendored) and builds
  `prf-scope-engines:trixie`; `pol dev deploy scope-engines` runs it, pushes the image to the isle registry and installs the catalog tile.
- `docker-compose.scope-engines.yml` is the tile's compose (`isle app deploy scope-engines --compose … --service scope-engines --port 9880 --engine scope`).
- The backend resolves the worker through `instruments/custom/scope_engines.py`: `SCOPE_ENGINES_URL` → the `scope` EngineProviderBinding row →
  in-process. A declared worker that does not answer is a refusal, never a silent fallback.
- `cost.json` is the measured cost block served by `GET /cost` and `/capability` (see `modules/instruments/COST.md`).

See `AI-Notes/plans/OSCILLOSCOPE_PLAN.md` and the service docstring for the routes.
