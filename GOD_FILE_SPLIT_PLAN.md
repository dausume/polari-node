# Safe god-file split plan — conventions, structure, data-model alignment, method

Companion to `CODE_HEALTH_AUDIT.md` §D (which lists the per-file module breakdown). This
doc is the **how**: the conventions to follow, the target directory structure, how to keep
the **data model consistent with how models are already structured**, and a safe,
verifiable sequence. Nothing here is executed yet — it's the plan.

## 0. The conventions we must mirror (observed, not invented)

**Frontend** — domain-grouped folders, never a flat dump:
- `models/<domain>/` — the **data** (interfaces, config shapes, enums). Sub-concerns nest
  (`sim-space/types/`). Naming: `<thing>-types.ts`, `<thing>.ts`. Barrels re-export
  (`sim-space-types.ts → ./types`).
- `services/<domain>/` — the **behavior** (`*.service.ts`, plus `*.interface.ts` and plain
  helper modules like `three-geometry-builders.ts`). Sub-concerns nest (`sim-space-3d/controls/`).
- `components/<domain>/` — the **UI** (`*.component.ts/.html/.scss`, child components, a
  `states/` or `_shared/` subfolder for families).
- **`sim-space` is the reference template** (models/sim-space + services/sim-space +
  services/sim-space-2d + services/sim-space-3d/controls).

**Backend** — concern-packages with one cohesive concept per file:
- A package per concern (`polariNoCode`, `simulations`, `polariDataTyping`, `polariApiProfiler`…).
- **One model/concept per snake_case file** named after it (`simulation_definition.py` →
  `SimulationDefinition`, `*_sim_state.py`, `simulation_runner.py`).
- `selftest_*.py` lives beside the code it tests.

**The frontend↔backend mirror** (preserve it): `noCode↔polariNoCode`, `sim-space↔simSpace`,
`polyTyping↔polariDataTyping`, `stateSpace↔state-space`. A new module on one side should be
namable on the other.

## 1. Data-model alignment (the explicit ask)

**Data lives where models live; services/modules are behavior that *consume* models.** Two
rules, both already true in `sim-space` and the Definition/seed pattern:

1. **Frontend:** every interface / config shape an extracted service needs goes in
   `models/<domain>/` (or its `types/` subfolder), NOT inline in the service. The service
   imports it. (e.g. the no-code split's `FlowContext`, overlay-config, solution/state/slot
   shapes belong in `models/noCode/` — many already do; the new `flow-context.service.ts`
   imports them.) This matches `models/sim-space/types/` ← consumed by `services/sim-space/*`.
2. **Backend:** a data entity is a `treeObject` class in its own file with the standard
   shape — `@treeObjectInit __init__`, `field_save_policy` (`core`/`derivable`),
   `default_initial_field_values`, class-level metadata — and, if it's config, a Definition
   class seeded via a `SEED_*` list. When a god-file split needs a new persisted entity,
   model it this way; when it needs a pure helper, it's a plain module in the concern package
   (no treeObject). Do NOT invent a parallel data representation — reuse the existing
   Definition/`*_sim_state` model and its `field_save_policy` semantics.

**Generalizable vs domain-specific:** if an extracted unit is genuinely cross-domain (a D3
canvas manager, an entity-versioning service, a popup coordinator), put it in a `shared`
location (`models/shared`, a new `services/shared/`) so it's reusable; if it's coupled to the
domain's data, keep it in the domain folder. Decide per service — don't over-generalize
something that's really no-code-specific.

## 2. The safe method (applies to every file)

1. **Keep the public surface stable.** The orchestrator/facade (component, manager class,
   Falcon resource) keeps its existing API + template/route bindings. A split step must
   change **zero call sites** outside the file being split. This is what makes it safe.
2. **Characterize before cutting.** Where the file has no test, add a thin guard first:
   backend `selftest_*.py` asserting current behavior (we already have the eval + runner +
   newtonian selftests); frontend a grep-map of every template/external reference to the
   symbols being moved. Don't move code you can't re-verify.
3. **Move, don't improve, in the same step.** Cut a cohesive block into a new module and
   re-import it — byte-for-byte. Logic improvements (dedup, renames) are a *separate* later
   step. One concern per step → small, reviewable, revertible.
4. **Verify after every extraction.** Backend: `py_compile` the touched files + run the
   relevant `selftest_*` (+ a `grep` for dangling refs, like the State-Projection removal).
   Frontend: grep-guard passes + a `./rebuild-staging.sh` to compile (no local `ng build`),
   ideally one extraction per rebuild so a break is unambiguous.
5. **Order: smallest, lowest-risk, highest-cohesion first.** Build confidence and shrink the
   god-file incrementally rather than one big-bang rewrite.

**Frontend specifics:** extracted services are `@Injectable({ providedIn: 'root' })` (no
provider boilerplate); the thin component re-exposes any template-referenced method via a
one-line delegate; preserve public `*$` observables / `@Input`/`@Output`; the
stateClass→overlay **registry dispatch and the registry singleton stay intact** (load-bearing).
**Backend specifics:** keep the decorator/entry-point import order; **no circular imports
during manager `__init__`** (the store/tree/typing reflection runs there); keep **seed
execution order** + the `defClassList` invariant; Falcon `on_*` methods stay on the resource
class (routed by name).

## 3. Target structure per cluster (conventions applied)

### No-code editor  (mirror: `noCode` ↔ `polariNoCode`)
```
components/custom-no-code/
  custom-no-code.component.ts        # thin orchestrator (~400-500): template bindings + lifecycle
  states/                            # (exists) per-state-class overlay components
  popups/                            # extracted popup child components
services/no-code-services/           # (exists) — the extracted behavior
  canvas-manager.service.ts
  solution-loading.service.ts
  overlay-dispatch.service.ts        # + state-overlay-factories.service.ts
  context-resolution.service.ts
  code-generation-orchestrator.service.ts
  version-management.service.ts      # generalizable? → services/shared/ if entity-agnostic
  execution-panel.service.ts
  context-menu-action-handler.service.ts
  popup-coordinator.service.ts
  rendering-debug.service.ts
  solution-state/                    # split of no-code-solution-state.service.ts
    no-code-solution-state.service.ts  # facade keeping the public *$ observables
    solution-cache.service.ts
    solution-backend-sync.service.ts
    solution-data.service.ts
    solution-serialization.service.ts
    flow-context.service.ts
    solution-factory.service.ts
    solution-discovery.service.ts
    form-validation.service.ts
models/noCode/                       # (exists) — the DATA the services consume
  flow-context.types.ts  overlay-config.types.ts  solution.types.ts ...  (centralize here)
models/stateSpace/  +  components/custom-no-code/states/_shared/
  state-space-registry/              # split of state-space-class-registry.ts
    state-space-class-registry.ts    # singleton store + getters + solution registration
    state-space.types.ts             # shared metadata/category types → models/stateSpace
    registries/{initial-state,end-state,control-flow,data-transform,runtime-specific}.registry.ts
```

### Class-browser UI  (domain folder per page)
```
components/class-main-page/
  class-main-page.component.ts        # orchestrator (~1200)
  config-editors/                     # child components per config domain
    table-config-list|editor.component.*  graph-config-*  display-config-editor.*
    display-grid-layout-editor.component.*  form-config-*  button-config-*  class-config-section.*
services/class-page/                  # NEW domain folder — the per-domain facades
  display-editor.facade.ts  table-config.facade.ts  graph-config.facade.ts
  geojson-config.facade.ts  dataset-config.facade.ts  button-appearance.service.ts
components/templateClassTable/class-data-table/
  class-data-table.component.ts       # core table (~650)
services/table/                       # (exists) — extracted table behavior
  table-crud-dialog.service.ts  table-action-button-executor.service.ts  table-geometry-preview.service.ts
components/api-profiler/  +  services/  (api-query/analyzer/profile-builder/endpoint-manager/response-formatter)
```

### Backend object / typing / server core
```
polariObjectTree/                     # NEW package — split of objectTreeManagerDecorators.py
  __init__.py                         # re-exports managerObject so the import path is STABLE
  manager_object.py                   # managerObjectInit decorator + bootstrap (entry point)
  object_store.py  object_tree.py  object_mutation.py  object_query.py  object_serialize.py
polariDataTyping/                     # (exists) — split polyTyping.py in-place
  polyTyping.py                       # re-exports polyTypedObject (stable path)
  polyTypedObjectCore.py  ...Analysis.py  ...Schema.py  ...Serialization.py  ...Reflection.py
  polyFieldProfile.py
polariApiServer/                      # (exists) — split polariServer.py + createClassAPI.py in-place
  polariServer.py                     # keeps the polariServer class + boot orchestration
  polariServerBootstrap.py  ...Definitions.py  ...Modules.py  ...Seeds.py  ...SeedHelpers.py
  ...TileStore.py  ...DynamicCRUDE.py
  createClassAPI.py                   # router; delegates to:
  createClassCore.py  createClassPersistence.py  createClassDeletion.py
```
> Backend rule: when a god-file is a single class at a package root (e.g.
> `objectTreeManagerDecorators.py`), prefer a **new package whose `__init__.py` re-exports the
> public name** so every existing `from objectTreeManagerDecorators import managerObject` keeps
> working untouched. Same trick for `polyTyping.py` / `polariServer.py` via re-export.

### Backend execution + profiler
```
simulations/
  simulation_runner.py                # keeps run_step (public)
  step_orchestrator.py  step_dependencies.py  data_access.py
  step_operations/{__init__,contribution_merge,context_projection,initial_conditions}.py
  seed/                               # split of seed_data.py
    _helpers.py  pendulum_2d_seed.py  pendulum_3d_seed.py   (newtonian_pendulum_seed.py moves here)
  seed_data.py                        # thin SEED_PENDULUM_* aggregator (re-export path stable)
polariNoCode/
  SolutionExecutionEngine.py          # keeps execute() (public)
  operand_resolution.py  condition_evaluators.py  graph_traversal.py
  state_handlers/{__init__,entry,control_flow,operations,simulation}.py
polariApiProfiler/                    # resource classes keep on_*; logic → handlers
  apiProfilerAPI.py  profiler_query_handler.py  profiler_match_handler.py  endpoint_fetch_handler.py
  profileMatcher.py → structure_analyzer.py  signature_matcher.py  match_ranker.py
  apiProfiler.py → http_client.py  structure_analyzer.py  type_detector.py  polari_type_mapper.py  data_extraction.py
```

## 4. Sequencing (safest → highest-payoff)

**Phase 1 — backend, locally verifiable (selftests + py_compile). Lowest risk, do first.**
1. `seed_data.py` → `simulations/seed/` (proven by the Newtonian extraction; selftest covers compile).
2. `simulation_runner.py` and `SolutionExecutionEngine.py` → their packages (runner + newtonian selftests guard them).
3. `profileMatcher.py` / `apiProfiler.py` / `apiProfilerAPI.py` (add a thin profiler selftest first to characterize).

**Phase 2 — backend core, reflection-sensitive. Verify each via py_compile + a full server import smoke test + reseed.**
4. `polyTyping.py`, then `createClassAPI.py`, then `polariServer.py`, then `objectTreeManagerDecorators.py` → re-export packages. (Order matters: typing before the server that uses it.)

**Phase 3 — frontend, gated on `./rebuild-staging.sh` (no local `ng build`). One extraction per rebuild.**
5. `state-space-class-registry.ts` (lowest risk: data init, no DI change) → `registries/`.
6. `no-code-solution-state.service.ts` → `solution-state/` (facade keeps observables).
7. `class-data-table.ts`, then `api-profiler.component.*`, then `class-main-page.*`.
8. `custom-no-code.ts` LAST (biggest, most template-coupled) — extract one service per rebuild.

**Cross-cutting (any time):** B.1 `AbstractStateLayer<T>` — fixes the StateLayer duplication
*and* drops all three files under 1000 in one move (frontend-gated).

## 5. Per-step checklist (the guard rails)
- [ ] Characterization in place (selftest / grep-map of references).
- [ ] One cohesive unit moved, byte-for-byte; data shapes landed in `models/<domain>` (FE) or a
      proper `treeObject`/Definition file (BE).
- [ ] Public surface unchanged (no external call site edited).
- [ ] Verify: BE `py_compile` + selftest + dangling-ref grep; FE grep-guard + one rebuild.
- [ ] Import-path stability confirmed (re-export shim where a package replaced a file).
- [ ] Commit "move" separately from any later "improve".
