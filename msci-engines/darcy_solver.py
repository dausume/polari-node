"""
Darcy solver — the msci-engines WORKER TWIN of
polari-framework/materialsScience/engines/darcy_engine.py
(_validate_payload + _solve_local, copied verbatim — the worker has no
framework import path). Port any change made there to here.

Serves /darcy/head-field and /darcy/drains via engines_service.py.
"""


def _validate_payload(payload):
    """Shared payload checks; returns an error string or ''."""
    geometry = payload.get('geometry') or {}
    if float(geometry.get('width_m', 0) or 0) <= 0 \
            or float(geometry.get('height_m', 0) or 0) <= 0:
        return 'geometry.width_m and geometry.height_m must be > 0'
    if float(payload.get('k_m_per_s', 0) or 0) <= 0:
        return 'k_m_per_s (hydraulic conductivity) must be > 0'
    holes = payload.get('holes') or []
    if not any(h.get('kind') == 'output' for h in holes):
        return 'at least one output hole is required'
    return ''


def _solve_local(payload):
    """The actual skfem solve. payload (all SI):
      {'geometry': {'width_m', 'height_m'},
       'k_m_per_s': K,
       'water_level_m': L,           # head at the input / supply
       'holes': [{'kind': 'input'|'output', 'z_m': elevation of the
                  hole CENTER, 'radius_m': bore radius,
                  'side': 'left'|'right'}, ...],
       'refine': mesh refinement (default 6),
       'source': volumetric source (default 0.0)}

    Returns {'ok': True, 'headField': [[x_m, z_m, h_m], ...],
             'outflowRateM3s', 'outflowRateMlS', 'inflowRateM3s',
             'fluxStats': {...}, 'meshMeta': {...}, 'note'} or
            {'ok': False, 'error'}.

    WORKER TWIN: msci-engines/engines_service.py duplicates this body
    (the worker has no framework import path) — port changes there.
    """
    error = _validate_payload(payload)
    if error:
        return {'ok': False, 'error': error}

    import numpy as np
    from skfem import (MeshTri, Basis, ElementTriP1, asm, condense,
                       solve, BilinearForm)
    from skfem.helpers import dot, grad

    geometry = payload['geometry']
    width = float(geometry['width_m'])
    height = float(geometry['height_m'])
    conductivity = float(payload['k_m_per_s'])
    water_level = float(payload.get('water_level_m', height) or height)
    refine = int(payload.get('refine', 6) or 6)

    mesh = MeshTri().refined(refine).scaled([width, height])
    basis = Basis(mesh, ElementTriP1())
    spacing = max(width, height) / (2 ** refine)

    @BilinearForm
    def darcy(u, v, _):
        return conductivity * dot(grad(u), grad(v))

    stiffness = asm(darcy, basis)

    def _patch_dofs(hole):
        """Boundary nodes on the hole's side wall within the bore."""
        x_wall = 0.0 if hole.get('side', 'left') == 'left' else width
        z_center = float(hole['z_m'])
        halfspan = max(float(hole.get('radius_m', 0.005) or 0.005),
                       0.75 * spacing)   # guarantee >= 1 node
        return basis.get_dofs(
            lambda x: (np.abs(x[0] - x_wall) < 1e-9)
                      & (np.abs(x[1] - z_center) <= halfspan))

    head = basis.zeros()
    input_dofs, output_dofs = [], []
    for hole in payload.get('holes', []):
        dofs = _patch_dofs(hole)
        flat = np.array(dofs.flatten(), dtype=int)
        if flat.size == 0:
            return {'ok': False,
                    'error': f"hole at z={hole.get('z_m')} m matched no "
                             f'mesh nodes (mesh spacing {spacing:.4g} m) '
                             f'— raise refine'}
        if hole.get('kind') == 'input':
            head[flat] = water_level
            input_dofs.append(flat)
        else:
            head[flat] = float(hole['z_m'])
            output_dofs.append(flat)
    dirichlet = np.unique(np.concatenate(input_dofs + output_dofs))

    load = basis.zeros()
    source = float(payload.get('source', 0.0) or 0.0)
    if source:
        from skfem.models.poisson import unit_load
        load = source * asm(unit_load, basis)

    head = solve(*condense(stiffness, load, x=head, D=dirichlet))

    # Discrete reaction flux at Dirichlet nodes: with zero interior
    # residual, (A·h - f)_i = ∫ K∇h·n φ_i dΓ (outward normal, per unit
    # thickness). Darcy flux is q = -K∇h, so water LEAVING through a
    # patch is the NEGATED reaction sum there (and water entering at
    # the input is the positive reaction sum).
    reaction = stiffness @ head - load
    outflow_per_thickness = float(-sum(
        reaction[flat].sum() for flat in output_dofs))
    inflow_per_thickness = float(sum(
        reaction[flat].sum() for flat in input_dofs))

    # Effective slot thickness: the bore diameter of the (largest)
    # output hole — the stated 2-D -> volumetric approximation.
    out_holes = [h for h in payload.get('holes', [])
                 if h.get('kind') == 'output']
    thickness = max(2.0 * float(h.get('radius_m', 0.005) or 0.005)
                    for h in out_holes)
    outflow_m3s = outflow_per_thickness * thickness
    inflow_m3s = inflow_per_thickness * thickness

    # Per-element Darcy speed |−K∇h| (P1 -> constant gradient/element).
    p, t = mesh.p, mesh.t
    x1, y1 = p[:, t[0]]
    x2, y2 = p[:, t[1]]
    x3, y3 = p[:, t[2]]
    det = (x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)
    h1, h2, h3 = head[t[0]], head[t[1]], head[t[2]]
    grad_x = ((h2 - h1) * (y3 - y1) - (h3 - h1) * (y2 - y1)) / det
    grad_z = ((h3 - h1) * (x2 - x1) - (h2 - h1) * (x3 - x1)) / det
    speed = conductivity * np.sqrt(grad_x ** 2 + grad_z ** 2)

    head_field = [[round(float(x), 5), round(float(z), 5),
                   round(float(v), 6)]
                  for x, z, v in zip(mesh.p[0], mesh.p[1], head)]
    # Triangle connectivity — 2026-07-15, keep in sync with the
    # framework twin (materialsScience/engines/darcy_engine.py).
    triangles = [[int(a), int(b), int(c)]
                for a, b, c in zip(mesh.t[0], mesh.t[1], mesh.t[2])]
    return {
        'ok': True, 'engine': 'scikit-fem', 'fidelity': 'fem',
        'headField': head_field,
        'headFieldColumns': ['x_m', 'z_m', 'head_m'],
        'headFieldTriangles': triangles,
        'outflowRateM3s': outflow_m3s,
        'outflowRateMlS': outflow_m3s * 1e6,
        'inflowRateM3s': inflow_m3s,
        'fluxStats': {'maxDarcySpeedMs': float(np.max(speed)),
                      'meanDarcySpeedMs': float(np.mean(speed))},
        'meshMeta': {'elements': int(mesh.t.shape[1]),
                     'nodes': int(mesh.p.shape[1]),
                     'refine': refine, 'spacingM': spacing,
                     'widthM': width, 'heightM': height},
        'note': '2-D vertical cross-section; volumetric rates use the '
                'output bore diameter as effective slot thickness — an '
                'approximation, stated here. Full 3-D / Navier-Stokes '
                'is out of scope (fidelity ceiling).',
    }
