"""Native UVView PNG export via private Studio; no screen capture or clipboard."""
from __future__ import annotations
import hashlib,json,math,struct,uuid,zlib
from pathlib import Path
from .inputs import input_file,snapshot
from .private_studio import run_private_studio,close_private_studio
from .results import sequence,number

STUDIO_SHA256='60c8339e075346ffd224816810fdb50ecb2c5097d3ce891b5e06df13548312ab'
TEMPLATE=Path(__file__).with_name('templates')/'uv_view_export.lus'

def _range(value,n,name):
    if value is None:return None
    if not isinstance(value,list) or len(value)!=n:raise ValueError(name+' has invalid length')
    if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in value):raise ValueError(name+' must contain finite numbers')
    if any(value[i]>=value[i+1] for i in range(0,n,2)):raise ValueError(name+' must increase')
    return [float(v) for v in value]

def validate(*,lid_path,width_px=1600,height_px=900,quantity='candela',screen_distance_m=None,view_bounds=None,value_range=None,contours=None,timeout_seconds=120):
    source=input_file(lid_path,{'.ies','.lid'})
    for val,lo,hi,name in [(width_px,400,4000,'width_px'),(height_px,250,2400,'height_px'),(timeout_seconds,1,600,'timeout_seconds')]:
        if isinstance(val,bool) or not isinstance(val,int) or not lo<=val<=hi:raise ValueError(name+' outside supported integer range')
    if width_px*height_px>8000000:raise ValueError('At most 8 million pixels')
    if quantity not in ('candela','native') or screen_distance_m is not None:raise ValueError('Supported quantities are candela/native; screen-distance conversion is not implemented')
    view_bounds=_range(view_bounds,4,'view_bounds');value_range=_range(value_range,2,'value_range')
    if view_bounds and any(abs(v)>100000 for v in view_bounds):raise ValueError('view_bounds outside -100000..100000 native units')
    if value_range and (value_range[0]<0 or value_range[1]>1e12):raise ValueError('value_range outside 0..1e12 native units')
    if contours is not None:
        if not isinstance(contours,list) or not 1<=len(contours)<=100:raise ValueError('contours requires 1..100 values')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<v<=1e12 for v in contours):raise ValueError('contours must be positive finite native values')
        if any(a>=b for a,b in zip(contours,contours[1:])):raise ValueError('contours must be strictly increasing')
        contours=[float(v) for v in contours]
    return dict(lid_path=str(source),width_px=width_px,height_px=height_px,quantity=quantity,view_bounds=view_bounds,value_range=value_range,contours=contours,timeout_seconds=timeout_seconds)

def _native(value):
    if isinstance(value,list):return '('+','.join(_native(v) for v in value)+')'
    if isinstance(value,str):return json.dumps(value,ensure_ascii=True)
    return format(value,'.17g')

def make_script(directory,parameters,nonce):
    p=parameters;source=Path(p['lid_path']);staged=directory/('input'+source.suffix.lower())
    mods=[]
    if p['view_bounds'] is not None:mods.append('props[2]='+_native(p['view_bounds'])+';')
    if p['value_range'] is not None:mods.append('props[0]='+_native(p['value_range'])+';props[15]=1;')
    if p['contours'] is not None:
        text=' '.join(format(v,'.15g') for v in p['contours'])
        mods.append('props[6]='+_native(text)+';props[13]=1;props[36]=('+_native(text)+');props[35]=0;')
    values={'SOURCE':_native(staged.as_posix()),'IS_IES':'1' if source.suffix.lower()=='.ies' else '0','REQUIRE_CD':'1' if p['quantity']=='candela' else '0','PNG':_native((directory/'photometry-native.png').as_posix()),'RECEIPT':_native((directory/'native-receipt.json').as_posix()),'PROGRESS':_native((directory/'native-progress.txt').as_posix()),'NONCE':_native(nonce),'WIDTH':str(p['width_px']),'HEIGHT':str(p['height_px']),'MODS':'\n'.join(mods)}
    text=TEMPLATE.read_text(encoding='utf-8')
    for key,value in values.items():text=text.replace('@@'+key+'@@',value)
    if '@@' in text:raise ValueError('Unexpanded native template')
    return text

def verify_png(path,width,height):
    data=path.read_bytes()
    if len(data)>64*1024*1024 or data[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('Invalid/oversized native PNG')
    pos=8;parts=[];header=None;ended=False
    while pos<len(data):
        if pos+12>len(data):raise ValueError('Truncated PNG chunk')
        length=struct.unpack('>I',data[pos:pos+4])[0];end=pos+12+length
        if end>len(data):raise ValueError('Truncated PNG payload')
        kind=data[pos+4:pos+8];payload=data[pos+8:pos+8+length]
        if zlib.crc32(kind+payload)&0xffffffff!=struct.unpack('>I',data[end-4:end])[0]:raise ValueError('PNG CRC mismatch')
        if kind==b'IHDR':
            if header is not None or pos!=8 or length!=13:raise ValueError('Invalid PNG header')
            header=struct.unpack('>IIBBBBB',payload)
        elif kind==b'IDAT':parts.append(payload)
        elif kind==b'IEND':
            if length or end!=len(data):raise ValueError('Invalid PNG ending')
            ended=True;break
        pos=end
    if not ended or not parts or header is None or tuple(header[:2])!=(width,height):raise ValueError('PNG dimensions/content mismatch')
    w,h,depth,color,compression,filter_method,interlace=header
    if depth!=8 or color not in (2,6) or compression or filter_method or interlace:raise ValueError('Unverified PNG encoding profile')
    row=w*(3 if color==2 else 4)+1;expected=h*row
    dec=zlib.decompressobj();raw=dec.decompress(b''.join(parts),expected+1)
    if len(raw)!=expected or not dec.eof or dec.unused_data or dec.unconsumed_tail:raise ValueError('Incomplete PNG pixel stream')
    if any(raw[y*row]>4 for y in range(h)):raise ValueError('Invalid PNG scanline filter')
    return {'width_px':w,'height_px':h,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'native_png_crc_and_pixel_stream_verified':True}

def finalize(directory,parameters,nonce,manifest,script_sha256):
    if hashlib.sha256((directory/'native-job.lus').read_bytes()).hexdigest()!=script_sha256:raise ValueError('Executed native script changed')
    raw=json.loads((directory/'native-receipt.json').read_text(encoding='utf-8-sig'))
    if isinstance(raw,dict) and set(raw)=={'List'}:
        values=raw['List']
        if not isinstance(values,dict) or set(values)!={f'p{i}' for i in range(11)}:raise ValueError('Invalid native receipt object')
        raw=[values[f'p{i}'] for i in range(11)]
    fields=sequence(raw,11)
    token,version,status,kind,size,peak,flux,properties,is_candela,angle_type,done=fields
    if token!=nonce or version!=1 or status!='succeeded' or done!='completed':raise ValueError('Native export receipt mismatch or failure')
    if kind!='UVView':raise ValueError('Unexpected native view class')
    angle_type=number(angle_type)
    if is_candela and angle_type not in (0,1):raise ValueError('Only native angle types 0/1 support candela U/V export')
    if isinstance(is_candela,bool) or is_candela not in (0,1) or (parameters['quantity']=='candela' and is_candela!=1):raise ValueError('Native input quantity differs')
    size=sequence(size,2)
    if [number(v) for v in size]!=[parameters['width_px'],parameters['height_px']]:raise ValueError('Native window size differs')
    props=sequence(properties)
    if len(props)<22 or any(props[k]!=v for k,v in {7:2,9:0,10:0,11:0,12:1,16:1,17:1,19:1,20:1}.items()):raise ValueError('Native view properties differ from verified export profile')
    if parameters['view_bounds'] is not None and sequence(props[2],4)!=parameters['view_bounds']:raise ValueError(f"Native view range differs: requested {parameters['view_bounds']}, actual {props[2]}; use native pixel-edge bounds")
    if parameters['value_range'] is not None and sequence(props[0],2)!=parameters['value_range']:raise ValueError('Native color range differs')
    if parameters['contours'] is not None:
        parsed=[float(v) for v in str(props[6]).split()]
        if parsed!=parameters['contours'] or props[13]!=1:raise ValueError('Native contours differ')
    peak=[number(v) for v in sequence(peak,3)];flux=number(flux)
    if peak[0]<0 or flux<0:raise ValueError('Negative photometry')
    image=verify_png(directory/'photometry-native.png',parameters['width_px'],parameters['height_px'])
    for entry in manifest:
        source=Path(entry['source'])
        if hashlib.sha256(source.read_bytes()).hexdigest()!=entry['source_sha256']:raise ValueError('Source changed during export')
        if hashlib.sha256((directory/entry['staged_name']).read_bytes()).hexdigest()!=entry['staged_sha256']:raise ValueError('Input snapshot changed during export')
    return {'status':'succeeded','image':str(directory/'photometry-native.png'),'image_verification':image,'native_peak_value_u_v':peak,'native_domain_integral':flux,'native_angle_type':angle_type,'native_uv_semantics':'native photometric U/V degrees (angle type 0 or 1)' if is_candela else 'native planar/data coordinates; no angular conversion','angular_domain_flux_lm':flux if is_candela else None,'quantity':'candela' if is_candela else 'native','value_unit':'cd' if is_candela else 'from native data (unconverted)','native_frame_name':props[21] if len(props)>21 else None,'native_view_class':kind,'axes':True,'color_bar':True,'contours':True,'native_display':'logarithmic color with native axes, grid, contours, color bar and labels','source_files_unchanged':True,'input_snapshots_unchanged':True,'executed_script_sha256':script_sha256,'source_geometry_retraced':False,'regulatory_assessment':False,'native_properties':props}

async def export_view(root:Path,executable:Path,*,lid_path:str,width_px=1600,height_px=900,quantity='candela',screen_distance_m=None,view_bounds=None,value_range=None,contours=None,timeout_seconds=120,process_id=None,transport=None)->dict:
    if process_id is not None:raise ValueError('This verified exporter supports private owned Studio jobs only; omit process_id')
    parameters=validate(lid_path=lid_path,width_px=width_px,height_px=height_px,quantity=quantity,screen_distance_m=screen_distance_m,view_bounds=view_bounds,value_range=value_range,contours=contours,timeout_seconds=timeout_seconds)
    executable=Path(executable).resolve()
    if executable.name.lower()=='lush.exe':executable=executable.with_name('LucidShape.exe')
    if not executable.is_file() or hashlib.sha256(executable.read_bytes()).hexdigest()!=STUDIO_SHA256:raise ValueError('Installed Studio differs from the verified UVView export profile')
    directory=Path(root).resolve()/('uv-view-export-'+uuid.uuid4().hex);directory.mkdir(parents=True)
    source=Path(parameters['lid_path']);manifest=snapshot(source,directory,'input'+source.suffix.lower());nonce=uuid.uuid4().hex
    for entry in manifest:entry['staged_sha256']=hashlib.sha256((directory/entry['staged_name']).read_bytes()).hexdigest()
    script=make_script(directory,parameters,nonce)
    expected=directory/'expected-native.lus';expected.write_text(script,encoding='utf-8')
    script_sha256=hashlib.sha256(expected.read_bytes()).hexdigest()
    request={'parameters':parameters,'nonce':nonce,'engine_sha256':STUDIO_SHA256,'inputs':manifest,'script_sha256':script_sha256}
    (directory/'request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2),encoding='utf-8')
    result={'status':'unverified','record_directory':str(directory),'inputs':manifest,'computer_use_calls':0,'automatic_resubmission_allowed':False}
    try:
        launch=await run_private_studio(executable,directory,script,'native-receipt.json',timeout_seconds)
        result.update(launch)
        if launch['status']!='receipt_ready':result['status']='unverified'
        else:
            result.update(finalize(directory,parameters,nonce,manifest,script_sha256))
            result['private_process_cleanup']=await close_private_studio(launch)
            result['process_left_open']=not result['private_process_cleanup']['closed']
    except Exception as exc:
        result.update(status='unverified',error=f'{type(exc).__name__}: {exc}')
    result['backend']='Private LucidStudio native UVView.snapshotBitmap / WindowsBitmap.saveBMPFile';result['read_only']=False
    (directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
