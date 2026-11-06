"""Fixed native checkpoint traces in an isolated optical boundary, exposed by MCP."""
import hashlib
import json
import math
from pathlib import Path
from . import optical_scene
from .inputs import input_file


def prepare(requirements_path,manifest_path,failure_path,start_segments,attempts,include_neighbor_directions=False,
            boundary_region='main_lens',coordinate_scale=1,region_labels=None):
    if isinstance(coordinate_scale,bool) or not isinstance(coordinate_scale,(int,float)) or not math.isfinite(coordinate_scale) or not 1<=coordinate_scale<=1000:
        raise ValueError('Diagnostic coordinate scale must be between one and 1000')
    if not isinstance(include_neighbor_directions,bool):
        raise ValueError('include_neighbor_directions must be boolean')
    if isinstance(attempts,bool) or not isinstance(attempts,int) or not 1<=attempts<=32:
        raise ValueError('Replay attempts must be one to 32')
    if not isinstance(start_segments,list) or not 1<=len(start_segments)<=4 or any(isinstance(v,bool) or not isinstance(v,int) or not 0<=v<256 for v in start_segments) or len(set(start_segments))!=len(start_segments):
        raise ValueError('Expected one to four distinct checkpoint indices')
    path=input_file(failure_path,{'.json'})
    data=path.read_bytes()
    if len(data)>1024*1024:
        raise ValueError('Failure path exceeds one MiB')
    fields=json.loads(data)['List']
    source=fields['p7']
    if not isinstance(source,list) or not 1<=len(source)<=256:
        raise ValueError('Invalid failed path')
    parameters=optical_scene.prepare(requirements_path,manifest_path,include_rayfile_emitters=False)
    if parameters.get('scene_schema_version'):
        lens_label=optical_scene.identifier(boundary_region)
        labels=region_labels or [lens_label]
        if not isinstance(labels,list) or lens_label not in labels or len(set(labels))!=len(labels) or any(label not in parameters['materials'] for label in labels):
            raise ValueError('Boundary region selection must include the declared refractive region')
        if parameters['materials'][lens_label]['kind']!='refractive' or any(parameters['materials'][label]['kind']=='refractive' and parameters['materials'][label]['index']!=parameters['materials'][lens_label]['index'] for label in labels):
            raise ValueError('Boundary replay isolates one refractive medium and optional reflective surfaces')
    else:
        if boundary_region not in ('main_lens','inner_lens'):
            raise ValueError('Legacy boundary region must be main_lens or inner_lens')
        labels=('main_lens',) if boundary_region=='main_lens' else ('AL','IN')
        lens_label='main_lens' if boundary_region=='main_lens' else 'IN'
    parameters['regions']=[r for r in parameters['regions'] if r['label'] in labels]
    glass=parameters['materials'][lens_label]['index']
    starts=[]
    for index in start_segments:
        if index>=len(source):
            raise ValueError('Checkpoint is outside failure path')
        p,v,length,intensity,n=source[index]
        values=[*p,*v,length,intensity,n]
        if len(p)!=3 or len(v)!=3 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in values) or length<=0 or intensity<0 or max(map(abs,p))>100000 or abs(sum(x*x for x in v)-1)>1e-5:
            raise ValueError('Invalid checkpoint ray')
        if abs(n-1)>1e-6 and abs(n-glass)>1e-6:
            raise ValueError('Checkpoint must use air or the approved index of the selected lens')
        offset=min(.05,length/2)
        starts.append({'segment_index_zero_based':index,'position_mm':[p[j]+offset*v[j] for j in range(3)],
            'direction':v,'index':n,'start_offset_mm':offset,'intensity':1.0,'direction_variant':0})
        if include_neighbor_directions:
            original=starts[-1]
            axis=(0,0,1) if abs(v[2])<.9 else (0,1,0)
            u=(v[1]*axis[2]-v[2]*axis[1],v[2]*axis[0]-v[0]*axis[2],v[0]*axis[1]-v[1]*axis[0])
            size=math.sqrt(sum(x*x for x in u));u=tuple(x/size for x in u)
            w=(v[1]*u[2]-v[2]*u[1],v[2]*u[0]-v[0]*u[2],v[0]*u[1]-v[1]*u[0])
            for variant,tangent in enumerate((u,tuple(-x for x in u),w,tuple(-x for x in w)),1):
                direction=[v[j]*math.cos(math.radians(.001))+tangent[j]*math.sin(math.radians(.001)) for j in range(3)]
                size=math.sqrt(sum(x*x for x in direction));direction=[x/size for x in direction]
                starts.append({**original,'direction':direction,'direction_variant':variant,'perturbation_deg':.001})
    parameters.update(failure_path=str(path),failure_sha256=hashlib.sha256(data).hexdigest(),
        start_rays=starts,replay_attempts=attempts,source_axis=fields['p0'],source_sample=fields['p1'],
        scene_scope=boundary_region+'_only',boundary_region=boundary_region,lens_material_label=lens_label,
        coordinate_scale=float(coordinate_scale),diagnostic_only=True,absorption_applied=False)
    return parameters


def stage(directory,parameters):
    rows=optical_scene.stage(directory,parameters)
    data=Path(parameters['failure_path']).read_bytes()
    if hashlib.sha256(data).hexdigest()!=parameters['failure_sha256']:
        raise ValueError('Failed path changed before staging')
    (directory/'source-failure.json').write_bytes(data)
    rows.append({'source':parameters['failure_path'],'staged_name':'source-failure.json',
        'source_sha256':parameters['failure_sha256'],'source_bytes':len(data)})
    return rows


def render(template,parameters):
    setup=(Path(__file__).with_name('templates')/'preflight_optical_scene.do').read_text(encoding='ascii').split('object data=LIDData')[0]
    setup=optical_scene.render(setup,parameters)
    label=parameters.get('lens_material_label','main_lens')
    glass=parameters['materials'][label]['index']
    medium_expression=f'Medium("{label}",{glass:.17g},0)'
    if setup.count(medium_expression)!=1:
        raise ValueError('Cannot share the exact material medium with checkpoint rays')
    for region in parameters['regions']:
        name=region['label'];material=parameters['materials'][name]
        if material['kind']=='refractive':
            setup=setup.replace(f'Medium("{name}",{material["index"]:.17g},0)','replayLens')
    setup=f'object replayLens={medium_expression};\n'+setup
    scale=parameters.get('coordinate_scale',1)
    setup=optical_scene.condition_setup(setup,scale)
    def vector(values):
        return '('+','.join(format(float(value),'.17g') for value in values)+')'
    starts=[]
    for row in parameters['start_rays']:
        starts.append(f'({row["segment_index_zero_based"]},{vector([v*scale for v in row["position_mm"]])},{vector(row["direction"])},{row["index"]:.17g})')
    return template.replace('@@SCENE_SETUP@@',setup).replace('@@REPLAY_STARTS@@','('+','.join(starts)+')').replace('@@REPLAY_ATTEMPTS@@',str(parameters['replay_attempts']))


def read_result(values,parameters):
    regions,records=values[:2]
    bounds=values[2] if len(values)>2 else []
    scale=parameters.get('coordinate_scale',1)
    label=parameters.get('lens_material_label','main_lens')
    if len(regions)!=len(parameters['regions']) or any(row[0]!=spec['label'] or row[1]!=spec['faces'] for row,spec in zip(regions,parameters['regions'])):
        raise ValueError('Native replay region set differs')
    if bounds:
        optical_scene.validate_conditioned_bounds(bounds,parameters)
    elif scale!=1:
        raise ValueError('Scaled diagnostic requires native geometry bounds')
    if len(records)!=len(parameters['start_rays'])*parameters['replay_attempts']:
        raise ValueError('Native replay path count differs')
    result=[]
    for ordinal,record in enumerate(records):
        index,attempt,rows=record
        if scale!=1:
            rows=[[[v/scale for v in p],d,length/scale if length<1e30 else length,intensity,n] for p,d,length,intensity,n in rows]
        spec=parameters['start_rays'][ordinal//parameters['replay_attempts']]
        if index!=spec['segment_index_zero_based'] or attempt!=ordinal%parameters['replay_attempts'] or not 1<=len(rows)<=256:
            raise ValueError('Native replay checkpoint or attempt differs')
        previous=spec['index']
        initial_glass=int(abs(previous-1)>1e-6)
        entries=exits=0
        for j,row in enumerate(rows):
            p,v,length,intensity,n=row
            if len(p)!=3 or len(v)!=3 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in [*p,*v,length,intensity,n]) or intensity<0 or length<0:
                raise ValueError('Invalid native replay path record')
            if abs(n-1)>1e-6 and abs(n-parameters['materials'][label]['index'])>1e-6:
                raise ValueError('Unexpected selected-lens medium')
            if j==0 and (max(abs(a-b) for a,b in zip(p,spec['position_mm']))>1e-4 or max(abs(a-b) for a,b in zip(v,spec['direction']))>1e-5 or abs(n-previous)>1e-6):
                raise ValueError('Native replay input differs')
            if abs(previous-1)<1e-6 and abs(n-1)>1e-6:
                entries+=1
            if abs(previous-1)>1e-6 and abs(n-1)<1e-6:
                exits+=1
            previous=n
        escaped=rows[-1][2]>1e30
        paired=initial_glass+entries==exits and abs(previous-1)<1e-6
        item={'segment_index_zero_based':index,'attempt':attempt,'path':rows,'segment_count':len(rows),
            'direction_variant':spec.get('direction_variant',0),
            'escaped':escaped,'initial_lens_medium':bool(initial_glass),'lens_entries':entries,'lens_exits':exits,
            'last_index':previous,'escaping_media_consistent':paired if escaped else None}
        if not parameters.get('scene_schema_version') and label=='main_lens':
            item.update(initial_glass=bool(initial_glass),glass_entries=entries,glass_exits=exits)
        elif not parameters.get('scene_schema_version'):
            item.update(initial_PC=bool(initial_glass),PC_entries=entries,PC_exits=exits)
        result.append(item)
    return {'backend':'native LucidShape RayPathFinder','scene_scope':parameters.get('scene_scope','main_lens_only'),
        'source_geometry_modified':False,'studio_modified':False,'diagnostic_only':True,
        'failure_sha256':parameters['failure_sha256'],'boundary_geometry_sha256':{r['label']:r['sha256'] for r in parameters['regions']},
        'main_lens_sha256':parameters['regions'][0]['sha256'] if label=='main_lens' else None,
        'coordinate_scale':scale,'native_geometry_bounds':bounds,'returned_path_length_unit':'original model mm',
        'main_lens_boundary_mode':parameters['main_lens_boundary_mode'],'start_rays':parameters['start_rays'],
        'paths':result,'inconsistent_escaping_paths':sum(r['escaped'] and not r['escaping_media_consistent'] for r in result),
        'limitations':['New stochastic traces from identical checkpoint input; original Fresnel choices are not replayed.',
            'Other lamp parts are excluded to isolate the selected boundary; this is not an engineering photometric simulation.',
            'Uniform coordinate scaling is a numerical diagnostic, not an accepted full-lamp correction.',
            'Declared input medium requires independent geometry verification; no per-traversal absorption applied.']}
