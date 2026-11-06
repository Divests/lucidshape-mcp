"""Stream bounded ranges of escaped rays into a native sensor, never lamp geometry."""
import hashlib,json,math,struct
from pathlib import Path
from .inputs import input_file
from .photometry import inspect_ies
from .optical_scene import validate_sensor, native_vector
try:
    import numpy as np
except ImportError:
    np=None
HEADER=struct.Struct('<5fIf')
BOUNDS=[-60.,60.,-20.6,19.4]
GRID=[1200,400]

def prepare(path,start,count,scale,pitch,sensor_config=None):
    if np is None:raise ValueError('Ray replay requires the optional analysis dependency: numpy')
    path=input_file(path,{'.ray'})
    for val,lo,hi,name in ((start,0,500000000,'source_start'),(count,1,4000000,'ray_count')):
        if isinstance(val,bool) or not isinstance(val,int) or not lo<=val<=hi:raise ValueError('Invalid '+name)
    for val,lo,hi,name in ((scale,1e-6,10,'flux_scale'),(pitch,-5,5,'pitch_deg')):
        if isinstance(val,bool) or not isinstance(val,(float,int)) or not math.isfinite(val) or not lo<=val<=hi:raise ValueError('Invalid '+name)
    stat=path.stat()
    with path.open('rb') as stream:header=HEADER.unpack(stream.read(28))
    if header[:4]!=(0,0,0,0) or header[6]!=-1 or not math.isfinite(header[4]) or header[4]<=0 or stat.st_size!=28+28*header[5]:
        raise ValueError('Require native weighted nonspectral 7-float RAY format')
    if start+count>header[5]:raise ValueError('Requested range extends past source file')
    sensor=validate_sensor(sensor_config) if sensor_config is not None else {
        'bounds_deg':BOUNDS,'grid_uv':GRID,'frame':{'x':[1,0,0],'y':[0,1,0],'z':[0,0,1]}}
    return {'rayfile_path':str(path),'source_bytes':stat.st_size,'source_mtime_ns':stat.st_mtime_ns,'source_record_count':header[5],
        'source_start':start,'source_stop_exclusive':start+count,'ray_count':count,'flux_scale':float(scale),'pitch_deg':float(pitch),
        'source_header_flux_lm':header[4],'grid_uv':sensor['grid_uv'],'bounds_deg':sensor['bounds_deg'],'sensor':sensor,'trace_mode':20,
        'geometry_retraced':False,'input_frame':'explicit sensor x/y/z basis in input-file coordinates; positive H right, positive V up'}

def stage(directory,p):
    source=Path(p['rayfile_path']);stat=source.stat()
    if (stat.st_size,stat.st_mtime_ns)!=(p['source_bytes'],p['source_mtime_ns']):raise ValueError('Source changed before staging')
    with source.open('rb') as stream:
        stream.seek(28+28*p['source_start']);raw=stream.read(28*p['ray_count'])
    if len(raw)!=28*p['ray_count']:raise ValueError('Incomplete source range')
    rawhash=hashlib.sha256(raw).hexdigest();a=np.frombuffer(raw,dtype='<f4').reshape(-1,7).astype(np.float64)
    if not np.isfinite(a).all() or (a[:,6]<0).any():raise ValueError('Invalid ray payload')
    norm=np.linalg.norm(a[:,3:6],axis=1)
    if np.max(abs(norm-1))>1e-4:raise ValueError('Direction is not a unit vector')
    a[:,3:6]/=norm[:,None]
    before=float(a[:,6].sum());angle=math.radians(p['pitch_deg']);c,s=math.cos(angle),math.sin(angle)
    basis=np.array([p['sensor']['frame'][axis] for axis in 'xyz'])
    for offset in (0,3):
        a[:,offset:offset+3]=a[:,offset:offset+3]@basis.T
        y=a[:,offset+1].copy();z=a[:,offset+2].copy()
        a[:,offset+1]=c*y+s*z;a[:,offset+2]=-s*y+c*z
    a[:,6]*=p['flux_scale'];payload=a.astype('<f4');actual=float(payload[:,6].sum(dtype=np.float64))
    if not math.isclose(actual,before*p['flux_scale'],rel_tol=2e-7):raise ValueError('Flux scaling failed')
    with (directory/'input.ray').open('wb') as stream:
        stream.write(HEADER.pack(0,0,0,0,actual,len(payload),-1));stream.write(payload.tobytes())
    d=payload[:,3:6].astype(np.float64);d/=np.linalg.norm(d,axis=1)[:,None]
    h=np.rad2deg(-np.arctan2(d[:,0],d[:,2]));v=np.rad2deg(np.arcsin(np.clip(d[:,1],-1,1)))
    bounds=p['bounds_deg'];grid=p['grid_uv']
    du=(bounds[1]-bounds[0])/grid[0];dv=(bounds[3]-bounds[2])/grid[1]
    inside=(h>=bounds[0])&(h<bounds[1])&(v>=bounds[2])&(v<bounds[3])
    hi=np.floor((h[inside]-bounds[0])/du).astype(int);vi=np.floor((v[inside]-bounds[2])/dv).astype(int)
    histogram=np.bincount(hi*grid[1]+vi,weights=payload[inside,6],minlength=grid[0]*grid[1]).reshape(grid)
    np.save(directory/'independent-flux.npy',histogram)
    coverage={**p,'source_range_sha256':rawhash,'staged_sha256':hashlib.sha256((directory/'input.ray').read_bytes()).hexdigest(),
        'input_flux_before_scale_lm':before,'staged_flux_lm':actual,'expected_sensor_flux_lm':float(histogram.sum()),
        'records_inside_sensor':int(inside.sum()),'records_outside_sensor':int((~inside).sum()),'zero_weight_records':int((payload[:,6]==0).sum()),
        'source_direction_max_norm_error':float(np.max(abs(norm-1)))}
    (directory/'coverage.json').write_text(json.dumps(coverage,indent=2),encoding='utf-8')
    return [{'source':str(source),'staged_name':'input.ray','source_range_sha256':rawhash,'source_start':p['source_start'],'source_stop_exclusive':p['source_stop_exclusive']}]

def result(directory,p,values):
    n,angle,grid,bounds,stats,mode=values
    if n!=p['ray_count'] or angle!=1 or grid!=p['grid_uv'] or len(bounds)!=4 or any(abs(a-b)>1e-6 for a,b in zip(bounds,p['bounds_deg'])) or mode!=20:
        raise ValueError('Native replay count, mode or sensor geometry differs')
    if len(stats)!=3 or not all(math.isfinite(x) for x in stats) or not 0<=stats[0]<=stats[1] or stats[2]<0:raise ValueError('Invalid native photometry')
    coverage=json.loads((directory/'coverage.json').read_text());expected=coverage['expected_sensor_flux_lm']
    if abs(stats[2]-expected)>max(.00002,expected*.0002):raise ValueError('Native replay changed integrated ray flux')
    iespath=directory/'photometry.ies';meta=inspect_ies(iespath)
    if meta['photometric_type']!='A' or meta['intensity_count']!=grid[0]*grid[1]:raise ValueError('Exported IES grid differs')
    a=np.fromstring(iespath.read_text(encoding='ascii').split('TILT=NONE')[1],sep=' ')
    nu,nv=grid;du=(bounds[1]-bounds[0])/nu;dv=(bounds[3]-bounds[2])/nv
    v=a[13:13+nv];h=a[13+nv:13+nv+nu];cd=a[13+nv+nu:].reshape(grid)*a[2]
    if not np.allclose(h,bounds[0]+(np.arange(nu)+.5)*du,atol=1e-8) or not np.allclose(v,bounds[2]+(np.arange(nv)+.5)*dv,atol=1e-8):raise ValueError('IES sample axes differ')
    omega=math.radians(du)*(np.sin(np.deg2rad(v+dv/2))-np.sin(np.deg2rad(v-dv/2)))
    reference=np.load(directory/'independent-flux.npy');l1=float(np.abs(cd*omega-reference).sum()/max(expected,1e-15))
    if l1>.002:raise ValueError(f'Native/independent binned flux disagree: relative L1={l1}')
    log=(directory/'engine.log').read_text(encoding='utf-8',errors='replace').lower()
    # No geometry means there is no NURBS intersection-mode banner. Verify the actual source/sensor loop.
    if f'start raytrace every recorded ray exactly once with {p["ray_count"]} rays' not in log.replace(',','') or 'start 1 threads for source' not in log or 'graphics card' in log:
        raise ValueError('Native source/sensor replay not evidenced')
    return {'operation':'escaped_ray_sensor_replay','geometry_retraced':False,'requested_trace_mode':20,'native_sensor_execution_verified':True,
        'ray_count':n,'coverage':coverage,'angle_type':'A','grid_uv':grid,'cell_edge_range_deg':bounds,
        'flux_in_sensor_domain_lm':stats[2],'maximum_cd':stats[1],'independent_relative_L1':l1,
        'ies_file':str(iespath),'ies_sha256':hashlib.sha256(iespath.read_bytes()).hexdigest(),
        'lid_file':str(directory/'photometry.lid'),'regulatory_compliance_evaluated':False},[]


def render(template,p):
    return template.replace('@@GRID_U@@',str(p['grid_uv'][0])).replace('@@GRID_V@@',str(p['grid_uv'][1])).replace('@@BOUNDS@@',native_vector(p['bounds_deg']))
