"""Validated sampling of the approved Lucid binary RAY and native terminal histories."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import random
import struct
from .inputs import MAX_BYTES
from . import optical_scene

HEADER=struct.Struct('<5fIf')
RAY=struct.Struct('<7f')

def write_ray_file(path:Path,rows):
    """Use the verified Lucid binary layout, with absolute lumen weights."""
    if not rows:
        raise ValueError('No escaped rays available for this beam')
    flux=math.fsum(row[6] for row in rows)
    if not math.isfinite(flux) or flux<=0:
        raise ValueError('Invalid outgoing flux')
    with path.open('wb') as stream:
        stream.write(HEADER.pack(0,0,0,0,flux,len(rows),-1))
        for row in rows:
            if len(row)!=7 or any(not math.isfinite(v) for v in row) or row[6]<0:
                raise ValueError('Invalid outgoing ray')
            stream.write(RAY.pack(*row))
    return flux

def stage_samples(directory:Path,parameters):
    data=Path(parameters['rayfile_path']).read_bytes()
    if hashlib.sha256(data).hexdigest()!=parameters.get('rayfile_sha256',hashlib.sha256(data).hexdigest()):
        raise ValueError('Approved ray file changed before sampling')
    a,b,c,d,flux,total,flag=HEADER.unpack_from(data)
    if (a,b,c,d,flag)!=(0,0,0,0,-1) or total!=parameters['ray_count'] or len(data)!=HEADER.size+total*RAY.size:
        raise ValueError('Unsupported or inconsistent Lucid binary ray-file layout')
    weights=[]
    for row in RAY.iter_unpack(memoryview(data)[HEADER.size:]):
        w=row[6]
        if not math.isfinite(w) or w<0:
            raise ValueError('Invalid original ray weight')
        weights.append(w)
    total_weight=math.fsum(weights)
    del weights
    if not math.isfinite(flux) or flux<=0 or total_weight<=0 or abs(total_weight-flux)>max(1e-4,flux*1e-6):
        raise ValueError('Native header flux and full payload weight differ')
    if 'flux_per_led_lm' in parameters and abs(flux-parameters['flux_per_led_lm'])>max(1e-4,flux*2e-6):
        raise ValueError('Declared source flux differs from the inspected RAY header')
    n=parameters['rays_per_led']
    coordinate_scale=parameters.get('coordinate_scale',1)
    if isinstance(coordinate_scale,bool) or not isinstance(coordinate_scale,(int,float)) or not math.isfinite(coordinate_scale) or not 1<=coordinate_scale<=1000:
        raise ValueError('Invalid diagnostic coordinate scale')
    scale=flux/total_weight*total/n
    out=directory/'sampled-rays.lue'
    digest=hashlib.sha256()
    with out.open('w',encoding='ascii',newline='\n') as stream:
        stream.write('#pragma NOPP\n@1=ObjectVector("sampled ray-file sources",(\n')
        for led,source in enumerate(optical_scene.sources(parameters)):
            pos=source['position_mm']
            if led:
                stream.write(',\n')
            stream.write(f'ObjectVector("source_{source["native_id"]}",(\n')
            rng=random.Random(parameters['sampling_seed']+led*1000003)
            indices=rng.sample(range(total),n)
            for k,index in enumerate(indices):
                digest.update(struct.pack('<II',led,index))
                x,y,z,dx,dy,dz,w=RAY.unpack_from(data,HEADER.size+index*RAY.size)
                if any(not math.isfinite(v) for v in (x,y,z,dx,dy,dz,w)) or abs(dx*dx+dy*dy+dz*dz-1)>1e-5:
                    raise ValueError('Invalid sampled source ray')
                local=optical_scene.transform(source['frame'],[x,y,z])
                p=[(pos[j]+local[j])*coordinate_scale for j in range(3)]
                v=optical_scene.transform(source['frame'],[dx,dy,dz])
                fmt=lambda xs: '('+','.join(format(t,'.17g') for t in xs)+')'
                if k:
                    stream.write(',\n')
                stream.write(f'LRay("sample",{fmt(p)},{fmt(v)},1000000,{w*scale:.17g},AirMedium("air"))')
            stream.write('))')
        stream.write('\n));\n')
    input_bytes=sum(f.stat().st_size for f in directory.iterdir() if f.suffix.lower() in ('.stp','.stl','.ray','.lue'))
    if input_bytes>MAX_BYTES:
        raise ValueError('Optical geometry, source and ray samples exceed 256 MiB')
    info={'source_header_flux_per_led_lm':flux,'source_payload_total_weight':total_weight,
        'coordinate_scale':coordinate_scale,'native_coordinate_units_per_model_mm':coordinate_scale,
        'sampling_scale':scale,'rays_per_led':n,'sampling_seed':parameters['sampling_seed'],
        'sample_index_sha256':digest.hexdigest(),'sampling':'independent uniform sampling without replacement for each LED; original source flux retained',
        'native_input_file':str(out),'native_input_sha256':hashlib.sha256(out.read_bytes()).hexdigest()}
    (directory/'sampling.json').write_text(json.dumps(info,indent=2)+'\n',encoding='utf-8')
    return {'source':parameters['rayfile_path'],'staged_name':out.name,'source_bytes':len(data),
        'source_sha256':hashlib.sha256(data).hexdigest(),'derived_sampling':info}

def read_terminals(directory:Path,parameters):
    coordinate_scale=parameters.get('coordinate_scale',1)
    info=json.loads((directory/'sampling.json').read_text(encoding='utf-8'))
    if coordinate_scale!=info.get('coordinate_scale',1):
        raise ValueError('Terminal coordinate scale differs from staged source')
    p=directory/'terminal-rays.json'
    if p.stat().st_size>64*1024*1024:
        raise ValueError('Terminal history file exceeds 64 MiB')
    raw=json.loads(p.read_text(encoding='utf-8'))
    rows=raw.get('List',{}).get('p0')
    frames=optical_scene.sources(parameters)
    media=optical_scene.media(parameters)
    modes=optical_scene.modes(parameters)
    by_id={s['native_id']:s['name'] for s in frames}
    if not isinstance(rows,list) or len(rows)>len(frames)*parameters['rays_per_led']:
        raise ValueError('Invalid terminal ray list')
    beams={name:[] for name in modes}
    traversals={m['id']:0 for m in media}
    for row in rows:
        if not isinstance(row,list) or len(row)!=9+len(media) or any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in row):
            raise ValueError('Invalid terminal ray record')
        led,*rest=row
        x,y,z,dx,dy,dz,intensity=rest[:7]
        visits=rest[7:-1];weighted=rest[-1]
        if int(led)!=led or led not in by_id or intensity<0 or weighted<0 or any(int(v)!=v or not 0<=v<=256 for v in visits):
            raise ValueError('Invalid terminal ray history counts')
        if abs(dx*dx+dy*dy+dz*dz-1)>1e-5:
            raise ValueError('Non-unit terminal ray direction')
        expected=intensity*math.prod((1-m['loss'])**n for m,n in zip(media,visits))
        if abs(weighted-expected)>max(1e-12,expected*1e-9):
            raise ValueError('Native per-traversal loss differs from independent weighting')
        ray=[x/coordinate_scale,y/coordinate_scale,z/coordinate_scale,dx,dy,dz,weighted]
        for name,members in modes.items():
            if by_id[led] in members:
                beams[name].append(ray)
        for m,n in zip(media,visits):
            traversals[m['id']]+=n
    outputs={}
    for beam,rays in beams.items():
        output=directory/(beam+'-weighted.ray')
        flux=write_ray_file(output,rays)
        source_flux=info['source_header_flux_per_led_lm']*len(modes[beam])
        if flux>source_flux*1.001:
            raise ValueError('Escaped ray flux exceeds source budget')
        outputs[beam]={'weighted_rayfile':str(output),'escaped_ray_count':len(rays),
            'escaped_flux_estimate_lm':flux,'input_source_flux_lm':source_flux,
            'escaped_fraction_estimate':flux/source_flux,'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}
    return outputs,traversals,info,len(rows)


def render(template, parameters):
    media=optical_scene.validate_history_media(parameters)
    updates=['if(abs(previous-1)>0.000001 && abs(n-1)>0.000001 && abs(previous-n)>0.000001) { cout << "LUCIDSHAPE_MCP_FAILED: direct medium transition unsupported" << endl; return; }']
    known=' || '.join(['abs(n-1)<0.000001']+[f'abs(n-{m["index"]:.17g})<0.000001' for m in media])
    updates.append(f'if(!({known})) {{ cout << "LUCIDSHAPE_MCP_FAILED: unknown path medium" << endl; return; }}')
    for i,m in enumerate(media):
        updates += [f'if(abs(previous-1)<0.000001 && abs(n-{m["index"]:.17g})<0.000001) entries{i}=entries{i}+1;',
                    f'if(abs(previous-{m["index"]:.17g})<0.000001 && abs(n-1)<0.000001) exits{i}=exits{i}+1;']
    replacements={
        'SOURCE_IDS':optical_scene.native_vector([s['native_id'] for s in optical_scene.sources(parameters)]),
        'SOURCE_COUNT':str(len(optical_scene.sources(parameters))),
        'RAYS_PER_LED':str(parameters['rays_per_led']),
        'COORDINATE_SCALE':format(parameters.get('coordinate_scale',1),'.17g'),
        'TOTAL_DECLARATIONS':'\n'.join(f'double count{i}=0;' for i in range(len(media))),
        'VISIT_DECLARATIONS':'\n'.join(f'double entries{i}=0,exits{i}=0;' for i in range(len(media))),
        'VISIT_UPDATES':'\n'.join(updates),
        'UNPAIRED':' || '.join(['abs(previous-1)>0.000001']+[f'entries{i}!=exits{i}' for i in range(len(media))]),
        'APPLY_LOSSES':'\n'.join(f'for(double q{i}=0;q{i}<exits{i};q{i}=q{i}+1) weight=weight*{1-m["loss"]:.17g};' for i,m in enumerate(media)),
        'EXIT_FIELDS':''.join(f',exits{i}' for i in range(len(media))),
        'ACCUMULATE':'\n'.join(f'count{i}=count{i}+exits{i};' for i in range(len(media))),
        'COUNT_FIELDS':''.join(f',count{i}' for i in range(len(media)))}
    for key,value in replacements.items():
        template=template.replace('@@'+key+'@@',value)
    return template
