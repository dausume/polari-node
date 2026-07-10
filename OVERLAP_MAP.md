# Cross-Cutting Module Overlap Map

Authoritative index of shared / cross-cutting code in this repository. Updated
during code review whenever a `@cross-cutting` file is added, edited, or its
consumer set changes.

## How to use this document

- **Before editing** a file in any directory listed below: read its row here +
  the file's own `@cross-cutting` header. Both list the consumers you may
  break. Run those consumers' smoke checks before merging.
- **When adding a new consumer** of a cross-cutting module: update both the
  module's `@cross-cutting` header AND the corresponding row here in the same PR.
- **When introducing a new cross-cutting module**: add a row here, an
  `IMPACT.md` next to the module's code, and `@cross-cutting` headers on every
  file inside it.

## Cross-cutting categories

We label functionality by the surfaces it spans, so the impact of a change is
predictable.

| Category tag | Meaning |
|---|---|
| `@xc:render-2d` | Used by anything that puts shapes on an SVG surface |
| `@xc:render-3d` | Used by anything that puts meshes in a WebGL scene |
| `@xc:render-shared` | Used by both 2D and 3D rendering surfaces |
| `@xc:no-code` | Used by the no-code state editor |
| `@xc:maps` | Used by the geographic Maps page |
| `@xc:bindings` | Used by the per-class binding model on `class-main-page` |
| `@xc:overlay` | Used by Angular-component-anchored-to-renderer-element overlays |
| `@xc:auth` | Used by the permissions / Keycloak integration |

A file can carry multiple tags. Tags drive the manual impact-check checklist
when editing.

---

## Index

### Cross-cutting modules — current state

| Module | Path | Tags | Consumers | Notes |
|---|---|---|---|---|
| `SvgIconLibrary` | `polari-platform-angular/src/app/models/shared/SvgIconLibrary.ts` | `@xc:render-2d`, `@xc:maps`, `@xc:bindings` | maps (markers), table action buttons, geojson config, class-main-page, class-data-table, table-config-sidebar | Named icons + named styles + apply helpers. **Phase 1 candidate for absorption into `sim-space-2d/` as Shape2D + Style2D libraries.** |
| `GeoJsonConfigData.SvgMarkerDefinition` | `polari-platform-angular/src/app/models/geojson/GeoJsonConfigData.ts` | `@xc:render-2d`, `@xc:maps`, `@xc:bindings` | geojson config sidebar, map renderer, data view, feature collection view, map polygon definition | The "iconName + styleName" reference pattern. **Phase 1 keeps as-is, but SimSpace2D's binding model uses the same reference shape so future merge is non-breaking.** |
| `D3ModelLayer` / `CircleStateLayer` / `RectangleStateLayer` / `DiamondStateLayer` | `polari-platform-angular/src/app/models/noCode/d3-extensions/` | `@xc:render-2d`, `@xc:no-code` | no-code editor (sole consumer today) | **5,347 lines.** ~30% is generic d3-join + drag + click + highlight scaffolding (lift to `sim-space-2d/d3-layers/`); ~70% is state-machine-specific (slots, connectors, bezier rails — stays in no-code on top of the lifted base). |
| `state-overlay-shell` / `state-overlay-interactions` | `polari-platform-angular/src/app/components/custom-no-code/states/_shared/state-overlay/` | `@xc:overlay`, `@xc:no-code` | no-code state overlays | Lift the overlay-anchoring mechanism to `sim-space/` as a generic `SimSpaceOverlayManager`; keep state-overlay specifics in no-code. |
| `OverlayComponentService` / `InteractionStateService` / `StateOverlayManager` | `polari-platform-angular/src/app/services/no-code-services/` | `@xc:overlay`, `@xc:no-code` | no-code editor, state-overlay components | Selection state + overlay coordination. Generic mechanism lifts; state-machine knowledge stays. |
| Keycloak + permissions cross-cutting | `polari-framework/accessControl/`, `polari-platform-angular/src/app/services/auth/*`, `polari-platform-angular/src/app/services/permissions/*` | `@xc:auth` | all backend endpoints (via Falcon middleware), header bar, permissions page, every per-class permissions tab | Mature, already cross-cutting. Listed for completeness — the auth substrate is a model for what "good cross-cutting documentation" looks like once it stabilizes. |
| Host/device resource probing | `polari-framework/polariNetworking/defineLocalSys.py` (isoSys, psutil), `polari-framework/simulations/resource_monitor.py` (cgroup/meminfo/disk budget), `polari-framework/polariApiServer/systemInfoAPI.py` (/system-info), `polari-framework/resources/node_resources.py` (res-1 consumer) | `@xc:bindings` | systemInfoAPI, simulations batch-run guard, resources.node_resources (node inventory), msci-engines + cad-engines workers (stdlib /system-info twins) | ONE probing rule: isoSys owns psutil hardware counts, resource_monitor owns the cgroup-aware budget + disk. `resources/` REUSES both (never re-probes psutil); the slim workers carry a stdlib-only /system-info because their images lack psutil. Any new resource probe must go through one of these three, not a new psutil call. |

### Cross-cutting modules — planned (Phase 0 skeleton; Phase 1 fills them in)

| Module | Path | Tags | Eventual consumers | Status |
|---|---|---|---|---|
| `sim-space/` (shared base) | `polari-platform-angular/src/app/{models,services,components}/sim-space/` + `polari-framework/simSpace/` | `@xc:render-shared`, `@xc:bindings`, `@xc:overlay` | sim-space-2d, sim-space-3d, no-code (after migration), eventually maps for primitive reuse | **Phase 0: empty skeleton.** Phase 1: `SimSpaceRenderer` interface, `SimSpaceObject` base, `SimSpaceDefinition` base, snapshot endpoint shape. |
| `sim-space-2d/` | `polari-platform-angular/src/app/{models,services,components}/sim-space-2d/` + `polari-framework/simSpace2D/` | `@xc:render-2d`, `@xc:bindings`, `@xc:no-code` | SimSpace2D viewer; no-code editor (after migration); class-main-page Sim Space → 2D tab | **Phase 0: empty skeleton.** Phase 1: D3SimSpaceRenderer (lifting the generic 30% of `d3-extensions/`), Shape2D + Style2D libraries (absorbing `SvgIconLibrary`). |
| `sim-space-3d/` | `polari-platform-angular/src/app/{models,services,components}/sim-space-3d/` + `polari-framework/simSpace3D/` | `@xc:render-3d`, `@xc:bindings` | SimSpace3D viewer; class-main-page Sim Space → 3D tab | **Phase 0: empty skeleton.** Phase 2: ThreeSimSpaceRenderer behind the same interface; Mesh3D / Material3D / Texture3D libraries. |

---

## Header comment convention

Every file inside any cross-cutting module gets a header like this:

```typescript
/**
 * @cross-cutting
 * @tags @xc:render-2d, @xc:no-code
 * @consumers
 *   - no-code editor (state rendering, drag, overlay anchor)
 *   - sim-space-2d viewer (primary user)
 *   - sim-space-3d viewer (Phase 2+)
 *   - dashboard renderer (when SimSpace embeds land)
 * @impact-on-edit
 *   Run these smoke checks before merging:
 *   - No-code editor: drag a state, draw a transition, open an overlay, save/load
 *   - SimSpace2D demo: render circles, click-to-instance navigation
 *   - SimSpace3D demo: render cubes, click-to-instance (if 3D is wired)
 * @see /OVERLAP_MAP.md
 */
```

The header lives at the top of every `.ts` / `.py` file inside a cross-cutting
module. **The list in the header IS the contract** — when a new consumer
appears, the header must be updated in the same PR, with the corresponding row
in this document updated as well.

---

## Maps consolidation policy

Maps shares marker / styling / binding *primitives* with SimSpace2D, but is
NOT itself a SimSpace2D. The boundary:

**Maps shares with SimSpace2D (Phase 1+):**
- Shape2D library (circle, rectangle, diamond, pin, star, …)
- Style2D library (fill, stroke, opacity, anchor)
- Marker-hierarchy reference pattern (per-feature → collection → config default)
- Class-binding model (fields → renderable position)
- Overlay-on-marker mechanism (click → Angular component)
- Picking + click-action contract

**Maps OWNS and SimSpace2D does NOT inherit:**
- Geographic projection (Mercator, equirectangular)
- Tile sources + mbtiles handling
- Geocoder definitions + address lookup
- Geographic zoom levels
- GeoJSON RFC 7946 feature collection model
- `MapPointDefinition` / `MapLineSegmentDefinition` / `MapPolygonDefinition`
  as classes — they encode geographic semantics SimSpace2DObject doesn't need
- MinIO mbtiles auto-registration pipeline

When a Phase 1 PR moves `SvgIconLibrary` content into `sim-space-2d/`, the
Maps code keeps consuming it via re-export — no churn in the consumers, the
implementation home just changes.

---

## D3 consumption boundary

D3 is currently imported by:

- `polari-platform-angular/src/app/models/noCode/d3-extensions/*` (4 files)
- `polari-platform-angular/src/app/models/noCode/NoCodeSolution.ts`
- `polari-platform-angular/src/app/services/no-code-services/no-code-state-renderer-manager.ts`
- `polari-platform-angular/src/app/components/custom-no-code/*` (3 files)

**Total: 10 files, all in the no-code area.** Charts use Observable Plot
(higher-level), maps use maplibre-gl (own pipeline). D3 has no other
consumers today.

**After Phase 1:** d3 imports will be allowed in `sim-space-2d/` only. The
no-code editor will import from `sim-space-2d/` instead of d3 directly. Lint
rule (advisory at first) will flag any new `import 'd3'` outside
`sim-space-2d/`.

---

## Three.js consumption boundary (planned)

Three.js will be imported only by `sim-space-3d/`, lazy-loaded so the main
bundle is unaffected for users who never open a 3D scene. Lint rule (advisory
at introduction) will flag any new `import 'three'` outside `sim-space-3d/`.

---

## Maintenance log

| Date | Change | PR |
|---|---|---|
| Phase 0 (today) | Initial document + empty `sim-space/`, `sim-space-2d/`, `sim-space-3d/` skeletons committed | (this work) |
