"""
cad-engines — the CAD import/export worker (its own module).

The prf-backend base image is Alpine/musl; trimesh rides there, but
FreeCAD (OpenCASCADE, a large C++ stack) and STEP toolkits do not. This
Debian worker mirrors msci-engines: the backend's mathshapes/cad_remote
delegates over HTTP when a format needs a library the Alpine image lacks
(CAD_ENGINES_URL knob).

Contract (same honesty rule as the msci worker — capability() never
lies about what is installed):

  GET  /capability                 which importers/exporters are live.
  POST /cad/import   {filename, format, contentBase64}
                     -> {ok, volume, bbox, centroid, meshPoints,
                         triangles, parametricParams}
  POST /cad/export   {format, meshPoints, triangles}
                     -> {ok, format, filename, contentType, contentBase64}

Baseline (always): trimesh imports STL/OBJ/PLY/GLTF/GLB and exports
STL/OBJ/GLB/three.js-JSON. FreeCAD (build-arg WITH_FREECAD=1) adds FCStd
import + STEP/FCStd export; it also covers STEP import.
"""

import base64
import io
import json

import falcon

# ---- optional heavy libs, probed once (honest capability) ----
try:
    import trimesh
    _TRIMESH = True
except Exception:
    _TRIMESH = False

try:
    import FreeCAD  # noqa: F401
    import Part     # noqa: F401
    import Mesh as FreeCADMesh  # noqa: F401
    _FREECAD = True
except Exception:
    _FREECAD = False


_TRIMESH_IMPORT = ('stl', 'obj', 'ply', 'gltf', 'glb', 'off')
_TRIMESH_EXPORT = ('stl', 'obj', 'glb', 'gltf', 'three-json', 'ply')
_FREECAD_IMPORT = ('fcstd', 'step', 'stp', 'iges', 'igs')
_FREECAD_EXPORT = ('step', 'stp', 'fcstd', 'iges')


def _capability():
    importers = list(_TRIMESH_IMPORT) if _TRIMESH else []
    exporters = list(_TRIMESH_EXPORT) if _TRIMESH else []
    if _FREECAD:
        importers += list(_FREECAD_IMPORT)
        exporters += list(_FREECAD_EXPORT)
    return {
        'ok': True, 'service': 'cad-engines',
        'trimesh': _TRIMESH, 'freecad': _FREECAD,
        'importFormats': importers, 'exportFormats': exporters,
        'note': 'trimesh is the always-on baseline; FreeCAD (WITH_FREECAD='
                '1) adds FCStd + STEP.',
        # res-2: declared resource footprint (labeled; res-3 measures).
        # trimesh import/export is strictly single-threaded — this
        # worker gains NOTHING from a big-compute node.
        'resources': {
            'minThreads': 1,
            'threadCeiling': 1,
            'cpuBenefit': 'none',
            'ramMb': 300,    # trimesh resident baseline
            'imageMb': 688,  # prf-cad-engines:staging on-disk size
            'fidelity': 'declared',
        },
    }


def _ext(fmt, filename):
    fmt = (fmt or '').lower().lstrip('.')
    if fmt:
        return fmt
    return (filename or '').lower().rsplit('.', 1)[-1]


class CapabilityResource:
    def on_get(self, req, resp):
        resp.media = _capability()


class ImportResource:
    def on_post(self, req, resp):
        body = req.media or {}
        fmt = _ext(body.get('format'), body.get('filename'))
        raw = body.get('contentBase64', '')
        try:
            data = base64.b64decode(raw)
        except Exception:
            resp.status = falcon.HTTP_400
            resp.media = {'ok': False, 'error': 'contentBase64 invalid'}
            return
        if fmt in _TRIMESH_IMPORT and _TRIMESH:
            resp.media = self._trimesh_import(data, fmt)
            return
        if fmt in _FREECAD_IMPORT and _FREECAD:
            resp.media = self._freecad_import(data, fmt)
            return
        resp.status = falcon.HTTP_400
        resp.media = {'ok': False,
                      'error': f"no importer for '{fmt}' on this worker",
                      'capability': _capability()}

    def _trimesh_import(self, data, fmt):
        try:
            mesh = trimesh.load(io.BytesIO(data), file_type=fmt, force='mesh')
            b = mesh.bounds  # [[minx,miny,minz],[maxx,maxy,maxz]]
            return {
                'ok': True, 'importer': 'trimesh',
                'volume': float(abs(mesh.volume)),
                'bbox': [[float(b[0][i]), float(b[1][i])] for i in range(3)],
                'centroid': [float(c) for c in mesh.centroid],
                'meshPoints': [[float(v) for v in p]
                               for p in mesh.vertices.tolist()],
                'triangles': [[int(i) for i in f]
                              for f in mesh.faces.tolist()],
                'parametricParams': {},
            }
        except Exception as e:
            return {'ok': False, 'error': f'trimesh import failed: {e}'}

    def _freecad_import(self, data, fmt):
        import tempfile
        import os
        try:
            with tempfile.NamedTemporaryFile(suffix=f'.{fmt}',
                                             delete=False) as fh:
                fh.write(data)
                path = fh.name
            shape = Part.Shape()
            if fmt in ('fcstd',):
                doc = FreeCAD.openDocument(path)
                shapes = [o.Shape for o in doc.Objects
                          if hasattr(o, 'Shape')]
                shape = shapes[0] if shapes else Part.Shape()
                params = {'freecadObjects': [o.Name for o in doc.Objects]}
            else:
                shape.read(path)
                params = {}
            os.unlink(path)
            mesh_data = shape.tessellate(0.1)
            verts = [[float(v.x), float(v.y), float(v.z)]
                     for v in mesh_data[0]]
            faces = [[int(i) for i in f] for f in mesh_data[1]]
            bb = shape.BoundBox
            return {
                'ok': True, 'importer': 'freecad',
                'volume': float(shape.Volume),
                'bbox': [[bb.XMin, bb.XMax], [bb.YMin, bb.YMax],
                         [bb.ZMin, bb.ZMax]],
                'centroid': [shape.CenterOfMass.x, shape.CenterOfMass.y,
                             shape.CenterOfMass.z],
                'meshPoints': verts, 'triangles': faces,
                'parametricParams': params,
            }
        except Exception as e:
            return {'ok': False, 'error': f'freecad import failed: {e}'}


class ExportResource:
    def on_post(self, req, resp):
        body = req.media or {}
        fmt = (body.get('format') or 'glb').lower().lstrip('.')
        points = body.get('meshPoints') or []
        faces = body.get('triangles') or []
        if not points:
            resp.status = falcon.HTTP_400
            resp.media = {'ok': False, 'error': 'meshPoints required'}
            return
        if fmt == 'three-json':
            resp.media = self._three_json(points, faces)
            return
        if fmt in _TRIMESH_EXPORT and _TRIMESH:
            resp.media = self._trimesh_export(points, faces, fmt)
            return
        if fmt in _FREECAD_EXPORT and _FREECAD:
            resp.media = self._freecad_export(points, faces, fmt)
            return
        resp.status = falcon.HTTP_400
        resp.media = {'ok': False,
                      'error': f"no exporter for '{fmt}' on this worker",
                      'capability': _capability()}

    def _trimesh_export(self, points, faces, fmt):
        try:
            mesh = trimesh.Trimesh(vertices=points, faces=faces or None)
            exported = mesh.export(file_type=fmt)
            if isinstance(exported, str):
                exported = exported.encode()
            return {'ok': True, 'format': fmt,
                    'filename': f'shape.{fmt}',
                    'contentType': 'model/gltf-binary' if fmt == 'glb'
                    else 'application/octet-stream',
                    'contentBase64': base64.b64encode(exported).decode()}
        except Exception as e:
            return {'ok': False, 'error': f'trimesh export failed: {e}'}

    def _three_json(self, points, faces):
        # A minimal three.js BufferGeometry JSON (position + index).
        flat_pos = [c for p in points for c in p]
        flat_idx = [i for f in faces for i in f]
        geometry = {
            'metadata': {'version': 4.5, 'type': 'BufferGeometry',
                         'generator': 'cad-engines'},
            'type': 'BufferGeometry',
            'data': {'attributes': {'position': {
                'itemSize': 3, 'type': 'Float32Array',
                'array': flat_pos, 'normalized': False}}},
        }
        if flat_idx:
            geometry['data']['index'] = {'type': 'Uint32Array',
                                         'array': flat_idx}
        payload = json.dumps(geometry).encode()
        return {'ok': True, 'format': 'three-json',
                'filename': 'shape.three.json',
                'contentType': 'application/json',
                'contentBase64': base64.b64encode(payload).decode()}

    def _freecad_export(self, points, faces, fmt):
        import tempfile
        import os
        try:
            m = FreeCADMesh.Mesh()
            for f in faces:
                m.addFacet(FreeCAD.Vector(*points[f[0]]),
                           FreeCAD.Vector(*points[f[1]]),
                           FreeCAD.Vector(*points[f[2]]))
            shape = Part.Shape()
            shape.makeShapeFromMesh(m.Topology, 0.1)
            with tempfile.NamedTemporaryFile(suffix=f'.{fmt}',
                                             delete=False) as fh:
                path = fh.name
            shape.exportStep(path) if fmt in ('step', 'stp') \
                else shape.exportBrep(path)
            with open(path, 'rb') as fh:
                data = fh.read()
            os.unlink(path)
            return {'ok': True, 'format': fmt, 'filename': f'shape.{fmt}',
                    'contentType': 'application/octet-stream',
                    'contentBase64': base64.b64encode(data).decode()}
        except Exception as e:
            return {'ok': False, 'error': f'freecad export failed: {e}'}


class SystemInfoResource:
    """Host specs for the node this worker runs on (res-1 remote
    inventory pull) — mirrors msci-engines' /system-info. stdlib-only,
    same response shape as the backend's endpoint."""

    def on_get(self, req, resp):
        import os
        import platform
        import shutil
        mem = {}
        try:
            with open('/proc/meminfo') as f:
                for line in f:
                    parts = line.split()
                    if parts and parts[0].rstrip(':') in (
                            'MemTotal', 'MemAvailable'):
                        mem[parts[0].rstrip(':')] = int(parts[1]) * 1024
        except (OSError, ValueError, IndexError):
            pass
        total = mem.get('MemTotal', 0)
        avail = mem.get('MemAvailable', 0)
        cg_limit = 0
        for path in ('/sys/fs/cgroup/memory.max',
                     '/sys/fs/cgroup/memory/memory.limit_in_bytes'):
            try:
                with open(path) as f:
                    raw = f.read().strip()
                if raw != 'max':
                    limit = int(raw)
                    cg_limit = 0 if limit >= (1 << 60) else limit
                break
            except (OSError, ValueError):
                continue
        try:
            usage = shutil.disk_usage('/')
            disk = {'totalBytes': usage.total, 'freeBytes': usage.free}
        except OSError:
            disk = {'totalBytes': 0, 'freeBytes': 0}
        process = {}
        try:
            with open('/proc/self/status') as f:
                for line in f:
                    if line.startswith(('VmRSS', 'VmHWM')):
                        key = 'residentMb' if line.startswith(
                            'VmRSS') else 'peakMb'
                        process[key] = round(
                            int(line.split()[1]) / 1024.0, 1)
        except (OSError, ValueError, IndexError):
            pass
        resp.media = [{'system-info': {
            'platform': {
                'systemType': platform.system(),
                'networkName': os.environ.get(
                    'HOSTNAME', platform.node()),
                'arch': platform.machine(),
                'isContainerized': True,
                'service': 'cad-engines',
            },
            'cpu': {
                'numLogicalCPUs': os.cpu_count() or 0,
                'numPhysicalCPUs': 0,
                'currentUsagePercent': 0,
            },
            'memory': {
                'total': total,
                'available': avail,
                'percentUsed': round(
                    (total - avail) * 100.0 / total, 1)
                if total else 0,
                'cgroupLimitBytes': cg_limit,
            },
            'disk': disk,
            'process': process,
        }}]


app = falcon.App()
app.add_route('/capability', CapabilityResource())
app.add_route('/system-info', SystemInfoResource())
app.add_route('/cad/import', ImportResource())
app.add_route('/cad/export', ExportResource())
