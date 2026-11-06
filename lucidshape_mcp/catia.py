"""Validated requests for fixed CATIA Automation tasks.

COM objects live only in a dedicated worker process; they never cross an asyncio
or thread boundary. This module can be imported without pywin32 installed.
"""
from __future__ import annotations

import math

from .inputs import input_file


def parameters(action: str, width_mm=100, height_mm=80, tilt_deg=0, catia_path=None, save_as_source=False, include_unsaved=False,cad_path=None,body_name=None) -> dict:
    if action == "catia_get_state":
        return {}
    if action=='catia_convert_step_to_stl':
        return {'cad_path':str(input_file(cad_path,{'.stp','.step'}))}
    if action=='catia_export_body_stl':
        if not isinstance(body_name,str) or not body_name.strip() or len(body_name)>256 or any(ord(c)<32 for c in body_name) or not isinstance(include_unsaved,bool):
            raise ValueError('An explicit nonempty CATIA body name and boolean include_unsaved are required')
        return {'catia_path':str(input_file(catia_path,{'.catpart'})),'body_name':body_name,'include_unsaved':include_unsaved}
    if action in ("catia_export_step","catia_show_all","catia_inspect_part"):
        result={"catia_path": str(input_file(catia_path, {".catpart"}))}
        if action=="catia_show_all":
            if not isinstance(save_as_source,bool):
                raise ValueError("save_as_source must be boolean")
            result["save_as_source"]=save_as_source
        if action=="catia_export_step":
            if not isinstance(include_unsaved,bool):
                raise ValueError("include_unsaved must be boolean")
            result["include_unsaved"]=include_unsaved
        return result
    if action != "catia_create_plane_mirror":
        raise ValueError("Unknown CATIA action")
    limits = {"width_mm": (width_mm, 1, 1000), "height_mm": (height_mm, 1, 1000), "tilt_deg": (tilt_deg, -15, 15)}
    result = {}
    for name, (value, lower, upper) in limits.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper:
            raise ValueError(f"{name} must be finite and between {lower} and {upper}")
        result[name] = value
    return result
