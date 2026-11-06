"""Project-independent scene configuration, validated before generating native code.

The historical requirements/regions layout is parsed only by legacy_profile.
The general schema uses explicit materials, region bindings, source frames,
operating modes and a far-field sensor. No configuration contains shell code.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import re
import struct

from . import legacy_profile
from .inputs import input_file, MAX_BYTES

LABELS = legacy_profile.LABELS  # Deprecated import compatibility, never a generic whitelist.
from .scene_utils import condition_setup, validate_conditioned_bounds, stage as stage_files
from .scene_utils import _number as number, _read_json as read_json
MATERIAL_CLASSES = {'refractive': 'Refractive', 'specular': 'Specular',
                    'Lambertian': 'LambertianReflective', 'absorber': 'Absorber'}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', value):
        raise ValueError('Identifiers must be 1..64 ASCII letters, digits, underscores or hyphens, beginning with a letter')
    return value


def integer(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{name} must be an integer in {low}..{high}')
    return value


def vector(value, limit=100000):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError('Expected a three-component vector')
    return [number(v, -limit, limit) for v in value]


def frame(value):
    if not isinstance(value, dict) or set(value) != {'x', 'y', 'z'}:
        raise ValueError('A frame must explicitly contain x, y and z unit vectors')
    x, y, z = (vector(value[a], 1) for a in 'xyz')
    dot = lambda a, b: sum(v*w for v, w in zip(a, b))
    cross = [x[1]*y[2]-x[2]*y[1], x[2]*y[0]-x[0]*y[2], x[0]*y[1]-x[1]*y[0]]
    if any(abs(dot(v, v)-1) > 1e-7 for v in (x, y, z)) or any(
            abs(dot(a, b)) > 1e-7 for a, b in ((x,y),(x,z),(y,z))) or max(abs(a-b) for a,b in zip(cross,z)) > 1e-7:
        raise ValueError('Frame must be orthonormal and right-handed; no scale or reflection is inferred')
    return dict(x=x, y=y, z=z)


def checked_file(path, digest, suffixes):
    p = input_file(path, suffixes)
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('A lowercase SHA256 of the inspected source is required')
    if p.stat().st_size > MAX_BYTES or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
        raise ValueError('Input differs from its recorded SHA256 or exceeds 256 MiB')
    return p


def _prepare(requirements_path, manifest_path, include_rayfile_emitters=True):
    rp, config = read_json(requirements_path)
    if 'schema_version' not in config:
        # Strict historical checks remain in one explicit compatibility adapter.
        p = legacy_profile.prepare(requirements_path, manifest_path, include_rayfile_emitters)
        p['compatibility_profile'] = 'legacy_s9'
        return p
    if config.get('schema_version') != 'optical-scene/1':
        raise ValueError('Unsupported optical scene schema_version')
    permitted = {'schema_version','name','length_unit','materials','region_materials','rayfile','sources','modes','sensor','diagnostics'}
    if set(config) - permitted:
        raise ValueError('Unknown scene fields: ' + ', '.join(sorted(set(config)-permitted)))
    if config.get('length_unit') != 'mm':
        raise ValueError('General scene geometry and ray positions must explicitly use mm')
    name = identifier(config['name'])
    mp, manifest = read_json(manifest_path)
    raw_materials = config['materials']
    if not isinstance(raw_materials, dict) or not 1 <= len(raw_materials) <= 64:
        raise ValueError('Expected 1..64 named materials')
    library = {}
    for key, raw in raw_materials.items():
        identifier(key)
        kind = raw.get('kind')
        allowed = {'refractive': {'kind','index','traversal_absorption_fraction'},
                   'specular': {'kind','reflectance'}, 'Lambertian': {'kind','reflectance'}, 'absorber': {'kind'}}
        if kind not in allowed or set(raw) != allowed[kind]:
            raise ValueError('Material fields must exactly match refractive/specular/Lambertian/absorber schema')
        m = {'kind': kind}
        if kind == 'refractive':
            m.update(index=number(raw['index'], 1.001, 3),
                     traversal_absorption_fraction=number(raw['traversal_absorption_fraction'], 0, 1))
        elif kind != 'absorber':
            m['reflectance'] = number(raw['reflectance'], 0, 1)
        library[key] = m
    candidates = manifest['regions']
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 128:
        raise ValueError('Expected 1..128 named geometry regions')
    labels = [identifier(row['label']) for row in candidates]
    if len(set(labels)) != len(labels):
        raise ValueError('Duplicate region labels')
    selected = [r for r in candidates if r.get('include_in_optical_scene') is True]
    bindings = config['region_materials']
    if not selected or set(bindings) != {r['label'] for r in selected} or any(v not in library for v in bindings.values()):
        raise ValueError('Every included region must have exactly one named material; excluded references must have none')
    regions = []
    for r in selected:
        if r.get('native_import_verified') is not True:
            raise ValueError('Native region import must be verified before scene construction')
        faces = integer(r['cad_face_count'], 1, 100000, 'cad_face_count')
        native = integer(r['native_top_level_object_count'], 1, 100001, 'native_top_level_object_count')
        mesh = r.get('mesh')
        if native not in ((1,) if mesh else (faces, faces+1)):
            raise ValueError('Native region face count differs from the inspected snapshot')
        path = checked_file(r['path'], r['sha256'], {'.stl','.lug'} if mesh else {'.stp','.step'})
        regions.append({'label':r['label'], 'path':str(path), 'sha256':r['sha256'], 'cad_faces':faces,
                        'faces':1 if mesh else faces, 'native_top_level_object_count':native,
                        'mesh':mesh, 'extension':path.suffix.lower() if mesh else '.stp',
                        'native_bounding_box_mm':r.get('native_bounding_box_mm'), 'material_id':bindings[r['label']]})
    source = config['rayfile']
    if set(source) != {'path','sha256','ray_count','flux_lm'}:
        raise ValueError('rayfile requires path, sha256, ray_count and flux_lm from inspection')
    ray = checked_file(source['path'], source['sha256'], {'.ray'})
    count = integer(source['ray_count'], 1, 20000000, 'ray_count')
    flux = number(source['flux_lm'], 1e-9, 1e9)
    with ray.open('rb') as stream:
        header=stream.read(28)
    if len(header)!=28:
        raise ValueError('Incomplete source RAY header')
    a,b,c,d,actual_flux,actual_count,flag=struct.unpack('<5fIf',header)
    if (a,b,c,d,flag)!=(0,0,0,0,-1) or actual_count!=count or ray.stat().st_size!=28+28*count:
        raise ValueError('Declared source count or native non-spectral RAY layout differs')
    if not math.isfinite(actual_flux) or abs(actual_flux-flux)>max(1e-4,flux*2e-6):
        raise ValueError('Declared source flux differs from the inspected RAY header')
    raw_sources = config['sources']
    if not isinstance(raw_sources,list) or not 1 <= len(raw_sources) <= 64:
        raise ValueError('Expected 1..64 source frames sharing the inspected ray file')
    sources = []
    for i, raw in enumerate(raw_sources):
        if set(raw) != {'id','position_mm','frame'}:
            raise ValueError('Each source requires id, position_mm and frame')
        sources.append({'name':identifier(raw['id']), 'native_id':i+1,
                        'position_mm':vector(raw['position_mm']), 'frame':frame(raw['frame'])})
    ids = [s['name'] for s in sources]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate source identifiers')
    modes = config['modes']
    if not isinstance(modes,dict) or not 1 <= len(modes) <= 16:
        raise ValueError('Expected 1..16 operating modes')
    for mode, members in modes.items():
        identifier(mode)
        if not isinstance(members,list) or not members or any(m not in ids for m in members) or len(members)!=len(set(members)):
            raise ValueError('A mode must contain distinct declared source identifiers')
    if set().union(*(set(m) for m in modes.values())) != set(ids):
        raise ValueError('Every declared source must belong to at least one mode')
    sensor = validate_sensor(config['sensor'])
    diagnostics = config.get('diagnostics',{})
    if set(diagnostics)-{'coordinate_scale'}:
        raise ValueError('General scenes support only explicit coordinate_scale diagnostics; no inferred boundary repair')
    scale = number(diagnostics.get('coordinate_scale',1),1,1000)
    if scale != 1 and include_rayfile_emitters:
        raise ValueError('Coordinate conditioning requires explicit sampled-ray staging')
    if sum(Path(r['path']).stat().st_size for r in regions) + ray.stat().st_size > MAX_BYTES:
        raise ValueError('Geometry and source package exceed 256 MiB')
    return {'scene_schema_version':'optical-scene/1','scene_name':name,
            'requirements_path':str(rp),'requirements_sha256':hashlib.sha256(rp.read_bytes()).hexdigest(),
            'region_manifest_path':str(mp),'region_manifest_sha256':hashlib.sha256(mp.read_bytes()).hexdigest(),
            'regions':regions,'material_library':library,
            'materials':{r['label']:library[r['material_id']] for r in regions},
            'sources':sources,'modes':modes,'sensor':sensor,'positions_mm':[s['position_mm'] for s in sources],
            'rayfile_path':str(ray),'rayfile_sha256':source['sha256'],'ray_count':count,'flux_per_led_lm':flux,
            'coordinate_scale':scale,'main_lens_boundary_mode':'individual_surfaces','main_lens_boundary_evidence':None,
            'absorption_applied':False,'diagnostic_only':True,'include_rayfile_emitters':include_rayfile_emitters}


def prepare(requirements_path, manifest_path, include_rayfile_emitters=True):
    try:
        return _prepare(requirements_path,manifest_path,include_rayfile_emitters)
    except (KeyError,TypeError,AttributeError) as error:
        raise ValueError('Malformed or incomplete scene configuration: '+str(error)) from error


def stage(directory,p):
    inputs=stage_files(directory,p)
    if p.get('scene_schema_version'):
        for key,digest,name in (('requirements_path','requirements_sha256','scene-config.json'),
                                ('region_manifest_path','region_manifest_sha256','source-regions.json')):
            source=input_file(p[key],{'.json'});data=source.read_bytes()
            if hashlib.sha256(data).hexdigest()!=p[digest]:
                raise ValueError('Scene configuration changed before staging')
            (directory/name).write_bytes(data)
            inputs.append({'source':str(source),'source_sha256':p[digest],'staged_name':name,'source_bytes':len(data)})
    return inputs


def sources(p):
    return p['sources'] if 'sources' in p else legacy_profile.source_frames(p)


def modes(p):
    return p['modes'] if 'modes' in p else legacy_profile.operating_modes(p)


def gpu_groups(p):
    if p.get('scene_schema_version'):
        return modes(p)
    return legacy_profile.gpu_groups(p)


def sensor(p):
    return p['sensor'] if 'sensor' in p else legacy_profile.sensor_spec()


def validate_sensor(spec):
    if not isinstance(spec,dict) or set(spec)!={'bounds_deg','grid_uv','frame'}:
        raise ValueError('Sensor requires bounds_deg, grid_uv and frame')
    bounds=spec['bounds_deg'];grid=spec['grid_uv']
    if not isinstance(bounds,list) or len(bounds)!=4 or not isinstance(grid,list) or len(grid)!=2:
        raise ValueError('Invalid sensor bounds or cell counts')
    bounds=[number(v,-90,90) for v in bounds]
    grid=[integer(v,2,4096,'sensor grid') for v in grid]
    if not bounds[0]<bounds[1] or not bounds[2]<bounds[3] or grid[0]*grid[1]>1000000:
        raise ValueError('Invalid sensor angular range or cell budget')
    return {'bounds_deg':bounds,'grid_uv':grid,'frame':frame(spec['frame'])}


def media(p):
    if not p.get('scene_schema_version'):
        return legacy_profile.medium_specs(p)
    used = {r['material_id'] for r in p['regions']}
    return [{'id':key,'index':m['index'],'loss':m['traversal_absorption_fraction']}
            for key,m in p['material_library'].items() if key in used and m['kind']=='refractive']


def validate_history_media(p):
    items = media(p)
    if any(abs(a['index']-b['index']) < 2e-6 for i,a in enumerate(items) for b in items[i+1:]):
        raise ValueError('Path-history backend cannot distinguish different materials with the same refractive index; share one material ID or use a different validated backend')
    return items


def native_vector(values):
    return '('+','.join(format(float(x),'.17g') for x in values)+')'


def transform(frame_spec, values):
    return [sum(frame_spec[a][i]*values[j] for j,a in enumerate('xyz')) for i in range(3)]


def source_call(s, scale=1):
    return f'addSource({s["native_id"]},{native_vector([v*scale for v in s["position_mm"]])},{native_vector(s["frame"]["z"])},{native_vector(s["frame"]["x"])});'


def render(template, p):
    calls = []
    for i,r in enumerate(p.get('regions',[])):
        label = identifier(r['label']); m = p['materials'][label]
        if m['kind']=='refractive':
            expr=f'Refractive("{label} refraction, diagnostic zero extra absorption",air,Medium("{label}",{m["index"]:.17g},0),1,1)'
        elif m['kind']=='specular':
            expr=f'Specular("{label} ideal specular",{m["reflectance"]:.17g})'
        elif m['kind']=='Lambertian':
            expr=f'LambertianReflective("{label} ideal diffuse",{m["reflectance"]:.17g},60)'
        else:
            expr=f'Absorber("{label} opaque")'
        boundary = {'individual_surfaces':0,'closed_global':1,'closed_local':2}[p.get('main_lens_boundary_mode','individual_surfaces')] if label=='main_lens' else 0
        calls.append(f'if(!addRegion("region_{i}{r["extension"]}","{label}",{r["faces"]},{expr},{boundary})) return;')
    settings = sensor(p)
    replacements = {'@@REGIONS@@':'\n'.join(calls),'@@SOURCES@@':'\n'.join(source_call(s) for s in sources(p)) if p.get('include_rayfile_emitters',True) else '',
                    '@@BOUNDARY_MODE@@':str({'individual_surfaces':0,'closed_global':1,'closed_local':2}[p.get('main_lens_boundary_mode','individual_surfaces')]),
                    '@@GRID_U@@':str(settings['grid_uv'][0]),'@@GRID_V@@':str(settings['grid_uv'][1]),
                    '@@SENSOR_BOUNDS@@':native_vector(settings['bounds_deg']),
                    '@@SENSOR_Z@@':native_vector(settings['frame']['z']),'@@SENSOR_X@@':native_vector(settings['frame']['x'])}
    for a,b in replacements.items():
        template=template.replace(a,b)
    return template
