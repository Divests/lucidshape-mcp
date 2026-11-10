"""Fixed native static-road calculation; no Python photometric projection."""
from __future__ import annotations
import csv
import hashlib
import json
import math
from pathlib import Path
import re
from .inputs import input_file, snapshot, decode_text
from .photometry import inspect_ies
from .results import number, count, sequence

VERIFIED_ENGINE = "1e64931b79d18a06f6a5eeb00663f23f33bd236428dc97568269ae53d2db9a48"
VERIFIED_ROAD_SCRIPT = "659e50cc21c5e9ae4890f1291a71127dcef4218de285057afc7d4380e8b4310f"
TEMPLATE = Path(__file__).with_name("templates") / "road_simulation.do"

def bounded(value, lo, hi, name):
    if isinstance(value, bool) or not isinstance(value, (float,int)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f"{name} must be finite in [{lo}, {hi}]")
    return float(value)

def vector(value, n, lo, hi, name):
    if not isinstance(value,list) or len(value)!=n:
        raise ValueError(f"{name} must contain {n} numbers")
    return [bounded(v,lo,hi,name) for v in value]

def dimensions(bounds, step):
    sizes=[]
    for low,high in zip(bounds[::2],bounds[1::2]):
        if high<=low:
            raise ValueError("Bounds must be increasing")
        n=(high-low)/step
        if not math.isclose(n,round(n),rel_tol=0,abs_tol=1e-7) or not 2<=n<=4000:
            raise ValueError("Each range must contain 2..4000 whole cells")
        sizes.append(round(n))
    if math.prod(sizes)>1000000:
        raise ValueError("At most one million cells per output")
    return sizes

def validate(arguments):
    p=dict(arguments)
    allowed={"lid_path","lamp_positions_m","driver_position_m","road_bounds_m","road_cell_size_m",
             "road_reflectance","view_bounds_deg","view_cell_size_deg","view_angles_deg"}
    if set(p)-allowed:
        raise ValueError("Unknown road-view parameters")
    p["lid_path"]=str(input_file(p["lid_path"],{".ies",".lid"}))
    positions=p["lamp_positions_m"]
    if not isinstance(positions,list) or not 1<=len(positions)<=2:
        raise ValueError("Provide one or two lamp positions")
    p["lamp_positions_m"]=[vector(v,3,-100,100,"lamp_positions_m") for v in positions]
    for pos in p["lamp_positions_m"]:
        if pos[2]<=0: raise ValueError("Lamp height must be positive")
    p["driver_position_m"]=vector(p["driver_position_m"],3,-100,100,"driver_position_m")
    if p["driver_position_m"][2]<=0: raise ValueError("Driver eye height must be positive")
    p["road_bounds_m"]=vector(p["road_bounds_m"],4,-1000,1000,"road_bounds_m")
    if p["road_bounds_m"][0]<=max(x[0] for x in p["lamp_positions_m"]):
        raise ValueError("Road must be in front of all lamp origins")
    p["road_cell_size_m"]=bounded(p.get("road_cell_size_m",.25),.01,10,"road_cell_size_m")
    p["road_reflectance"]=bounded(p.get("road_reflectance",.15),0,1,"road_reflectance")
    p["view_bounds_deg"]=vector(p.get("view_bounds_deg",[-20,20,-12,8]),4,-80,80,"view_bounds_deg")
    p["view_cell_size_deg"]=bounded(p.get("view_cell_size_deg",.05),.01,5,"view_cell_size_deg")
    p["view_angles_deg"]=vector(p.get("view_angles_deg",[0,0,0]),3,-60,60,"view_angles_deg")
    if p["view_angles_deg"][0]!=0 or p["view_angles_deg"][2]!=0:
        raise ValueError("This verified profile supports zero camera yaw and roll")
    dimensions(p["road_bounds_m"],p["road_cell_size_m"])
    dimensions(p["view_bounds_deg"],p["view_cell_size_deg"])
    return p

def native(value):
    if isinstance(value,(tuple,list)):
        return "("+",".join(native(v) for v in value)+")"
    return format(float(value),".17g")

def stage(directory, parameters, executable):
    p=parameters
    source=Path(p["lid_path"]); name="input"+source.suffix.lower()
    inputs=snapshot(source,directory,name)
    if source.suffix.lower()==".ies":
        meta=inspect_ies(directory/name)
        if meta["photometric_type"] not in ("A","B"):
            raise ValueError("Native road profile supports verified Type A/B photometry")
    library=Path(executable).resolve().parent.parent/"classes/AnalyseUVData/roadIllumDF.hdo"
    payload=library.read_bytes()
    digest=hashlib.sha256(payload).hexdigest()
    if digest!=VERIFIED_ROAD_SCRIPT:
        raise ValueError("Installed road library differs from the verified LS2.0 profile")
    (directory/"native-road.hdo").write_bytes(payload)
    inputs.append({"source":str(library),"staged_name":"native-road.hdo","source_bytes":len(payload),"source_sha256":digest})
    positions=p["lamp_positions_m"]
    p0=positions[0]; p1=positions[-1]
    args=[int(len(positions)==1),p0,p0,p1,p["road_bounds_m"],p["road_cell_size_m"],0,
          p["driver_position_m"],p["view_angles_deg"],p["view_bounds_deg"],
          [p["view_cell_size_deg"]]*2,0,0,1,0,[1,0,0],[1,0,0],[1,0,0],
          [0,1,0],[0,1,0],[0,1,0],0,10,[]]
    replacements={"INPUT":json.dumps(name),"IS_IES":"1" if source.suffix.lower()==".ies" else "0",
                  "PARAMETERS":native(args),"REFLECTANCE":native(p["road_reflectance"])}
    # The batch engine intentionally disables the C preprocessor. Inline only
    # the hash-pinned vendor computation functions, without GUI dialogs.
    vendor=decode_text(payload)[0]
    constants=dict(re.findall(r"(?m)^#define\s+(LIDTOOLS_\w+)\s+(\d+)\s*$",vendor))
    constants["MB_OK"]="0"
    body=vendor[vendor.index("object roadIllum_makeRoadTracer("):vendor.rfind("#endif")]
    body=re.sub(r"(?ms)^#ifdef DEBUG_\w+\s*\n.*?^#endif\s*$","",body)
    if re.search(r"(?m)^\s*#",body): raise ValueError("Unexpected vendor preprocessor directive")
    # LS2.0's bundled helper includes absent optional objects in ObjectVector;
    # its batch parser rejects those null entries. Preserve only present members.
    old='object allSensorsImage = ObjectVector("sensors", (point_light1, point_light2, road_sensor, back_plane_sensor, parameter[LIDTOOLS_DRIVER_VIEW_EXTRA_SHAPES] ) );'
    replacement='object members=(road_sensor); if(point_light1) members << point_light1; if(point_light2) members << point_light2; if(back_plane_sensor) members << back_plane_sensor; object extra=parameter[LIDTOOLS_DRIVER_VIEW_EXTRA_SHAPES]; if(extra) members << extra; object allSensorsImage=ObjectVector("sensors",members);'
    if body.count(old)!=1: raise ValueError("Unexpected native road object construction")
    body=body.replace(old,replacement)
    template=TEMPLATE.read_text(encoding="utf-8").replace('#include "native-road.hdo"',body)
    script=re.sub(r"@@([A-Z_]+)@@",lambda m:replacements[m[1]],template)
    script=re.sub(r"\b(?:LIDTOOLS_\w+|MB_OK)\b",lambda m:constants[m[0]],script)
    (directory/"job.do").write_text(script,encoding="utf-8")
    return inputs,("engine-result.json","road.lid","driver.lid","road.csv","driver.csv","road.png","driver.png","driver-color.png")

def unpack(value):
    if isinstance(value,dict) and set(value)=={"List"}:
        return unpack(value["List"])
    if isinstance(value,dict) and set(value)=={f"p{i}" for i in range(len(value))}:
        return [unpack(value[f"p{i}"]) for i in range(len(value))]
    return value

def finalize(directory, parameters):
    path=directory/"engine-result.json"
    if path.stat().st_size>4*1024*1024:raise ValueError("Oversized road receipt")
    raw=unpack(json.loads(decode_text(path.read_bytes())[0]))
    version,source,road,driver,rho=sequence(raw,5)
    if version!=1 or not math.isclose(number(rho),parameters["road_reflectance"],abs_tol=1e-12):
        raise ValueError("Road receipt version/reflectance mismatch")
    source_angle,source_nu,source_nv,source_flux=sequence(source,4)
    if count(source_angle) not in (0,1) or min(count(source_nu),count(source_nv))<2 or number(source_flux)<0:
        raise ValueError("Invalid native source photometry")
    outputs={}
    for name,row,unit in (("road",road,"lx"),("driver",driver,"cd/m2")):
        nu,nv,datatype,angle,flags,bounds,uaxis,vaxis,peak=sequence(row,9)
        nu,nv=count(nu),count(nv)
        want=dimensions(parameters["road_bounds_m"] if name=="road" else parameters["view_bounds_deg"],
                        parameters["road_cell_size_m"] if name=="road" else parameters["view_cell_size_deg"])
        if [nu,nv]!=want:raise ValueError(f"Unexpected native {name} resolution: {[nu,nv]} != {want}")
        axis_u=[number(v) for v in sequence(uaxis,nu)]
        axis_v=[number(v) for v in sequence(vaxis,nv)]
        if any(b<=a for axis in (axis_u,axis_v) for a,b in zip(axis,axis[1:])):
            raise ValueError("Native output axes must increase")
        expected_bounds=parameters["road_bounds_m"] if name=="road" else parameters["view_bounds_deg"]
        expected_step=parameters["road_cell_size_m"] if name=="road" else parameters["view_cell_size_deg"]
        bounds=[number(v) for v in sequence(bounds,4)]
        if any(not math.isclose(a,b,rel_tol=0,abs_tol=1e-8) for a,b in zip(bounds,expected_bounds)):
            raise ValueError("Native output bounds differ from the request")
        for axis,start in ((axis_u,bounds[0]),(axis_v,bounds[2])):
            if any(not math.isclose(value,start+(i+.5)*expected_step,rel_tol=0,abs_tol=1e-8) for i,value in enumerate(axis)):
                raise ValueError("Native output sampling differs from requested cell centers")
        if count(datatype)!=(3 if name=="road" else 5) or count(angle)!=3:
            raise ValueError("Unexpected native output data or coordinate type")
        peak=[number(v) for v in sequence(peak,3)]
        flags=sequence(flags,2)
        if flags != ([1,0] if name=="road" else [0,1]):
            raise ValueError(f"Native {name} unit flags are unexpected: {flags}")
        count_values=0;total=0.;minimum=math.inf;maximum=-math.inf;peak_sample=None
        with (directory/f"{name}.csv").open(encoding="utf-8-sig",newline="") as f:
            rows=csv.reader(f)
            if next(rows,None)!=["u_index","v_index","u","v","value"]:raise ValueError("Bad native CSV header")
            for index,row in enumerate(rows):
                if len(row)!=5 or index>=nu*nv:raise ValueError("Invalid native CSV cardinality")
                i,j=map(int,row[:2]);u,v,val=map(float,row[2:])
                if (i,j)!=(index%nu,index//nu):raise ValueError("Native grid index mismatch")
                number(val)
                if val<0:raise ValueError("Negative native road photometry")
                if not math.isclose(u,axis_u[i],abs_tol=1e-8) or not math.isclose(v,axis_v[j],abs_tol=1e-8):
                    raise ValueError("Native grid coordinate mismatch")
                if math.isclose(u,peak[1],rel_tol=0,abs_tol=1e-8) and math.isclose(v,peak[2],rel_tol=0,abs_tol=1e-8):
                    peak_sample=val
                count_values+=1;total+=val;minimum=min(minimum,val);maximum=max(maximum,val)
        if count_values!=nu*nv:raise ValueError("Incomplete native road grid")
        if not math.isclose(peak[0],maximum,rel_tol=1e-6,abs_tol=1e-12):
            raise ValueError("Native peak and CSV maximum disagree")
        if not any(math.isclose(peak[1],v,rel_tol=0,abs_tol=1e-8) for v in axis_u) or not any(math.isclose(peak[2],v,rel_tol=0,abs_tol=1e-8) for v in axis_v):
            raise ValueError("Native peak coordinate is not a grid center")
        if peak_sample is None or not math.isclose(peak[0],peak_sample,rel_tol=1e-6,abs_tol=1e-12):
            raise ValueError("Native peak position and CSV sample disagree")
        import struct
        for image_name in ([name+".png"] if name=="road" else ["driver.png","driver-color.png"]):
            with (directory/image_name).open("rb") as stream: header=stream.read(24)
            if len(header)!=24 or header[:8]!=b"\x89PNG\r\n\x1a\n" or header[12:16]!=b"IHDR" or struct.unpack(">II",header[16:24])!=(nu,nv):
                raise ValueError("Native PNG dimensions/signature mismatch")
        outputs[name]={"unit":unit,"shape_u_v":[nu,nv],"data_type":count(datatype),"angle_type":count(angle),
                       "bounds":[number(v) for v in sequence(bounds,4)],
                       "u_axis":axis_u,"v_axis":axis_v,"native_peak":peak,
                       "min":minimum,"max":maximum,"mean":total/count_values,
                       "lid":str(directory/f"{name}.lid"),"csv":str(directory/f"{name}.csv"),
                       "native_png":str(directory/f"{name}.png")}
    return {"backend":"LucidShape native static-road library / gatherSensorLight / luminanceSensorImage",
            "parameters":parameters,"coordinate_system":"X forward, Y left, Z up; metres",
            "outputs":outputs,"source_native":source,"road_reflectance":rho,
            "native_library_sha256":VERIFIED_ROAD_SCRIPT,
            "native_library_adapter":"Fixed hash-pinned computational functions; DEBUG blocks removed and only present optional objects added to the scene.",
            "camera_projection":"LucidShape classic field-of-view type 0; nominal U/V coordinates are not direct ray angles.",
            "reflectance_application":"Native Cdm2Sensor uses 1/pi; native driver UVData.scaleByFactor(rho); metre factor remains 1.",
            "native_png_export":{"method":"UVData.saveUVAsBMP","scale":"logarithmic, native automatic range",
                "driver_display_type":5,"pseudocolor_display_type":2,"photorealistic_eye_response":False},
            "additional_lamp_pitch_deg":0,
            "additional_flux_scale":1,"mirrored":False,"same_lid_for_each_lamp":True,
            "source_geometry_retraced":False,"regulatory_assessment":False,
            "scope":"Far-field point-source photometry on a flat Lambertian road; no vehicle or windshield.",
            "limits":["Absolute lamp aiming is not inferred from the IES filename.",
                      "No ambient light, occluders, atmospheric scatter or visual-adaptation calibration.",
                      "Native field-of-view/road-domain background pixels are not evidence of measured darkness."]},()

