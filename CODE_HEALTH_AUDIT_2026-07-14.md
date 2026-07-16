# Code Health Audit — refresh (2026-07-14)

Supersedes/updates `CODE_HEALTH_AUDIT.md` (2026-06-23). Same scope: `polari-framework`
(Python) + `polari-platform-angular/src` (TS/HTML). Read-only audit — nothing here is
removed/refactored except where marked ✅ done. **Legend:** 🟢 confirmed low-risk ·
🟡 test-gated / risky · ⚖️ judgment call · ❌ ruled out (keep).

Four parallel sweeps: (A) backend file-size refresh, (B) frontend file-size refresh,
(C) **NEW** — object/interface placement discipline, (D) **NEW** — directory nesting vs.
view nesting. C and D are audits of two standards Dustin restated on 2026-07-14 that were
never checked before tonight.

Status of the 2026-06-23 audit: still nothing acted on except `newton-arrow-tip` removal
(§A of the old doc). This refresh does not repeat the old doc's §A (dead code) or §B
(duplicate code) findings — those are unchanged and still open; see the old doc.

---

## A. Backend — files over 1000 lines (refreshed)

| File | Current | Old (06-23) | Δ |
|---|---:|---:|---:|
| `polariNoCode/SolutionExecutionEngine.py` | 2731 | 1387 | **+1344** |
| `polariApiServer/polariServer.py` | 2493 | 1416 | **+1077** |
| `objectTreeManagerDecorators.py` | 2412 | 2415 | -3 |
| `materialsScience/standard_materials_seed.py` | 1932 | NEW | — |
| `simulations/seed_data.py` | 1704 | 1698 | +6 |
| `polariApiProfiler/apiProfilerAPI.py` | 1659 | 1651 | +8 |
| `simulations/simulation_api.py` | 1499 | NEW | — |
| `simulations/simulation_runner.py` | 1243 | 1179 | +64 |
| `polariApiServer/createClassAPI.py` | 1153 | 1145 | +8 |
| `polariApiProfiler/profileMatcher.py` | 1150 | 1150 | 0 |
| `polariDataTyping/polyTyping.py` | 1120 | 1106 | +14 |
| `polariApiProfiler/apiProfiler.py` | 1045 | 1045 | 0 |

**`SolutionExecutionEngine.py` and `polariServer.py` both grew 1000+ lines since June** —
they were already on the old decomposition list (§D of the old doc has proposed splits for
both) but are now the two most urgent targets, well past where they ranked in June.

**Two new entrants:**
- `materialsScience/standard_materials_seed.py` (1932) — five standard material families
  (sol-gel silica, geopolymer, technical ceramic/alumina, CNT, N-doped CNT) as full
  MaterialsScienceMaterial + MaterialScaleDefinition rows. Same shape as the already-flagged
  `seed_data.py` — a natural per-family split (`_helpers.py` + one file per family) follows
  the exact pattern already proven on the Newtonian pendulum extraction.
- `simulations/simulation_api.py` (1499) — several distinct HTTP route resources
  (runs/storage-prediction/future run-kickoff) in one file. Same shape as the already-
  proposed `apiProfilerAPI.py` split: resource classes keep their `on_*` methods, extract
  handler logic into separate modules.

Aside: tonight's own additions (aquaponics/mathshapes/simSpace modules) are all well under
1000 lines — largest is `shape_geometry.py` at 694. No concern there.

---

## B. Frontend — files over 1000 lines (refreshed)

| File | Current | Old (06-23) | Δ |
|---|---:|---:|---:|
| `components/custom-no-code/custom-no-code.ts` | 5396 | 5263 | +133 |
| `components/class-main-page/class-main-page.ts` | 3084 | 3058 | +26 |
| `models/noCode/d3-extensions/CircleStateLayer.ts` | 2318 | 2318 | 0 |
| `services/no-code-services/no-code-solution-state.service.ts` | 2031 | 2031 | 0 |
| `components/custom-no-code/states/_shared/state-space-class-registry.ts` | 2015 | 1789 | **+226** |
| `components/class-main-page/class-main-page.html` | 1797 | 1792 | +5 |
| `models/noCode/d3-extensions/RectangleStateLayer.ts` | 1519 | 1519 | 0 |
| `models/noCode/d3-extensions/DiamondStateLayer.ts` | 1409 | 1409 | 0 |
| `components/api-profiler/api-profiler.component.html` | 1321 | 1321 | 0 |
| `components/api-profiler/api-profiler.component.ts` | 1197 | 1197 | 0 |
| `components/sim-space/sim-space-viewer/sim-space-viewer.component.ts` | 1120 | — | **NEW** |
| `components/templateClassTable/class-data-table/class-data-table.ts` | 1078 | 1078 | 0 |

**New entrant:** `sim-space-viewer.component.ts` (1120) just crossed 1000 tonight — the
shared 2D/3D renderer dispatcher (renderer factory, snapshot loading, temporal/scrubber
state, XR scene registration, evaluation-overlay wiring). Not urgent yet, but it's
accreted several concerns; watch it, and extract a service (temporal/scrubber state is the
obvious first seam) before it grows further.

**Six files from tonight's own IC-panel work, explicitly checked — none over 1000:**

| File | Lines |
|---|---:|
| `sim-space-initial-conditions-panel.component.ts` | 350 |
| `sim-space-simulation-run-panel.component.ts` | 777 |
| `sim-space-viewer.component.ts` | 1120 ⚠️ (see above) |
| `sim-space-editor-sidebar.component.ts` | 908 |
| `pot-geometry-editor.component.ts` | 497 |
| `math-shape-geometry-library.service.ts` | 106 |

---

## C. NEW — Object/interface placement discipline

Standard (restated 2026-07-14): objects/interfaces live in their designated definition
sections — frontend `models/<domain>/`, backend a real class/treeObject in its own
definition file — never scattered inline inside logic files. This is already the
documented convention in `GOD_FILE_SPLIT_PLAN.md` §0 ("models/<domain>/ — the data...
services/<domain>/ — the behavior"); this is the first time it's been *audited* against
actual code rather than just stated.

**Headline finding: this is not sporadic — it's the dominant pattern outside `sim-space`.**
`sim-space` is the one domain that actually follows its own convention (a real
`models/sim-space/types/*.ts` barrel — the reference template cited in the god-file plan).
Every other domain checked — scoring, materials-science, topology, api-profiler,
multi-scale, no-code-services — consistently keeps its own interfaces inline inside the
service file that uses them. **130+ `export interface` declarations across
`services/**/*.service.ts`, another 104 inside `components/**/*.component.ts` (unscanned —
see scope note).**

### Clear violations — meaningful cross-file data shapes, inline, reused by ≥2 files
- 🟢 `services/scoring/accountability.service.ts` — **17** exported interfaces
  (SubjectRow, ElectionRow, PolicyScoreReport, PoliticianScoreReport, SurvivalReport…).
  No `models/scoring/` exists at all.
- 🟢 `services/materials-science/materials-basis.service.ts` — **14** exported interfaces
  (MaterialDetail, PresenceMatrix, DetailLevel, BlendEffect…), each reused by ≥2
  component files. No `models/materials-science/`.
- 🟢 `services/topology/topology.service.ts:8-152` — 13 interfaces (TopologyGraph,
  ValidateReport, DriftReport, AssignResult…), all inline.
- 🟢 `services/api-profiler.service.ts:19-250` — 12 interfaces (APIProfile ×2 files,
  ProfileMatch ×2, APIDomain ×2, APIEndpoint ×2, FormatAnalysis…).
- 🟡 `services/sim-space/simulation-run.service.ts:35-174` — 11 interfaces
  (SimulationRunSummary reused in 5 files, SimulationStepResult in 3,
  InitialConditionsForClass/StorageEstimate in 2 each). **Notable: this IS inside the
  sim-space domain**, which otherwise has a proper `models/sim-space/` — this one file
  breaks its own domain's convention. Highest-symbolic-value fix (proves the pattern,
  touches the reference-template domain).
- 🟢 `services/multi-scale/msim-authoring.service.ts` + 6 sibling `msim-*.service.ts`
  files — each defines its own 2-4 interfaces (StageGateVerdict, IcInterfaceConfig,
  CouplingConfig…), no shared `models/multi-scale/`.
- 🟢 `services/runtime-config.service.ts:24-72` — 7 interfaces defining the **whole app's
  runtime config shape** (BackendConfig, KeycloakConfig, RuntimeConfig) — used app-wide,
  arguably the single most cross-cutting shape in the entire frontend, and it's entirely
  undomained.
- 🟢 `services/materials-science/engine-model.service.ts:11-65` — `EngineTemplate`,
  reused in **6 files** — the widest reuse found in this sweep — inline in a service.

### Borderline — legitimate to keep local (not violations)
- ⚖️ `services/sim-space/sim-space-binding.service.ts:25` `interface BindingRow`,
  `services/sim-space-3d/math-shape-geometry-library.service.ts:25`
  `interface ShapeSurfaceResponse` — both unexported, single-file-scoped wire shapes.
- ⚖️ `no-code-solution-state.service.ts:32` `SolutionCache`,
  `state-definition.service.ts:32` `StateDefinitionCache` — internal cache shapes, not
  domain data.

### Backend
- ❌ `simSpace/sim_space_api.py` — **good, no violation**: real `_row_to_summary` /
  `_row_to_payload` serializer methods, single source of truth. This is the model to copy.
- 🟡 `simulations/simulation_api.py` — **71** inline `response.media = {...}` dict
  literals, no serializer helper found anywhere in the file.
- 🟡 `polariApiProfiler/apiProfilerAPI.py` — same pattern, **85** inline dicts, no
  serializer helper.

### Scope not yet covered (flagging, not claiming clean)
- 104 `interface` hits inside `components/**/*.component.ts` — unscanned this pass.
- Deeper backend spot-checks beyond the 5 files above (this was a first-pass sweep, capped
  at 25 findings by design).

---

## D. NEW — Directory nesting vs. view nesting

Standard (restated 2026-07-14): where components nest visually (a panel inside a viewer
inside a page), the file tree should nest the same way. Exemplar Dustin already validated
tonight: `components/sim-space/sim-space-viewer/` holds
`sim-space-simulation-run-panel.component.ts`, `sim-space-initial-conditions-panel.component.ts`,
`sim-space-editor-sidebar.component.ts`, etc. — all siblings under the viewer that hosts
them.

**Scope note:** deep-checked `custom-no-code`, `class-main-page`, `api-profiler` by real
import graph (excluding `app.module.ts` bootstrap registration, which isn't real view
usage). `multi-scale`, `materials-science`, `dashboard` **not yet checked** — same pass
still needed there.

### Dumping-ground directory
`components/shared/` (~15 sub-components) is the textbook version of this anti-pattern —
but per-component checking shows it's a MIX, not uniformly bad:

- ❌ `KatexDisplayComponent` — 6 distinct consumers (matrices/sim-space/templateClassTable)
  → genuinely shared, correctly placed.
- ❌ `ClassSelectorDialogComponent` — 3 distinct consumers → correctly placed.
- ❌ `EquationSelectorDialogComponent` — 3 distinct consumers → correctly placed.

### Firm violations — single (or overwhelmingly single) consumer, filed elsewhere

| Parent (real consumer) | Misplaced child | Current | Suggested |
|---|---|---|---|
| `templateClassTable/class-data-table` | `CalendarViewDialogComponent` | `components/shared/calendar-view-dialog/` | `components/templateClassTable/class-data-table/calendar-view-dialog/` |
| `templateClassTable/class-data-table` | `CrudDialogComponent` | `components/shared/crud-dialog/` | `components/templateClassTable/class-data-table/crud-dialog/` |
| `dashboard/dashboard-renderer` | `ScreenSupportNoticeComponent` | `components/shared/screen-support-notice/` | `components/dashboard/dashboard-renderer/screen-support-notice/` |
| `components/matrices` (2 matrices-only files) | `MatrixGridCellComponent` | `components/shared/matrix-grid-cell/` | `components/matrices/matrix-grid-cell/` |
| `class-main-page.ts` (only real consumer) | entire `dataset-config/` (5 dialogs/sidebar) | top-level `components/dataset-config/` | `components/class-main-page/dataset-config/` |
| `class-main-page.ts` (only real consumer) | entire `table-config/` | top-level `components/table-config/` | `components/class-main-page/table-config/` |

### Borderline (2 consumers — judgment call, not firm violations)
- ⚖️ `InstancePickerDialogComponent` (shared/) — used by `display-config` + `templateClassTable`.
- ⚖️ `LatexEditDialogComponent` (shared/) — used by 2 matrices-family sites.
- ⚖️ `geojson-config/` — used by `class-main-page` + `templateClassTable/class-data-table`.

### Correctly placed (checked, confirmed clean)
- ❌ `graph-config/` — 5 distinct consumers across class-main-page/dashboard/3×multi-scale
  → genuinely cross-cutting, correctly top-level.
- ❌ `sim-space/sim-space-viewer/` — the exemplar itself; no issues.

### Inconclusive — needs a different check before concluding anything
- `DetailDisplayRendererComponent`, `MapPointPickerDialogComponent`,
  `RowActionsCellComponent` — no static `.ts` importer found outside `shared/`. Either
  dead code, or consumed dynamically (a component registry / `*ngComponentOutlet`) rather
  than statically imported — grep can't see that. Cross-reference against the existing
  dead-code list (old audit §A) before acting.
- `display-config/` — only an `app.module.ts` hit; likely registry-driven, same caveat.

---

## Prioritized punch list

Highest-value / lowest-risk first, per Dustin's own stated preference for reviewing before
mass-refactoring big working files:

1. **§C, `simulation-run.service.ts`** — pull its 11 interfaces into `models/sim-space/`.
   Small, mechanical, and it's inside the ONE domain that already has the right structure —
   this is the cheapest, clearest proof-of-pattern fix, and current wrongness there
   actively undermines the "sim-space is the reference template" claim.
2. **§C, `runtime-config.service.ts`** — 7 interfaces defining app-wide config, currently
   undomained. High cross-cutting value, low risk (types-only move).
3. **§A backend, `SolutionExecutionEngine.py` + `polariServer.py`** — both grew 1000+
   lines since June and are now the most urgent backend decomposition targets (proposed
   splits already exist in the old doc §D — just re-prioritize them to the top).
4. **§D, the two `class-main-page` directories** (`dataset-config/`, `table-config/`) —
   single-consumer, straightforward moves, no logic changes.
5. **§C, the six large-interface-count services** (scoring, materials-science, topology,
   api-profiler, multi-scale) — bigger lift, same mechanical shape each time: extract to
   `models/<domain>/`, service imports from there instead. Good candidates to batch once
   the pattern is proven on #1.
6. **§C backend, `simulation_api.py` + `apiProfilerAPI.py` serializer gap** — no
   serializer helper exists for either (71 and 85 inline dicts respectively); adding one
   (mirroring `sim_space_api.py`'s `_row_to_payload` pattern) is the fix, but higher-risk
   since it touches every response shape in two big files — do this with the same care as
   any other backend god-file split (py_compile + selftest gate).

**Not yet covered — flag for a follow-up sweep, don't assume clean:**
- §C: 104 inline interfaces inside `components/**/*.component.ts`, unscanned.
- §D: `multi-scale`, `materials-science`, `dashboard` view-nesting, unscanned.
- §D: the 4 inconclusive "dynamically consumed?" components — resolve before deciding
  dead-code vs. misplaced.
