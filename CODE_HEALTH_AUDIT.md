# Code Health Audit — checklist (2026-06-23)

Scope: `polari-framework` (Python) + `polari-platform-angular/src` (TS/HTML). Read-only
audit — nothing here is removed/refactored except where marked ✅ done. Tick items as
you decide. **Legend:** 🟢 confirmed low-risk · 🟡 test-gated / risky · ⚖️ judgment call ·
❌ ruled out (keep).

**Verification status:** Dead-code items were re-verified by me (caught 2 agent false
positives). Duplicate-code line numbers are agent-reported and *credible* but should be
re-confirmed before refactoring. File sizes are exact (`wc -l`).

---

## A. Dead code

- [x] ✅ **`newton-arrow-tip`** matrix equation — `matrices/seed_data.py`. Removed +
      verified (selftest 11/11). Orphaned by the State-Projection migration.
- [ ] 🟢 **`equations.component.ts`** — `operationLabel()` (line 117) + `operationLabelForId`
      (line 36) + the imports they orphan (`EQUATION_OPERATION_LABELS`, `EquationOperationType`,
      lines 7–8). Unused in `.ts` + template. *(the `operationLabels` hits elsewhere are a
      different component.)*
- [ ] 🟢 **`matrices/matrices-page/matrices-page.component.ts`** — 4 accessors
      `elementwiseBindingsText` / `setElementwiseBindings` / `operandsText` / `setOperands`
      (lines 257–274). Zero refs in any `.ts` or `.html`.
- [ ] 🟢 **`polariAnalytics/functionalityAnalysis.py`** — lines ~49–153, a bare `"""…"""`
      block of commented-out functions (a no-op string). Pure dead weight.
- [ ] 🟡 **`utils/stepping.ts`** (whole file: `stepCheckpoint` + `StepConfig` + `StepRecord`).
      No TS importer — the only hits are the Python-code-generator emitting Python import
      *strings*. Looks dead, but it's the **step/stop execution machinery** — verify with a
      checkpoint run first. **HOLD until testing.**
- [ ] ⚖️ **`polariNetworking/managedIsoNetwork.py`** — class referenced only by its own test
      (`managedIsoNetworkTest.py`); no production import; needs `scapy`. Intentional infra or
      abandoned? Removing it + its test is your call.
- ❌ **`setOperators.py`** — KEEP. Listed in `moduleService/moduleDiscovery.py:212` (module
      registry); the 3 functions are unused stubs but the module is referenced.
- ❌ **`polariNetworking/managedSink.py` / `SinkAdapter`** — KEEP. Actually used at
      `polariServer.py:547` (`sink = SinkAdapter()`). (Agent false positive.)

---

## B. Duplicate code

- [ ] 🟢 **D3 StateLayer shape classes ×3** — `models/noCode/d3-extensions/CircleStateLayer.ts`
      (2318) · `RectangleStateLayer.ts` (1519) · `DiamondStateLayer.ts` (1409). **~85–90%
      identical** — connector mgmt, collision detection (`getAllStateBoundingBoxes`,
      `resolveCollision`, `getNearestTangentDirection`), slot placement, and the full drag
      cluster (`onDragStateStart/State/StateEnd`, ~470 lines) are near-byte-identical; only
      shape geometry/size differs. **~2100 duplicated lines.** → extract
      `AbstractStateLayer<T>` base with shape-specific `generateShapePath()`/`getStateSize()`
      hooks (~2100 → ~600). **Highest-ROI refactor.** *Confidence: very high. Re-confirm exact
      line ranges first.*
- [ ] 🟢 **`compile_2d.py` vs `compile_3d.py`** (`simSpace/compilers/`) — explicitly
      "symmetric." The run-filter + binding-iteration/dispatch scaffold, `_emit_connections`,
      `_emit_instances`, and `_resolve_position_2d/3d` are ~85–95% identical (~150 dup lines).
      → shared `compile_common(... dim)` + a per-dim defaults config + one
      `_resolve_position(..., dim)`. *Confidence: high (I've read compile_3d — symmetry is
      real). Note: `_emit_vectors` is 3D-only (the new State-Projection path) — leave it.*
- [ ] ⚖️ **`*SimState` `__init__` boilerplate** — `pendulum_bob_sim_state.py`,
      `newtonian_pendulum_bob_sim_state.py`, `pendulum_string_sim_state.py`: mechanical
      `self.x = x` blocks + parallel `field_save_policy`/`default_initial_field_values` dicts
      (~45 lines). → a base that auto-generates `__init__` from type hints + policy dicts.
      *Low impact + the one-`__init__`-per-class convention was a deliberate fix for field
      discovery (see newtonian memory) — verify a generated `__init__` still registers fields
      before adopting.*
- ❌ **`apiProfiler.py` / `apiProfilerAPI.py` / `profileMatcher.py`** — checked, **no major
      duplication** (complementary concerns, not parallel impls).

---

## C. Files over 1000 lines (decomposition targets)

**Backend — Python**
- [ ] 2415 `objectTreeManagerDecorators.py`
- [ ] 1698 `simulations/seed_data.py` *(already partly decomposed — Newtonian extracted)*
- [ ] 1651 `polariApiProfiler/apiProfilerAPI.py`
- [ ] 1416 `polariApiServer/polariServer.py`
- [ ] 1387 `polariNoCode/SolutionExecutionEngine.py`
- [ ] 1179 `simulations/simulation_runner.py`
- [ ] 1150 `polariApiProfiler/profileMatcher.py`
- [ ] 1145 `polariApiServer/createClassAPI.py`
- [ ] 1106 `polariDataTyping/polyTyping.py`
- [ ] 1045 `polariApiProfiler/apiProfiler.py`

**Frontend — TS / HTML**
- [ ] 5263 `components/custom-no-code/custom-no-code.ts` *(known god-component; dispatch/
      overlays/nav/codegen/versions/STOMP all in one)*
- [ ] 3058 `components/class-main-page/class-main-page.ts`
- [ ] 2318 `models/noCode/d3-extensions/CircleStateLayer.ts` *(→ shrinks via B.1)*
- [ ] 2031 `services/no-code-services/no-code-solution-state.service.ts`
- [ ] 1792 `components/class-main-page/class-main-page.html`
- [ ] 1789 `components/custom-no-code/states/_shared/state-space-class-registry.ts`
- [ ] 1519 `models/noCode/d3-extensions/RectangleStateLayer.ts` *(→ shrinks via B.1)*
- [ ] 1409 `models/noCode/d3-extensions/DiamondStateLayer.ts` *(→ shrinks via B.1)*
- [ ] 1321 `components/api-profiler/api-profiler.component.html`
- [ ] 1197 `components/api-profiler/api-profiler.component.ts`
- [ ] 1078 `components/templateClassTable/class-data-table/class-data-table.ts`

**Cross-links:** B.1 (StateLayer dedup) directly shrinks three of the C files. The
StateLayer + compile_2d/3d refactors are the two that improve *both* size and duplication
at once — best first targets when you greenlight refactoring.

---

## Notes on sequencing (when you greenlight)
1. **Dead code 🟢 (A2–A4)** — smallest, safest; backend via `py_compile` + selftests, frontend
   via careful grep re-check (no local `ng build`).
2. **B.1 StateLayer base** — highest payoff, but frontend-only and unverifiable locally →
   best done where `./rebuild-staging.sh` can confirm between steps.
3. **B.2 compile_2d/3d** — backend, selftest-coverable locally; lower risk.
4. **A5 (stepping.ts)** + **B.3 (SimState init)** — gated on a checkpoint/seed test.

---

## D. Decomposition proposals (for the §C oversized files)

Pattern throughout: a **thin orchestrator** (component/class/resource that keeps the
public API + template/route bindings) delegating to **cohesive modules by concern**
(injectable services on the frontend; helper modules/packages on the backend),
one-directional imports, no cycles. Frontend splits **can't be `ng build`-verified
locally** → do them where `./rebuild-staging.sh` can confirm between steps. Backend
splits are `py_compile`/selftest-checkable locally. Targets keep every file < ~900.

### No-code editor
- [ ] **`custom-no-code.ts` (5263)** — 15 seams → injectable services: `canvas-manager`
      (~250), `solution-loading` (~300), `overlay-dispatch` (~350) + `state-overlay-factories`
      (~400), `context-resolution` (~200), `code-generation-orchestrator` (~150),
      `version-management` (~100), `execution-panel` (~100), `context-menu-action-handler`
      (~200), `popup-coordinator` (~350), `rendering-debug` (~80); component → ~400–500.
      **Caution:** the stateClass→overlay registry dispatch is load-bearing; the component
      must still expose every template-referenced method.
- [ ] **`no-code-solution-state.service.ts` (2031)** → `solution-cache`, `-backend-sync`,
      `-data`, `-serialization`, `flow-context` (~800, the dataflow tracer), `-factory`,
      `-discovery`, `form-validation`; facade → ~300–400. **Caution:** the public `*$`
      BehaviorSubjects + `backendIdMap` must stay on the facade.
- [ ] **`state-space-class-registry.ts` (1789)** → move the ~1250 lines of `registerClass()`
      calls into `registries/{initial-state,end-state,control-flow,data-transform,
      runtime-specific}.registry.ts`; main keeps the singleton store + getters + solution
      registration (~400–500); shared types → `.types.ts`. **Caution:** must remain ONE
      singleton — sub-registries feed the same `classes` Map; factories are closures.

### Class-browser / profiler UI
- [ ] **`class-main-page.ts` (3058)** → per-domain facade services (`display-editor`,
      `table-config`, `graph-config`, `geojson-config`, `dataset-config`, `button-appearance`)
      + a `display-grid-layout-editor` child component; orchestrator → ~1200. **Caution:**
      keep displayManager subscriptions + two-way-bound objects in the component.
- [ ] **`class-main-page.html` (1792)** → extract list/editor pairs per config type into
      child components (`table-config-list`/`-editor`, `graph-*`, `display-config-editor`,
      `form-*`, `button-*`, `class-config-section`); template → ~400.
- [ ] **`class-data-table.ts` (1078)** → `table-crud-dialog`, `-action-button-executor`,
      `-geometry-preview` services; core table → ~650. **Caution:** dataSource mutation
      stays in the component (change detection).
- [ ] **`api-profiler.component.ts` (1197)** → services `query-executor`, `api-analyzer`,
      `profile-builder`, `class-creator`, `api-endpoint-manager`, `api-response-formatter`;
      orchestrator → ~500.
- [ ] **`api-profiler.component.html` (1321)** → child components per tab section
      (`api-query-form`, `api-response-viewer`, `api-format-analysis`, `profile-matches-viewer`,
      `endpoint-form`, `endpoints-list`, `class-creation-form`); template → ~200.

### Backend core
- [ ] **`objectTreeManagerDecorators.py` (2415)** → `polariObjectStore` (DB jumpstart/restore/
      persist/seed-ids ~450), `polariObjectTree` (tree data + traversal ~850),
      `polariObjectMutation` (~380), `polariObjectQuery` (~200), `polariObjectSerialize` (~350);
      keep `managerObjectInit`/decorator + bootstrap (~200). **Caution:** decorator stays the
      entry point; `getInstanceIdentifiers` stays in tree + imported widely; no circular
      imports during manager `__init__`.
- [ ] **`polariDataTyping/polyTyping.py` (1106)** → `polyTypedObjectCore`, `…Analysis` (~350),
      `…Schema`, `…Serialization`, `…Reflection`, `polyFieldProfile`; main re-exports
      `polyTypedObject`. **Caution:** `getCreateMethod()` is used early in DB restore — no
      reverse deps.
- [ ] **`polariApiServer/createClassAPI.py` (1145)** → `createClassAPIHandlers` (HTTP),
      `createClassCore` (factory/edit ~280), `createClassPersistence` (registry restore/repair
      ~380), `createClassDeletion`; router → ~50. **Caution:** the `exec()`-built `__init__`
      signature must match `treeObjectInit`; static restore/repair are called from polariServer
      init — no cycles.
- [ ] **`polariApiServer/polariServer.py` (1416)** → `polariServerBootstrap` (CORS/Falcon),
      `…Definitions` (defClassList + CRUDE), `…Modules`, `…Seeds` (orchestration) +
      `…SeedHelpers` (per-domain seed methods), `…TileStore` (MBTiles), `…DynamicCRUDE`; main
      keeps init/lifecycle. **Caution:** seed execution ORDER is critical; `defClassList`
      completeness invariant; modules load before seeding.

### Backend execution + profiler
- [ ] **`polariNoCode/SolutionExecutionEngine.py` (1387)** → `operand_resolution` (~300),
      `condition_evaluators` (~150), a `state_handlers/` package (split the ~30-branch
      `_evaluate_state` by family: entry / control-flow / operations / simulation),
      `graph_traversal` (~100); engine keeps `execute()` + the loop. **Caution:** keep dispatch
      as a registry so `execute()` stays stable.
- [ ] **`simulations/simulation_runner.py` (1179)** → `step_orchestrator` (`run_step` ~200),
      `step_dependencies` (topo-sort), a `step_operations/` package (`contribution_merge`,
      `context_projection`, `initial_conditions`), `data_access`. **Caution:** `run_step` is the
      public contract; keep its run-filter predicate consistent with compile_2d/3d + the eval.
- [ ] **`polariApiProfiler/apiProfilerAPI.py` (1651)** → extract logic into
      `profiler_query_handler`, `profiler_match_handler`, `endpoint_fetch_handler`; the resource
      classes keep their `on_*` methods and delegate. **Caution:** Falcon routes `on_*` by class
      — don't move them off the resource.
- [ ] **`polariApiProfiler/profileMatcher.py` (1150)** → `structure_analyzer`,
      `signature_matcher` (~350), `match_ranker`; `ProfileMatcher` → thin facade.
- [ ] **`polariApiProfiler/apiProfiler.py` (1045)** → `http_client` (~250), `structure_analyzer`,
      `type_detector`, `polari_type_mapper`, `data_extraction`; `APIProfiler` facade holds the
      manager ref. **Caution:** dynamic class creation (`exec`/`type`) stays near manager init.

### Already-known (handled outside the agent sweep)
- [ ] **`simulations/seed_data.py` (1698)** → `simulations/seed/_helpers.py` (shared builders,
      imported by this + `newtonian_pendulum_seed.py`), `pendulum_2d_seed.py`,
      `pendulum_3d_seed.py`; `seed_data.py` stays the thin `SEED_PENDULUM_*` list aggregator the
      sub-seeds `.extend()` — the exact pattern the Newtonian extraction already proved.
      **Caution:** one-directional imports (sub-seeds → helpers/lists, never back).
- covered by **B.1** — **`CircleStateLayer` / `RectangleStateLayer` / `DiamondStateLayer`** →
      `AbstractStateLayer<T>` base. Drops ~2100 dup lines AND each shape file falls < 1000:
      size + duplication fixed by the same refactor.

**Highest-leverage first targets** (fix size *and* duplication, or are locally verifiable):
B.1 StateLayer base · `seed_data.py` split · `simulation_runner.py` / `SolutionExecutionEngine.py`
(backend, selftest-coverable). The big frontend god-files (`custom-no-code.ts`,
`class-main-page`) are the highest payoff but want the rebuild loop to verify.
