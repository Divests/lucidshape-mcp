"""Bounded native GPU batches with explicit sources, modes and flux accounting."""
from __future__ import annotations
import hashlib
import json
import math
import random
import re
import struct
from pathlib import Path
from . import optical_scene
s9_scene = optical_scene  # Deprecated module attribute for existing integrations.
from .path_sampling import HEADER, RAY
from .inputs import MAX_BYTES

def integer(value, lo, hi, name):
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f'{name} must be an integer in {lo}..{hi}')
    return value

REJECTION_REASON='GPU fixed-traversal-loss candidate disabled: outgoing-air absorption also attenuates entry Fresnel reflection; per-complete-traversal physics validation failed. Existing CPU path-history backend remains available.'
CONTROL_BACKEND='zero_extra_absorption_control'

def prepare(requirements_path, manifest_path, rays, start, sample, seed, cpu_reference, zero_extra_absorption_control=False):
    """Only an explicitly requested zero-extra-loss control may use this GPU path."""
    if zero_extra_absorption_control is not True:
        raise ValueError(REJECTION_REASON)
    p=optical_scene.prepare(requirements_path,manifest_path,include_rayfile_emitters=False)
    req=json.loads(Path(requirements_path).read_text(encoding='utf-8'))
    if not p.get('scene_schema_version') and any(req[x].get('absorption_fraction')!=.002 or req[x].get('absorption_definition_confirmed') is not True
           for x in ('main_lens','inner_lens')):
        raise ValueError('Control must reference the unchanged approved 0.2 percent traversal requirements')
    integer(rays,100,500000,'rays_per_led')
    integer(start,0,p['ray_count']-rays,'source_start')
    integer(seed,0,2147483647,'sampling_seed')
    if not isinstance(sample,bool) or not isinstance(cpu_reference,bool) or (sample and start):
        raise ValueError('Invalid GPU sampling/reference selection')
    p.update(rays_per_led=rays,source_start=start,sample=sample,sampling_seed=seed,
        trace_mode=20 if cpu_reference else 13,absorption_applied=False,
        absorption_backend=CONTROL_BACKEND,zero_extra_absorption_control=True,
        approved_per_traversal_absorption_fraction=None if p.get('scene_schema_version') else .002,applied_extra_absorption_fraction=0.,
        fresnel_retained=True,matches_approved_fixed_traversal_model=False,
        requirements_sha256=hashlib.sha256(Path(requirements_path).read_bytes()).hexdigest(),diagnostic_only=True)
    return p

def stage(directory,p):
    inputs=optical_scene.stage(directory,p)
    source=Path(p['rayfile_path']);data=source.read_bytes()
    if hashlib.sha256(data).hexdigest()!=p['rayfile_sha256']:
        raise ValueError('Approved ray file changed')
    a,b,c,d,flux,total,flag=HEADER.unpack_from(data)
    if (a,b,c,d,flag)!=(0,0,0,0,-1) or total!=p['ray_count'] or len(data)!=28+28*total:
        raise ValueError('Unsupported source RAY layout')
    # The installed reader uses ABSOLUTE payload lumen weights. The header is
    # not a normalization instruction. Preserve each weight for full coverage.
    w=RAY.unpack_from(data,28)[6]
    if not math.isfinite(w) or w<=0 or any(row[6]!=w for row in RAY.iter_unpack(memoryview(data)[28:])) or abs(w*total-flux)>flux*1e-6:
        raise ValueError('This verified GPU batch format requires equal original ray weights')
    n=p['rays_per_led']
    if sum(f.stat().st_size for f in directory.iterdir() if f.is_file())+28+28*n>MAX_BYTES:
        raise ValueError('Geometry and staged GPU source exceed 256 MiB')
    indices=(random.Random(p['sampling_seed']).sample(range(total),n) if p['sample']
             else range(p['source_start'],p['source_start']+n))
    chunk_flux=flux if p['sample'] else flux*n/total
    native_weight=flux/n if p['sample'] else w
    out=directory/'source.ray'
    digest=hashlib.sha256()
    with out.open('wb') as stream:
        stream.write(HEADER.pack(0,0,0,0,chunk_flux,n,-1))
        for index in indices:
            x,y,z,dx,dy,dz,weight=RAY.unpack_from(data,28+28*index)
            if any(not math.isfinite(v) for v in (x,y,z,dx,dy,dz)) or abs(dx*dx+dy*dy+dz*dz-1)>1e-5:
                raise ValueError('Invalid source direction or coordinate')
            s=p['coordinate_scale']
            stream.write(RAY.pack(x*s,y*s,z*s,dx,dy,dz,native_weight))
            digest.update(struct.pack('<I',index))
    info={'source_sha256':p['rayfile_sha256'],'source_count':total,'rays_per_led':n,
        'source_start':None if p['sample'] else p['source_start'],
        'source_stop_exclusive':None if p['sample'] else p['source_start']+n,
        'sampling':'uniform without replacement; full-power estimate' if p['sample'] else 'contiguous disjoint coverage; partial absolute flux',
        'index_sha256':digest.hexdigest(),
        'batch_flux_per_led_lm':struct.unpack('<f',struct.pack('<f',chunk_flux))[0],
        'full_flux_per_led_lm':flux,'coordinate_scale':p['coordinate_scale'],
        'absolute_payload_weight_lm':native_weight,'source_ray_positions_scaled_only':True,
        'staged_sha256':hashlib.sha256(out.read_bytes()).hexdigest()}
    (directory/'coverage.json').write_text(json.dumps(info,indent=2)+'\n',encoding='utf-8')
    inputs.append({'source':str(source),'staged_name':'source.ray','derived_source':info})
    return inputs

def render(template,p,templates):
    if p.get('absorption_backend')==CONTROL_BACKEND:
        setup=(templates/'preflight_optical_scene.do').read_text(encoding='ascii').split('object data=LIDData')[0]
        setup=setup.replace('// Extra per-traversal absorption is intentionally absent, never a final simulation.',
            '// USER AUTHORIZED CONTROL: no extra traversal absorption; native Fresnel/TIR retained.')
        setup=optical_scene.render(optical_scene.condition_setup(setup,p['coordinate_scale']),p)
        source_calls=[]
        groups=optical_scene.gpu_groups(p)
        for group,members in enumerate(groups.values()):
            for source in optical_scene.sources(p):
                if source['name'] in members:
                    source_calls.append(f'if(group=={group}) '+optical_scene.source_call(source,p['coordinate_scale']))
        template=optical_scene.render(template,p)
        return (template.replace('@@SCENE_SETUP@@',setup).replace('@@BATCH_SOURCES@@','\n'.join(source_calls))
            .replace('@@GROUP_COUNT@@',str(len(groups)))
            .replace('@@GROUP_NAMES@@','('+','.join(json.dumps(name) for name in groups)+')')
            .replace('@@GROUP_SIZES@@',optical_scene.native_vector([len(v) for v in groups.values()]))
            .replace('@@SCALE@@',format(p['coordinate_scale'],'.17g')).replace('@@MODE@@',str(p['trace_mode']))
            .replace('@@COUNT@@',str(p['rays_per_led']))
            .replace('@@SOURCE_FLUX@@',format(p['flux_per_led_lm']*(1 if p['sample'] else p['rays_per_led']/p['ray_count']),'.17g')))
    raise ValueError(REJECTION_REASON)


def read_result(directory,p,values):
    regions,bounds,calibration,stats,mode=values
    optical_scene.validate_conditioned_bounds(bounds,p)
    groups=optical_scene.gpu_groups(p)
    if len(regions)!=len(p['regions']) or mode!=p['trace_mode'] or len(stats)!=len(groups):
        raise ValueError('GPU scene report structure differs')
    for row,spec in zip(regions,p['regions']):
        if row[:2]!=[spec['label'],spec['faces']] or row[2] not in (0,1):raise ValueError('GPU region import differs')
        if 'native_top_level_object_count' in spec and row[1]+row[2]!=spec['native_top_level_object_count']:
            raise ValueError('GPU top-level geometry count differs')
        refractive=p['materials'][spec['label']]['kind']=='refractive' or (not p.get('scene_schema_version') and spec['label'] in ('main_lens','IN'))
        expected=('Refractive' if p.get('absorption_backend')==CONTROL_BACKEND else 'ComboActor') if refractive else p['materials'][spec['label']]['kind']
        if refractive and row[3]!=expected:raise ValueError('Lens material differs from declared backend')
        if p.get('absorption_backend')==CONTROL_BACKEND:
            classes={'refractive':'Refractive','specular':'Specular','Lambertian':'LambertianReflective','absorber':'Absorber'}
            if row[3]!=classes[p['materials'][spec['label']]['kind']]:
                raise ValueError('Native actor differs from declared control material')
    info=json.loads((directory/'coverage.json').read_text())
    expected=info['batch_flux_per_led_lm']
    if not math.isfinite(calibration) or abs(calibration-expected)>max(1e-6,expected*2e-5):
        raise ValueError('Native source normalization failed independent header flux calibration')
    log=(directory/'engine.log').read_text(encoding='utf-8',errors='replace').lower()
    if 'switching to' in log:raise ValueError('GPU trace silently fell back to another mode')
    if mode==13 and (log.count('nurbs intersection type is 13 (graphics card)')!=len(groups) or log.count('gpu intersection time')!=len(groups)):
        raise ValueError('Both GPU traces require native execution/timing evidence')
    counts=re.findall(r'source\[\d+\].*?"led axis (\d+)".*?with ([\d,]+) rays',log)
    launched=[s['native_id'] for members in groups.values() for s in optical_scene.sources(p) if s['name'] in members]
    if [(int(i),int(n.replace(',',''))) for i,n in counts]!=[(i,p['rays_per_led']) for i in launched]:
        raise ValueError('Native per-LED launch counts do not match batch coverage')
    beams={}
    for ordinal,(row,(group,members)) in enumerate(zip(stats,groups.items())):
        size=len(members)
        g,n,lo,hi,flux=row
        if g!=ordinal or n!=size*p['rays_per_led'] or not all(math.isfinite(v) for v in (lo,hi,flux)) or not 0<=lo<=hi or not 0<flux<=expected*size*1.0001:
            raise ValueError('Invalid GPU photometry or energy budget')
        beams[group]={'lid_file':str(directory/(group+'.lid')),'lid_sha256':hashlib.sha256((directory/(group+'.lid')).read_bytes()).hexdigest(),
            'flux_in_sensor_domain_lm':flux,'minimum_cd':lo,'maximum_cd':hi,'source_count':size}
    control=p.get('absorption_backend')==CONTROL_BACKEND
    return {'simulation_run':True,'native_GPU_execution_verified':mode==13,'trace_mode':mode,
        'total_input_rays':len(launched)*p['rays_per_led'],'source_count':len(optical_scene.sources(p)),
        'operating_modes':groups,'coverage':info,'native_source_calibration_lm':calibration,
        'beams':beams,'per_complete_traversal_absorption_fraction':0. if control else .002,
        'absorption_backend':p.get('absorption_backend'),
        'zero_extra_absorption_control':control,'matches_approved_fixed_traversal_model':False,
        'sensor_rejected_ray_count':sum(int(n.replace(',','')) for n in re.findall(r'([\d,]+) rays were rejected by sensors',log)),
        'sensor_rejection_scope':'native log diagnostic; cause and contribution not classified',
        'absorption_estimator':'user-authorized control: no extra traversal absorption; Fresnel/TIR retained' if control else 'REJECTED outgoing-air mixture',
        'full_source_coverage':not p['sample'] and p['source_start']==0 and p['rays_per_led']==info['source_count'],
        'sensor':optical_scene.sensor(p),'coordinate_scale':p['coordinate_scale'],
        'generalized_native_backend_validated':False if p.get('scene_schema_version') else None,
        'mesh_convergence_verified':False,'regulatory_compliance_evaluated':False,
        'studio_modified':False,'CATIA_connected':False},[name+'.lid' for name in groups]+['coverage.json']
