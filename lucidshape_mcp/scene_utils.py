"""Shared scene validation, immutable snapshots and coordinate conditioning."""
import hashlib
import json
import math
from pathlib import Path
from .inputs import input_file, MAX_BYTES


def _number(v, low, high):
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not low<=v<=high:
        raise ValueError('Invalid optical scene number')
    return float(v)


def _read_json(path):
    p=input_file(path,{'.json'})
    if p.stat().st_size>1024*1024:
        raise ValueError('Optical scene manifest too large')
    return p,json.loads(p.read_text(encoding='utf-8'))


def condition_setup(setup,scale):
    """Uniformly condition private native geometry; always record actual bounds."""
    scale=_number(scale,1,1000)
    surface_anchor='if(face.isOfClassName("Surface")) {'
    report_anchor='regions << (label,count,skipped,material.nameOfClass());'
    if setup.count(surface_anchor)!=1 or setup.count(report_anchor)!=1:
        raise ValueError('Native coordinate-conditioning anchors differ')
    setup=f'object geometryBounds=();\nobject coordinateTransform=AxisSystem("diagnostic uniform coordinate scale",$,(0,0,0),(0,0,1),(1,0,0),0);\nif(!coordinateTransform.setScale({scale:.17g},{scale:.17g},{scale:.17g})) return;\n'+setup
    if scale!=1:
        setup=setup.replace(surface_anchor,surface_anchor+'\n            if(!face.transform(coordinateTransform)) { cout << "LUCIDSHAPE_MCP_FAILED: coordinate transform failed" << endl; return 0; }')
    return setup.replace(report_anchor,
        'double xmin=1e100,xmax=-1e100,ymin=1e100,ymax=-1e100,zmin=1e100,zmax=-1e100;\n    if(!imported.checkBoundingBox(xmin,xmax,ymin,ymax,zmin,zmax)) return 0;\n    geometryBounds << (label,(xmin,xmax,ymin,ymax,zmin,zmax));\n    '+report_anchor)


def validate_conditioned_bounds(bounds,parameters):
    scale=parameters.get('coordinate_scale',1)
    if len(bounds)!=len(parameters['regions']):
        raise ValueError('Native transformed bounds missing')
    for (name,box),spec in zip(bounds,parameters['regions']):
        if name!=spec['label'] or len(box)!=6 or any(not math.isfinite(v) for v in box):
            raise ValueError('Invalid native geometry bounds')
        expected=(spec.get('mesh') or {}).get('vertex_bounds_mm') or spec.get('native_bounding_box_mm')
        if scale!=1 and expected is None:
            raise ValueError('Conditioned geometry requires independently recorded original bounds')
        if expected and max(abs(a/scale-b) for a,b in zip(box,expected))>.00002:
            raise ValueError('Uniform coordinate transformation changed geometry bounds')


def stage(directory:Path,parameters:dict):
    inputs=[]
    files=[(r['path'],f'region_{i}{r["extension"]}',r['sha256']) for i,r in enumerate(parameters['regions'])]
    if parameters.get('include_rayfile_emitters',True):
        files.append((parameters['rayfile_path'],'source.ray',parameters['rayfile_sha256']))
    total=0
    for original,name,expected_hash in files:
        p=Path(original)
        data=p.read_bytes()
        total+=len(data)
        if total>MAX_BYTES or hashlib.sha256(data).hexdigest()!=expected_hash:
            raise ValueError('Optical input changed while staging or exceeded size limit')
        (directory/name).write_bytes(data)
        inputs.append({'source':str(p),'staged_name':name,'source_sha256':expected_hash,'source_bytes':len(data)})
    return inputs

