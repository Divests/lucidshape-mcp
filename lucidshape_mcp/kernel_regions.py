"""Fixed read-only topology inspection inputs for the independent OCCT MCP worker."""
import hashlib
import json
import math
from pathlib import Path
from .inputs import input_file
from .optical_scene import identifier

LABELS=('inner_lens_solid_reference','AL','IN')

def prepare(manifest_path,points,additional_solid_regions=None,local_boundary_checks=False,reference_mesh_path=None,reference_triangle_indices=None,
            region_labels=None,region_roles=None,target_region=None):
    p=input_file(manifest_path,{'.json'})
    if p.stat().st_size>1024*1024:
        raise ValueError('Region manifest too large')
    manifest=json.loads(p.read_text(encoding='utf-8'))
    additional=additional_solid_regions or []
    labels=[identifier(r['label']) for r in manifest['regions']]
    if not 1<=len(labels)<=128 or len(set(labels))!=len(labels):
        raise ValueError('Expected distinct named geometry regions')
    if not isinstance(additional,list) or any(not isinstance(v,str) or v not in labels for v in additional) or len(additional)!=len(set(additional)):
        raise ValueError('Additional regions must be distinct labels present in the manifest')
    roles=region_roles if region_roles is not None else manifest.get('region_roles')
    if roles is None and set(LABELS)<=set(labels):
        roles={'reference':'inner_lens_solid_reference','coating':'AL','transmitting':'IN'}
    if roles is not None and (not isinstance(roles,dict) or set(roles)!={'reference','coating','transmitting'} or
            len(set(roles.values()))!=3 or any(value not in labels for value in roles.values())):
        raise ValueError('Partition roles require three distinct existing regions: reference, coating, transmitting')
    if roles and set(additional)&set(roles.values()):
        raise ValueError('Additional solids must not duplicate partition roles')
    selected=region_labels if region_labels is not None else (list(roles.values()) if roles else labels)
    if not isinstance(selected,list) or not selected or len(selected)!=len(set(selected)) or any(v not in labels for v in selected):
        raise ValueError('region_labels must select distinct existing regions')
    selected=list(dict.fromkeys([*selected,*additional]))
    if roles and not set(roles.values())<=set(selected):
        raise ValueError('All explicitly mapped partition roles must be selected')
    boundary=target_region or ('main_lens' if 'main_lens' in selected else None)
    if boundary is not None and boundary not in selected:
        raise ValueError('Boundary target must be one of the selected regions')
    if not isinstance(local_boundary_checks,bool):
        raise ValueError('local_boundary_checks must be boolean')
    if local_boundary_checks and (boundary is None or not points or len(points)>8):
        raise ValueError('Local boundary checks require an explicit boundary target and one to eight probe points')
    regions=[]
    for label in selected:
        matches=[r for r in manifest['regions'] if r['label']==label]
        if len(matches)!=1:
            raise ValueError('Missing or duplicate optical region')
        row=matches[0]
        source=input_file(row['path'],{'.stp','.step'})
        regions.append({'label':label,'source':str(source),'sha256':row['sha256'],'staged_name':label+'.stp'})
    points=points or []
    if not isinstance(points,list) or len(points)>100:
        raise ValueError('Expected at most 100 probe points')
    for xyz in points:
        if not isinstance(xyz,list) or len(xyz)!=3 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>100000 for v in xyz):
            raise ValueError('Invalid point coordinates')
    result={'region_manifest_path':str(p),'regions':regions,'probe_points_mm':points,
        'local_boundary_checks':local_boundary_checks,'region_roles':roles,'target_region':boundary,
        'additional_solid_regions':additional}
    if reference_mesh_path is not None:
        if not local_boundary_checks:
            raise ValueError('Reference mesh requires explicit local boundary checks')
        mesh=input_file(reference_mesh_path,{'.stl'})
        if mesh.stat().st_size>200*1024*1024:
            raise ValueError('Reference mesh exceeds diagnostic size limit')
        result['reference_mesh']={'source':str(mesh),'staged_name':'reference-mesh.stl',
            'sha256':hashlib.sha256(mesh.read_bytes()).hexdigest()}
    if reference_triangle_indices is not None:
        values=reference_triangle_indices
        if not reference_mesh_path or not isinstance(values,list) or not 1<=len(values)<=256 or any(isinstance(v,bool) or not isinstance(v,int) or not 0<=v<2000000 for v in values) or len(set(values))!=len(values):
            raise ValueError('Reference triangle indices require a mesh and one to 256 distinct valid indices')
        result['reference_triangle_indices']=values
    return result

def stage(directory,parameters):
    manifest=[]
    source_manifest=Path(parameters['region_manifest_path'])
    data=source_manifest.read_bytes()
    (directory/'source-manifest.json').write_bytes(data)
    manifest.append({'source':str(source_manifest),'staged_name':'source-manifest.json',
        'source_sha256':hashlib.sha256(data).hexdigest(),'source_bytes':len(data)})
    for row in parameters['regions']+([parameters['reference_mesh']] if parameters.get('reference_mesh') else []):
        data=Path(row['source']).read_bytes()
        if hashlib.sha256(data).hexdigest()!=row['sha256']:
            raise ValueError('Optical region differs from verified snapshot')
        (directory/row['staged_name']).write_bytes(data)
        manifest.append({'source':row['source'],'source_sha256':row['sha256'],
            'source_bytes':len(data),'staged_name':row['staged_name']})
    return manifest
