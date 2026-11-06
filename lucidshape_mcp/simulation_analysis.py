"""File-only pooling and preliminary photometry; never operates CAD/Studio."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import re
from .path_sampling import HEADER, RAY, write_ray_file
from .photometry import inspect_ies
from .compatibility import operation
from .optical_scene import identifier


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_rays(path):
    data=Path(path).read_bytes()
    if len(data)<HEADER.size:raise ValueError('Incomplete ray header')
    a,b,c,d,flux,n,flag=HEADER.unpack_from(data)
    if (a,b,c,d,flag)!=(0,0,0,0,-1) or not 0<n<=2000000 or len(data)!=HEADER.size+n*RAY.size:
        raise ValueError('Invalid weighted ray layout')
    rows=list(RAY.iter_unpack(memoryview(data)[HEADER.size:]))
    for row in rows:
        if any(not math.isfinite(v) for v in row) or row[6]<0 or abs(sum(v*v for v in row[3:6])-1)>1e-5:
            raise ValueError('Invalid weighted ray payload')
    actual=math.fsum(row[6] for row in rows)
    if not math.isfinite(flux) or flux<=0 or abs(actual-flux)>max(1e-5,flux*2e-6):
        raise ValueError('Weighted ray header flux differs from payload')
    return rows,actual


def pool_traces(report_paths,out):
    """Pool complete runs, weighting each estimate by its per-LED sample count."""
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    reports=[json.loads(Path(p).read_text(encoding='utf-8')) for p in report_paths]
    ids=set();samples=set();seeds=set();signature=None;total=0
    for r in reports:
        job=r['trace_job'];res=job['result'];params=job['parameters']
        if job['status']!='succeeded' or operation(job['template'])!='trace_optical_paths' or r.get('canonical_requirements_modified',True):
            raise ValueError('Only complete unchanged-configuration trace reports can be pooled')
        if res['history_weights_independently_verified'] is not True or res['weighted_rayfile_position_unit']!='original model mm':
            raise ValueError('Trace history or output units not verified')
        sample=res['sampling']['sample_index_sha256']
        seed=res['sampling']['sampling_seed']
        if job['job_id'] in ids or sample in samples or seed in seeds:raise ValueError('Duplicate run, seed or source sample set')
        ids.add(job['job_id']);samples.add(sample);seeds.add(seed)
        current=(r['requirements_sha256'],r.get('effective_requirements_sha256'),
            tuple((g['label'],g['sha256']) for g in params['regions']),res['coordinate_scale'])
        if signature is not None and signature!=current:raise ValueError('Trace physics or geometry mismatch')
        signature=current
        n=res['rays_per_led']
        source_count=res.get('source_count',12)
        if res['total_input_rays']!=source_count*n:raise ValueError('Input count mismatch')
        signature_extra=(source_count,params.get('sources'),params.get('modes'),params.get('materials'),params.get('sensor'))
        if total and signature_extra!=previous_extra:raise ValueError('Source, mode or material configuration differs')
        previous_extra=signature_extra
        total+=n
    if not reports:raise ValueError('No trace reports')
    names=list(reports[0]['trace_job']['result']['beams'])
    for name in names:
        identifier(name)
    if any(set(r['trace_job']['result']['beams'])!=set(names) for r in reports):
        raise ValueError('Trace output modes differ')
    beams={}
    for beam in names:
        merged=[];runs=[]
        for p,r in zip(report_paths,reports):
            res=r['trace_job']['result'];info=res['beams'][beam]
            path=Path(info['weighted_rayfile'])
            if sha(path)!=info['sha256']:raise ValueError('Weighted ray artifact changed')
            rows,flux=read_rays(path)
            if len(rows)!=info['escaped_ray_count'] or abs(flux-info['escaped_flux_estimate_lm'])>max(1e-5,flux*2e-6):
                raise ValueError('Escaped ray report differs from artifact')
            factor=res['rays_per_led']/total
            merged.extend((*row[:6],row[6]*factor) for row in rows)
            runs.append({'trace_report':str(Path(p).resolve()),'rays_per_led':res['rays_per_led'],
                'sampling_seed':res['sampling']['sampling_seed'],'escaped_flux_lm':flux,'pooling_weight':factor,
                'weighted_rayfile':str(path),'sha256':info['sha256']})
        if len(merged)>2000000:raise ValueError('Pooled ray count exceeds native binning limit')
        target=out/(beam+'-pooled.ray')
        write_ray_file(target,merged)
        reread,flux=read_rays(target)
        expected=math.fsum(r['escaped_flux_lm']*r['pooling_weight'] for r in runs)
        if abs(flux-expected)>max(1e-5,expected*2e-6):raise ValueError('Pooling changed flux normalization')
        values=[r['escaped_flux_lm'] for r in runs]
        beams[beam]={'weighted_rayfile':str(target),'sha256':sha(target),'escaped_ray_count':len(reread),
            'escaped_flux_lm':flux,'run_flux_spread_over_mean':(max(values)-min(values))/(sum(values)/len(values)),
            'runs':runs}
    return {'total_input_rays':source_count*total,'source_count':source_count,'rays_per_source':total,'rays_per_led':total,'run_count':len(reports),'beams':beams,
        'pooling':'each absolute-lumen ray weight multiplied by run sample count / pooled sample count; source power is not multiplied by run count',
        'requirements_sha256':signature[0],'coordinate_scale':signature[-1],
        'convergence_verified':False,'regulatory_compliance_evaluated':False}


def load_type_b(path):
    meta=inspect_ies(Path(path))
    if meta['photometric_type']!='B':raise ValueError('Road projection requires verified Type-B data')
    text=Path(path).read_text(encoding='utf-8-sig')
    values=[float(v) for v in re.split(r'[\s,]+',text.split('TILT=NONE',1)[1].strip())]
    nv,nh=int(values[3]),int(values[4]);mult=values[2]
    v=values[13:13+nv];h=values[13+nv:13+nv+nh]
    cd=[x*mult for x in values[13+nv+nh:]]
    return {'h':h,'v':v,'cd':cd,'meta':meta}


def sample_cd(field,h,v):
    """Bilinear interpolation on measured bin centers; outside coverage is unknown."""
    hs,vs=field['h'],field['v']
    if not hs[0]<=h<=hs[-1] or not vs[0]<=v<=vs[-1]:return None
    import bisect
    i=min(len(hs)-2,max(0,bisect.bisect_right(hs,h)-1))
    j=min(len(vs)-2,max(0,bisect.bisect_right(vs,v)-1))
    a=(h-hs[i])/(hs[i+1]-hs[i]);b=(v-vs[j])/(vs[j+1]-vs[j]);nv=len(vs)
    return ((1-a)*((1-b)*field['cd'][i*nv+j]+b*field['cd'][i*nv+j+1])+
        a*((1-b)*field['cd'][(i+1)*nv+j]+b*field['cd'][(i+1)*nv+j+1]))


def road_lux(field,x,y,height=.798,offsets=(-.756,.756),pitch_deg=-math.degrees(math.atan(.01))):
    """Far-field point lamps on a flat horizontal road; identical L/R lamp data."""
    if x<=0 or height<=0:raise ValueError('Road distance and lamp height must be positive')
    p=math.radians(pitch_deg);c,s=math.cos(p),math.sin(p);total=0
    for offset in offsets:
        z=-height;dy=y-offset;distance=math.sqrt(x*x+dy*dy+z*z)
        # Inverse of pitch that maps local forward into (cos(p),0,sin(p)).
        local_x=c*x+s*z;local_z=-s*x+c*z
        h=-math.degrees(math.asin(dy/distance));v=math.degrees(math.atan2(local_z,local_x))
        intensity=sample_cd(field,h,v)
        if intensity is None:return None
        total+=intensity*height/distance**3
    return total


def angular_histogram(rows,resolution=1):
    nh,nv=round(120/resolution),round(30/resolution)
    flux=[0.]*(nh*nv);squares=[0.]*(nh*nv);counts=[0]*(nh*nv)
    for row in rows:
        h=-math.degrees(math.asin(max(-1,min(1,row[4]))));v=math.degrees(math.atan2(row[5],row[3]))
        if -60<=h<60 and -20<=v<10:
            i=min(nh-1,math.floor((h+60)/resolution));j=min(nv-1,math.floor((v+20)/resolution));k=i*nv+j
            w=row[6];flux[k]+=w;squares[k]+=w*w;counts[k]+=1
    omega=[]
    for i in range(nh):
        h0=math.radians(-60+i*resolution);h1=math.radians(-60+(i+1)*resolution)
        omega.extend([math.radians(resolution)*(math.sin(h1)-math.sin(h0))]*nv)
    return {'flux':flux,'counts':counts,'sum_weight_squared':squares,'solid_angle_sr':omega,
        'cd':[f/o for f,o in zip(flux,omega)],'grid':[nh,nv],'resolution_deg':resolution}
