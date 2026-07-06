"""
msci-engines HTTP surface — the worker side of materialsScience/engines/.

Same contract as the in-backend engines: capability() is honest,
compute endpoints return data or an evidence-bearing refusal. JSON only,
no framework dependency — the backend's engine modules translate to/from
MaterialScaleDefinition land.

  GET  /capability
  POST /dft/molecular-energy   {"atoms": "C 0 0 0; ...", "basis": "6-31g",
                                "xc": "b3lyp"}   -> total energy (Hartree)
  POST /fem/conduction         {"thermalConductivity": k, "heatSource": q,
                                "refine": n}     -> temperature field stats
"""

import json
import shutil

import falcon


def _capability():
    report = {'service': 'msci-engines', 'engines': {}}
    for lib in ('pyscf', 'pymatgen', 'ase', 'skfem', 'sfepy'):
        try:
            mod = __import__(lib)
            report['engines'][lib] = {
                'available': True,
                'version': getattr(mod, '__version__', 'unknown')}
        except Exception as e:
            report['engines'][lib] = {'available': False, 'error': str(e)}
    report['engines']['quantum-espresso'] = {
        'available': bool(shutil.which('pw.x')),
        'note': 'build with WITH_QE=1 to include'}
    return report


class CapabilityResource:
    def on_get(self, req, resp):
        resp.media = _capability()


class MolecularEnergyResource:
    def on_post(self, req, resp):
        body = json.load(req.bounded_stream)
        atoms = body.get('atoms', '')
        if not atoms:
            resp.status = falcon.HTTP_400
            resp.media = {'ok': False, 'error': "'atoms' is required "
                          "(pyscf format: 'C 0 0 0; H 0 0 1.1; ...')"}
            return
        try:
            from pyscf import gto, dft
            mol = gto.M(atom=atoms, basis=body.get('basis', '6-31g'),
                        charge=int(body.get('charge', 0)),
                        spin=int(body.get('spin', 0)), verbose=0)
            mf = dft.RKS(mol)
            mf.xc = body.get('xc', 'b3lyp')
            energy = mf.kernel()
            resp.media = {'ok': True, 'engine': 'pyscf',
                          'totalEnergyHa': float(energy),
                          'converged': bool(mf.converged),
                          'basis': body.get('basis', '6-31g'),
                          'xc': body.get('xc', 'b3lyp'),
                          'atomCount': mol.natm,
                          'electronCount': int(mol.nelectron)}
        except Exception as e:
            resp.status = falcon.HTTP_500
            resp.media = {'ok': False, 'error': f'pyscf failed: {e}'}


class ConductionResource:
    def on_post(self, req, resp):
        body = json.load(req.bounded_stream)
        k = float(body.get('thermalConductivity', 0.0) or 0.0)
        if k <= 0:
            resp.status = falcon.HTTP_400
            resp.media = {'ok': False,
                          'error': f'thermalConductivity must be > 0, got {k}'}
            return
        try:
            import numpy as np
            from skfem import (MeshTri, Basis, ElementTriP1, asm, condense,
                               solve, BilinearForm)
            from skfem.helpers import dot, grad
            from skfem.models.poisson import unit_load

            mesh = MeshTri().refined(int(body.get('refine', 4)))
            basis = Basis(mesh, ElementTriP1())

            @BilinearForm
            def conduction(u, v, _):
                return k * dot(grad(u), grad(v))

            stiffness = asm(conduction, basis)
            load = float(body.get('heatSource', 1.0)) * asm(unit_load, basis)
            temperature = solve(*condense(stiffness, load,
                                          D=basis.get_dofs()))
            resp.media = {'ok': True, 'engine': 'scikit-fem',
                          'maxTemperature': float(np.max(temperature)),
                          'meanTemperature': float(np.mean(temperature)),
                          'degreesOfFreedom': int(temperature.shape[0])}
        except Exception as e:
            resp.status = falcon.HTTP_500
            resp.media = {'ok': False, 'error': f'scikit-fem failed: {e}'}


app = falcon.App()
app.add_route('/capability', CapabilityResource())
app.add_route('/dft/molecular-energy', MolecularEnergyResource())
app.add_route('/fem/conduction', ConductionResource())
