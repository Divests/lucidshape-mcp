"""Independent numeric/structural checks of native TILT=NONE IES output."""
from __future__ import annotations
import math
from pathlib import Path
import re


def inspect_ies(path: Path) -> dict:
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('IES output exceeds the inspection limit')
    lines = path.read_text(encoding='utf-8-sig', errors='replace').splitlines()
    if not lines or not lines[0].strip().upper().startswith('IES'):
        raise ValueError('Missing IES header')
    tilt = [i for i,line in enumerate(lines) if line.strip().upper().startswith('TILT=')]
    if len(tilt)!=1 or lines[tilt[0]].strip().upper()!='TILT=NONE':
        raise ValueError('Only one native TILT=NONE block is supported')
    try:
        values=[float(item) for item in re.split(r'[\s,]+',' '.join(lines[tilt[0]+1:]).strip())]
    except ValueError as exc:
        raise ValueError('Non-numeric IES photometry block') from exc
    if len(values)<13 or any(not math.isfinite(value) for value in values):
        raise ValueError('Incomplete or non-finite IES data')
    def integer(index,minimum,maximum):
        value=values[index]
        if int(value)!=value or not minimum<=value<=maximum:
            raise ValueError('Invalid IES integer field')
        return int(value)
    lamps=integer(0,1,1000000)
    nv,nh=integer(3,2,100000),integer(4,2,100000)
    kind,units=integer(5,1,3),integer(6,1,2)
    multiplier=values[2]
    if multiplier<=0 or nv*nh>2000000 or len(values)!=13+nv+nh+nv*nh:
        raise ValueError('IES data count or candela multiplier is invalid')
    vertical=values[13:13+nv]
    horizontal=values[13+nv:13+nv+nh]
    for axis in (vertical,horizontal):
        if any(b<=a for a,b in zip(axis,axis[1:])):
            raise ValueError('IES angles must be strictly increasing')
    intensities=values[13+nv+nh:]
    if min(intensities)<0:
        raise ValueError('Negative IES candela data')
    return {'header':lines[0].strip(),'photometric_type':{1:'C',2:'B',3:'A'}[kind],
            'lamp_count':lamps,'vertical_angles':nv,'horizontal_angles':nh,
            'vertical_range_deg':[vertical[0],vertical[-1]],
            'horizontal_range_deg':[horizontal[0],horizontal[-1]],
            'candela_multiplier':multiplier,'peak_cd':max(intensities)*multiplier,
            'intensity_count':len(intensities),'length_units':{1:'feet',2:'metres'}[units],
            'checks':'numeric fields, angle order, dimensions, nonnegative candela, exact data count; not a full LM-63 certification'}


def sample_bounds(bounds,grid):
    # Native UVData.range reports outer cell edges, including IES readback.
    # borderIsOnCellCenter describes the storage convention, not range() output.
    result=[]
    for i,n in enumerate(grid):
        low,high=bounds[2*i:2*i+2]
        if not low<high or n<2:
            raise ValueError('Invalid photometry sampling grid')
        offset=(high-low)/(2*n)
        result.extend([low+offset,high-offset])
    return result
