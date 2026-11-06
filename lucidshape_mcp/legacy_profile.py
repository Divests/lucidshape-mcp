"""Stage a fixed S9 optical preflight; JSON input can never supply shell code."""
from __future__ import annotations
import csv
import hashlib
import json
import math
from pathlib import Path

from .inputs import input_file, MAX_BYTES
from .scene_utils import _number, _read_json, condition_setup, validate_conditioned_bounds, stage

LABELS=('main_lens','bracket','heat_sink','heat_shield','pcba','AL','IN')
SOURCE_COUNT=12
LOW_SOURCE_COUNT=7
REGION_ROLES={'reference':'inner_lens_solid_reference','coating':'AL','transmitting':'IN'}


def source_frames(p):
    return [{'name':f'source_{i+2}','native_id':i+2,'position_mm':pos,
             'frame':{'x':[0,1,0],'y':[0,0,1],'z':[1,0,0]}}
            for i,pos in enumerate(p.get('positions_mm',[[0,0,0]]*SOURCE_COUNT))]


def operating_modes(p):
    frames=source_frames(p)
    return {'low':[s['name'] for s in frames[:LOW_SOURCE_COUNT]],'high':[s['name'] for s in frames]}


def gpu_groups(p):
    frames=source_frames(p)
    return {'low':[s['name'] for s in frames[:LOW_SOURCE_COUNT]],'additional':[s['name'] for s in frames[LOW_SOURCE_COUNT:]]}


def medium_specs(p):
    materials=p.get('materials',{})
    return [{'id':'PC','index':materials.get('IN',{}).get('index',1.584),'loss':.002},
            {'id':'glass','index':materials.get('main_lens',{}).get('index',1.473),'loss':.002}]


def sensor_spec():
    return {'bounds_deg':[-60,60,-20,10],'grid_uv':[1200,300],
            'frame':{'x':[0,1,0],'y':[0,0,1],'z':[1,0,0]}}


def output_ray_counts(n):
    return {'low':LOW_SOURCE_COUNT*n,'high':SOURCE_COUNT*n}



def prepare(requirements_path,manifest_path,include_rayfile_emitters=True):
    rp,req=_read_json(requirements_path)
    mp,manifest=_read_json(manifest_path)
    coordinate_scale=_number(req.get('native_diagnostics',{}).get('coordinate_scale',1),1,1000)
    if coordinate_scale!=1 and include_rayfile_emitters:
        raise ValueError('Coordinate conditioning requires explicit sampled-ray staging, not a native ray-file emitter')
    mode=req.get('native_diagnostics',{}).get('main_lens_boundary_mode','individual_surfaces')
    if mode not in ('individual_surfaces','closed_global','closed_local'):
        raise ValueError('Unsupported main-lens boundary mode')
    boundary_evidence=None
    if mode!='individual_surfaces':
        main=next((r for r in manifest['regions'] if r['label']=='main_lens'),None)
        if main is None or main.get('mesh') or not manifest.get('fresh_main_topology_report'):
            raise ValueError('Closed-volume diagnostic requires independently checked main-lens STEP')
        evidence_path,evidence=_read_json(manifest['fresh_main_topology_report'])
        job=evidence.get('job',{})
        checked=job.get('result',{}).get('regions',{}).get('main_lens',{})
        if evidence.get('status')!='succeeded' or job.get('status')!='succeeded' or job.get('template')!='inspect_optical_region_topology' or checked.get('source_sha256')!=main['sha256'] or checked.get('valid') is not True or checked.get('solids')!=1 or checked.get('shell_closed_flags')!=[True]:
            raise ValueError('Closed-volume diagnostic topology does not certify this main-lens snapshot')
        boundary_evidence={'report':str(evidence_path),'sha256':hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            'source_sha256':main['sha256'],'scope':'one valid closed solid; not optical accuracy certification'}
    if req['inner_lens'].get('aluminized_region_geometry_mapping_verified') is not True:
        raise ValueError('AL/IN region identity must be confirmed')
    candidates=manifest['regions']
    if len(candidates)!=8 or len({r['label'] for r in candidates})!=8:
        raise ValueError('Expected seven optical regions and one excluded reference solid')
    selected=[r for r in candidates if r.get('include_in_optical_scene') is True]
    if set(r['label'] for r in selected)!=set(LABELS):
        raise ValueError('Optical region set differs; reference solid must stay excluded')
    regions=[]
    for label in LABELS:
        r=next(r for r in selected if r['label']==label)
        if r.get('native_import_verified') is not True:
            raise ValueError('Native region import has not been verified')
        faces=r['cad_face_count']
        if isinstance(faces,bool) or not isinstance(faces,int) or not 1<=faces<=100000:
            raise ValueError('Invalid region face count')
        native_faces=r.get('native_top_level_object_count')
        mesh=r.get('mesh')
        valid_counts=(1,) if mesh else (faces,faces+1)
        if isinstance(native_faces,bool) or not isinstance(native_faces,int) or native_faces not in valid_counts:
            raise ValueError('Native region face count is missing or differs from preflight')
        p=input_file(r['path'],{'.stl','.lug'} if mesh else {'.stp','.step'})
        if hashlib.sha256(p.read_bytes()).hexdigest()!=r['sha256']:
            raise ValueError('Region file differs from verified snapshot')
        regions.append({'label':label,'path':str(p),'sha256':r['sha256'],
            'cad_faces':faces,'faces':native_faces,'mesh':mesh,'extension':p.suffix.lower() if mesh else '.stp',
            'native_bounding_box_mm':r.get('native_bounding_box_mm')})
    source=req['approved_source']
    ray=input_file(source['path'],{'.ray'})
    if hashlib.sha256(ray.read_bytes()).hexdigest()!=source['sha256_from_native_inspection']:
        raise ValueError('Ray file differs from approved inspected source')
    axes=input_file(req['model']['axes_inventory'],{'.csv'})
    with axes.open(encoding='utf-8-sig',newline='') as stream:
        rows=list(csv.DictReader(stream))
    if len(rows)!=12 or len({r['name'] for r in rows})!=12:
        raise ValueError('Expected twelve distinct source axes')
    positions=[]
    for i,row in enumerate(rows):
        # This tool intentionally supports only the confirmed parallel S9 frames.
        if row['name']!=f'轴系.{i+2}':
            raise ValueError('Source axis sequence differs')
        basis=[[_number(float(row[f'{axis}dir_{d}']),-1,1) for d in 'xyz'] for axis in 'xyz']
        if basis!=[[0.,1.,0.],[0.,0.,1.],[1.,0.,0.]]:
            raise ValueError('Source basis differs from the inspected S9 frame')
        positions.append([_number(float(row[f'origin_{d}_mm']),-10000,10000) for d in 'xyz'])
    materials={
        'main_lens':{'kind':'refractive','index':_number(req['main_lens']['refractive_index'],1.001,3)},
        'IN':{'kind':'refractive','index':_number(req['inner_lens']['refractive_index'],1.001,3)},
        'AL':{'kind':'specular','reflectance':_number(req['inner_lens']['aluminized_surface_reflectance'],0,1)},
        'bracket':{'kind':'Lambertian','reflectance':_number(req['other_surface_parameters']['Bracket']['reflectance'],0,1)},
        'heat_sink':{'kind':'Lambertian','reflectance':_number(req['other_surface_parameters']['HeatSink']['reflectance'],0,1)},
        'heat_shield':{'kind':'absorber'},'pcba':{'kind':'absorber'}}
    if req['surface_scattering_choice'].get('status')!='user_confirmed':
        raise ValueError('Ideal surface models must be confirmed')
    for label in ('HeatShield','PCBA'):
        r=req['other_surface_parameters'][label]
        if r.get('reflectance')!=0 or r.get('transmission')!=0:
            raise ValueError('This preflight requires the approved opaque absorber assumptions')
    if sum(Path(r['path']).stat().st_size for r in regions)+(ray.stat().st_size if include_rayfile_emitters else 0)>MAX_BYTES:
        raise ValueError('Optical input package exceeds 256 MiB')
    return {'requirements_path':str(rp),'region_manifest_path':str(mp),'regions':regions,
        'coordinate_scale':coordinate_scale,
        'main_lens_boundary_mode':mode,'main_lens_boundary_evidence':boundary_evidence,
        'materials':materials,'positions_mm':positions,'rayfile_path':str(ray),
        'rayfile_sha256':source['sha256_from_native_inspection'],
        'ray_count':source['ray_count'],'flux_per_led_lm':source['flux_per_led_lm'],
        'absorption_applied':False,'diagnostic_only':True,'include_rayfile_emitters':include_rayfile_emitters}






