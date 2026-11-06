"""Validate contiguous GPU provenance and sum declared operating-mode data."""
import hashlib,json,math
from pathlib import Path
from .inputs import input_file
from . import optical_scene
from .compatibility import operation

def prepare(report_path,allow_incomplete=False):
    path=input_file(report_path,{'.json'})
    if path.stat().st_size>4*1024*1024:raise ValueError('Batch report too large')
    report=json.loads(path.read_text(encoding='utf-8'))
    jobs=report.get('jobs',[])
    if report.get('status') not in (('succeeded_provisional','failed') if allow_incomplete else ('succeeded_provisional',)) or not 1<=len(jobs)<=100:
        raise ValueError('Require successful bounded batch report')
    if not report.get('canonical_requirements_unchanged'):raise ValueError('Requirements changed')
    first=jobs[0]['parameters'];source=jobs[0]['result']['coverage']['source_sha256']
    if first.get('absorption_backend')=='native outgoing-air absorbing mixture; 0.998 survival per exit':
        raise ValueError('This candidate material failed backward Fresnel validation; cannot merge or resume as valid requested physics')
    control=first.get('absorption_backend')=='zero_extra_absorption_control'
    groups=optical_scene.gpu_groups(first)
    for name in groups:
        optical_scene.identifier(name)
    settings=optical_scene.validate_sensor(optical_scene.sensor(first))
    output_groups={name:[name] for name in groups} if first.get('scene_schema_version') else {'low':['low'],'high':['low','additional']}
    cursor=0;inputs=[];flux={name:0. for name in groups};total=0
    for batch,job in enumerate(jobs):
        p=job['parameters'];r=job['result'];c=r['coverage']
        if job.get('status')!='succeeded' or operation(job.get('template'))!='trace_optical_gpu' or not r.get('native_GPU_execution_verified'):
            raise ValueError('Every merged batch must be native GPU verified')
        for key in ('regions','materials','positions_mm','sources','modes','sensor','material_library','scene_schema_version','coordinate_scale','rayfile_sha256','trace_mode','absorption_backend','zero_extra_absorption_control','applied_extra_absorption_fraction','fresnel_retained'):
            if p.get(key)!=first.get(key):raise ValueError('Batch physics or geometry differs')
        if control and (p.get('zero_extra_absorption_control') is not True or p.get('applied_extra_absorption_fraction')!=0 or p.get('fresnel_retained') is not True
                        or r.get('zero_extra_absorption_control') is not True or r.get('per_complete_traversal_absorption_fraction')!=0):
            raise ValueError('Control loss flags differ or are absent')
        if p.get('sample') or c['source_sha256']!=source or c['source_start']!=cursor or c['source_stop_exclusive']!=cursor+p['rays_per_led']:
            raise ValueError('Source coverage has a sample, gap, duplicate or overlap')
        cursor=c['source_stop_exclusive'];total+=r['total_input_rays']
        if set(r['beams'])!=set(groups):raise ValueError('Batch operating modes differ')
        for group in groups:
            spec=r['beams'][group];lid=input_file(spec['lid_file'],{'.lid'})
            digest=hashlib.sha256(lid.read_bytes()).hexdigest()
            if digest!=spec.get('lid_sha256'):raise ValueError('Batch LID changed or has no recorded digest')
            inputs.append({'path':str(lid),'sha256':digest,'name':f'{group}_{batch}.lid','group':group})
            flux[group]+=spec['flux_in_sensor_domain_lm']
    traced_sources=sum(len(members) for members in groups.values())
    if (not allow_incomplete and cursor!=first['ray_count']) or cursor>first['ray_count'] or total!=traced_sources*cursor:
        raise ValueError('Full source coverage is incomplete; cannot publish full-ray result')
    return {'report_path':str(path),'report_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'inputs':inputs,'batch_count':len(jobs),'rays_per_led':cursor,'total_input_rays':total,
        'source_sha256':source,'expected_flux':{name:sum(flux[g] for g in members) for name,members in output_groups.items()},
        'output_groups':output_groups,'operating_modes':optical_scene.modes(first),'sensor':settings,
        'input_rays_by_mode':{name:sum(len(groups[g]) for g in members)*cursor for name,members in output_groups.items()},
        'full_source_coverage':cursor==first['ray_count'],'covered_rays_per_led':cursor,
        'zero_extra_absorption_control':control,'absorption_backend':first.get('absorption_backend'),
        'matches_approved_fixed_traversal_model':False}

def stage(directory,p):
    total=0
    for entry in p['inputs']:
        data=Path(entry['path']).read_bytes();total+=len(data)
        if total>256*1024*1024 or hashlib.sha256(data).hexdigest()!=entry['sha256']:
            raise ValueError('LID package changed or exceeds limit')
        (directory/entry['name']).write_bytes(data)
    return p['inputs']

def render(template,p):
    groups=p.get('output_groups',{'low':['low'],'high':['low','additional']})
    calls=[]
    for entry in p['inputs']:
        targets=[i for i,members in enumerate(groups.values()) if entry['group'] in members]
        calls.append(f'if(!addData("{entry["name"]}",{optical_scene.native_vector(targets)})) return;')
    return optical_scene.render(template,p).replace('@@INPUTS@@','\n'.join(calls)).replace(
        '@@OUTPUT_NAMES@@','('+','.join(json.dumps(name) for name in groups)+')')

def read_result(directory,p,values):
    if len(values)==3:
        low,high,error=values
        rows=[['low',low],['high',high]]
    else:
        rows,error=values
    if [r[0] for r in rows]!=list(p['expected_flux']):raise ValueError('Merged output modes differ')
    if not math.isfinite(error) or error>2e-6:raise ValueError('Native merge changed per-cell sums')
    beams={}
    for name,flux in rows:
        if not math.isfinite(flux) or abs(flux-p['expected_flux'][name])>max(.00001,flux*2e-6):
            raise ValueError('Native merge changed absolute batch flux')
        path=directory/(name+'.lid')
        beams[name]={'lid_file':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'flux_in_sensor_domain_lm':flux}
    return {'beams':beams,'full_source_coverage':p.get('full_source_coverage',True),'total_input_rays':p['total_input_rays'],
        'input_rays_by_mode':p.get('input_rays_by_mode',{'low':7*p['rays_per_led'],'high':12*p['rays_per_led']}),
        'source_sha256':p['source_sha256'],'maximum_relative_cell_sum_error':error,
        'zero_extra_absorption_control':p.get('zero_extra_absorption_control',False),
        'absorption_backend':p.get('absorption_backend'),'matches_approved_fixed_traversal_model':False,
        'sensor':optical_scene.sensor(p),'regulatory_compliance_evaluated':False,'mesh_convergence_verified':False},[name+'.lid' for name in beams]
