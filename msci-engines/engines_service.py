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
    # res-2: declared resource footprint — the profile importer caches
    # this into a ModuleResourceProfile(subject_kind='engine'). Numbers
    # are DECLARED (labeled); res-3 measurement overrides them.
    import os
    report['resources'] = {
        'minThreads': 1,
        'threadCeiling': os.cpu_count() or 1,  # BLAS/FEM assembly scales
        'cpuBenefit': 'sublinear',
        'ramMb': 900,     # pyscf+sfepy resident baseline under load
        'imageMb': 2160,  # prf-msci-engines:staging on-disk size
        'fidelity': 'declared',
    }
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
            result = {'ok': True, 'engine': 'pyscf',
                      'totalEnergyHa': float(energy),
                      'converged': bool(mf.converged),
                      'basis': body.get('basis', '6-31g'),
                      'xc': body.get('xc', 'b3lyp'),
                      'atomCount': mol.natm,
                      'electronCount': int(mol.nelectron)}
            # Frontier orbitals (msci-23): donor/acceptor evidence for
            # the semiconductor bias analysis. Kohn-Sham levels —
            # approximate, said in the note.
            try:
                occupied = [float(e) for e, o in
                            zip(mf.mo_energy, mf.mo_occ) if o > 0]
                virtual = [float(e) for e, o in
                           zip(mf.mo_energy, mf.mo_occ) if o == 0]
                if occupied and virtual:
                    ha_to_ev = 27.211386245988
                    homo = max(occupied) * ha_to_ev
                    lumo = min(virtual) * ha_to_ev
                    result.update({
                        'homoEv': homo, 'lumoEv': lumo,
                        'gapEv': lumo - homo,
                        'frontierNote': 'Kohn-Sham orbital energies — '
                                        'approximate frontier levels '
                                        'for donor/acceptor '
                                        'comparison, not measured '
                                        'IP/EA'})
            except Exception:
                pass
            resp.media = result
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


class DarcyHeadFieldResource:
    """aqp-3: steady Darcy head field for a self-watering pot cross-
    section. Payload/result contract lives in darcy_solver.py (the
    framework twin is materialsScience/engines/darcy_engine.py)."""

    def on_post(self, req, resp):
        body = json.load(req.bounded_stream)
        try:
            from darcy_solver import _solve_local
            result = _solve_local(body)
        except Exception as e:
            resp.status = falcon.HTTP_500
            resp.media = {'ok': False, 'error': f'darcy solve failed: {e}'}
            return
        if not result.get('ok'):
            resp.status = falcon.HTTP_400
        resp.media = result


class DarcyDrainsResource:
    """aqp-3: the headline drains-by-gravity verdict."""

    def on_post(self, req, resp):
        body = json.load(req.bounded_stream)
        try:
            from darcy_solver import _validate_payload, _solve_local
            error = _validate_payload(body)
            if error:
                resp.status = falcon.HTTP_400
                resp.media = {'ok': False, 'error': error}
                return
            water_level = float(body.get('water_level_m', 0) or 0)
            lowest_output = min(float(h['z_m']) for h in body['holes']
                                if h.get('kind') == 'output')
            if water_level <= lowest_output:
                resp.media = {
                    'ok': True, 'drains': False, 'fidelity': 'geometric',
                    'outflowRateMlS': 0.0,
                    'limitingFactor': 'water table at/below the lowest '
                                      'output hole',
                    'evidence': f'water level {water_level:.4g} m <= '
                                f'lowest output lip {lowest_output:.4g} '
                                f'm — no head difference drives flow'}
                return
            result = _solve_local(body)
            if not result.get('ok'):
                resp.status = falcon.HTTP_400
                resp.media = result
                return
            outflow_mls = float(result.get('outflowRateMlS', 0.0))
            drains = outflow_mls > 1e-9
            resp.media = {
                'ok': True, 'drains': drains,
                'outflowRateMlS': outflow_mls, 'fidelity': 'fem',
                'limitingFactor': None if drains else
                    'computed outflow ~ 0 despite positive head — check '
                    'K and hole placement',
                'evidence': f'steady Darcy solve: water level '
                            f'{water_level:.4g} m vs lowest output '
                            f'{lowest_output:.4g} m; outflow '
                            f'{outflow_mls:.4g} mL/s',
                'meshMeta': result.get('meshMeta')}
        except Exception as e:
            resp.status = falcon.HTTP_500
            resp.media = {'ok': False, 'error': f'darcy solve failed: {e}'}


class SystemInfoResource:
    """Host specs for the node this worker runs on (res-1 remote
    inventory pull). stdlib-only — same response shape as the
    backend's /system-info, minus what a slim worker can't know."""

    @staticmethod
    def _meminfo():
        info = {}
        try:
            with open('/proc/meminfo') as f:
                for line in f:
                    parts = line.split()
                    if parts and parts[0].rstrip(':') in (
                            'MemTotal', 'MemAvailable'):
                        info[parts[0].rstrip(':')] = int(parts[1]) * 1024
        except (OSError, ValueError, IndexError):
            pass
        return info

    @staticmethod
    def _physical_cpus():
        try:
            cores = set()
            with open('/proc/cpuinfo') as f:
                phys = core = None
                for line in f:
                    if line.startswith('physical id'):
                        phys = line.split(':')[1].strip()
                    elif line.startswith('core id'):
                        core = line.split(':')[1].strip()
                    elif not line.strip() and phys is not None:
                        cores.add((phys, core))
                        phys = core = None
            return len(cores)
        except OSError:
            return 0

    @staticmethod
    def _cgroup_limit():
        for path in ('/sys/fs/cgroup/memory.max',
                     '/sys/fs/cgroup/memory/memory.limit_in_bytes'):
            try:
                with open(path) as f:
                    raw = f.read().strip()
                if raw == 'max':
                    return 0
                limit = int(raw)
                return 0 if limit >= (1 << 60) else limit
            except (OSError, ValueError):
                continue
        return 0

    @staticmethod
    def _process_mem():
        """This worker process's resident/peak RSS (res-3 measured
        RAM) — /proc/self/status VmRSS/VmHWM, stdlib."""
        out = {}
        try:
            with open('/proc/self/status') as f:
                for line in f:
                    if line.startswith(('VmRSS', 'VmHWM')):
                        key = 'residentMb' if line.startswith(
                            'VmRSS') else 'peakMb'
                        out[key] = round(
                            int(line.split()[1]) / 1024.0, 1)
        except (OSError, ValueError, IndexError):
            pass
        return out

    def on_get(self, req, resp):
        import os
        import platform
        mem = self._meminfo()
        total = mem.get('MemTotal', 0)
        avail = mem.get('MemAvailable', 0)
        try:
            usage = shutil.disk_usage('/')
            disk = {'totalBytes': usage.total, 'freeBytes': usage.free}
        except OSError:
            disk = {'totalBytes': 0, 'freeBytes': 0}
        resp.media = [{'system-info': {
            'platform': {
                'systemType': platform.system(),
                'networkName': os.environ.get(
                    'HOSTNAME', platform.node()),
                'arch': platform.machine(),
                'isContainerized': True,
                'service': 'msci-engines',
            },
            'cpu': {
                'numLogicalCPUs': os.cpu_count() or 0,
                'numPhysicalCPUs': self._physical_cpus(),
                'currentUsagePercent': 0,
            },
            'memory': {
                'total': total,
                'available': avail,
                'percentUsed': round(
                    (total - avail) * 100.0 / total, 1)
                if total else 0,
                'cgroupLimitBytes': self._cgroup_limit(),
            },
            'disk': disk,
            'process': self._process_mem(),
        }}]


from structure_service import (          # noqa: E402
    StructureAnalyzeResource, StructureXrdResource,
)

app = falcon.App()
app.add_route('/capability', CapabilityResource())
app.add_route('/system-info', SystemInfoResource())
app.add_route('/dft/molecular-energy', MolecularEnergyResource())
app.add_route('/fem/conduction', ConductionResource())
app.add_route('/darcy/head-field', DarcyHeadFieldResource())
app.add_route('/darcy/drains', DarcyDrainsResource())
app.add_route('/structure/analyze', StructureAnalyzeResource())
app.add_route('/structure/xrd', StructureXrdResource())
