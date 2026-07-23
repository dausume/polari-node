"""
msci-engines structure analysis (ssp-3) — the pymatgen side of the
crystal-structure work. Same contract as every worker engine: honest
capability, data-in/data-out, refusals name the knob.

  POST /structure/analyze  {"cell": [[3x3 A]], "symbols": ["Fe", ...],
                            "frac": [[x,y,z], ...], "symprec": 0.01}
      -> detected space group (symbol + number), crystal system,
         point group, symmetrized Wyckoff sites, primitive cell size
  POST /structure/xrd      {same structure fields, "wavelength":
                            "CuKa", "twoThetaMax": 90, "topN": 30}
      -> simulated powder XRD stick pattern (two-theta, relative
         intensity, hkl, d-spacing) — the bench-comparable prediction
"""

import json

import falcon


def _structure_from(body):
    """(pymatgen Structure, refusal|None) from the wire payload."""
    cell = body.get('cell')
    symbols = body.get('symbols')
    frac = body.get('frac')
    if not (cell and symbols and frac) or len(symbols) != len(frac):
        return None, {
            'ok': False,
            'error': 'structure payload needs cell (3x3), symbols '
                     '(N) and frac (Nx3) of equal length',
            'suggestion': {'knob': 'cell/symbols/frac',
                           'action': 'send the built P1 cell (the '
                                     'backend crystal_ops payload)'}}
    try:
        from pymatgen.core import Lattice, Structure
        structure = Structure(Lattice(cell), symbols, frac)
        return structure, None
    except Exception as e:
        return None, {'ok': False,
                      'error': f'pymatgen could not build the '
                               f'structure: {e}'}


def _pymatgen_missing():
    try:
        import pymatgen  # noqa: F401
        return None
    except Exception as e:
        return {'ok': False,
                'error': f'pymatgen unavailable on this worker: {e}',
                'suggestion': {
                    'knob': 'msci-engines image',
                    'action': 'rebuild the worker (pymatgen is a '
                              'standard dependency of the Debian '
                              'image)'}}


class StructureAnalyzeResource:
    def on_post(self, req, resp):
        missing = _pymatgen_missing()
        if missing:
            resp.media = missing
            return
        body = json.load(req.bounded_stream)
        structure, refusal = _structure_from(body)
        if refusal:
            resp.media = refusal
            return
        symprec = float(body.get('symprec', 0.01) or 0.01)
        try:
            from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
            analyzer = SpacegroupAnalyzer(structure, symprec=symprec)
            symmetrized = analyzer.get_symmetrized_structure()
            wyckoffs = []
            for sites, letter in zip(symmetrized.equivalent_sites,
                                     symmetrized.wyckoff_symbols):
                site = sites[0]
                wyckoffs.append({
                    'element': site.specie.symbol,
                    'wyckoff': letter,
                    'multiplicity': len(sites),
                    'representativeFrac': [round(float(v), 5)
                                           for v in site.frac_coords],
                })
            primitive = analyzer.find_primitive()
            resp.media = {
                'ok': True,
                'spaceGroupSymbol': analyzer.get_space_group_symbol(),
                'spaceGroupNumber': analyzer.get_space_group_number(),
                'crystalSystem': analyzer.get_crystal_system(),
                'pointGroup': analyzer.get_point_group_symbol(),
                'hallSymbol': analyzer.get_hall(),
                'wyckoffSites': wyckoffs,
                'primitiveAtomCount': len(primitive)
                if primitive is not None else len(structure),
                'inputAtomCount': len(structure),
                'symprec': symprec,
                'engine': f'pymatgen SpacegroupAnalyzer',
            }
        except Exception as e:
            resp.media = {'ok': False,
                          'error': f'symmetry analysis failed: {e}',
                          'suggestion': {
                              'knob': 'symprec',
                              'action': 'loosen the symmetry '
                                        'tolerance (e.g. 0.1) for '
                                        'slightly-distorted cells'}}


class StructureXrdResource:
    def on_post(self, req, resp):
        missing = _pymatgen_missing()
        if missing:
            resp.media = missing
            return
        body = json.load(req.bounded_stream)
        structure, refusal = _structure_from(body)
        if refusal:
            resp.media = refusal
            return
        wavelength = body.get('wavelength', 'CuKa')
        two_theta_max = float(body.get('twoThetaMax', 90.0) or 90.0)
        top_n = int(body.get('topN', 30) or 30)
        try:
            from pymatgen.analysis.diffraction.xrd import XRDCalculator
            calculator = XRDCalculator(wavelength=wavelength)
            pattern = calculator.get_pattern(
                structure, two_theta_range=(0, two_theta_max))
            peaks = []
            for two_theta, intensity, hkls, d in zip(
                    pattern.x, pattern.y, pattern.hkls,
                    pattern.d_hkls):
                peaks.append({
                    'twoTheta': round(float(two_theta), 3),
                    'intensity': round(float(intensity), 2),
                    'hkl': [list(h['hkl']) for h in hkls],
                    'dSpacingA': round(float(d), 4),
                })
            peaks.sort(key=lambda p: -p['intensity'])
            peaks = sorted(peaks[:top_n],
                           key=lambda p: p['twoTheta'])
            resp.media = {
                'ok': True,
                'wavelength': str(wavelength),
                'twoThetaMax': two_theta_max,
                'peaks': peaks,
                'peakCount': len(peaks),
                'engine': 'pymatgen XRDCalculator',
                'validity': 'kinematic powder pattern of the IDEAL '
                            'lattice — texture, strain, instrument '
                            'broadening and thermal factors are not '
                            'modeled; compare peak POSITIONS first',
            }
        except Exception as e:
            resp.media = {'ok': False,
                          'error': f'XRD simulation failed: {e}'}
