"""Construct a private, axis-aligned Studio model and verify its reloaded geometry."""
import math
from pathlib import Path

from .results import count, number, sequence

OPERATION = "create_geometry_primitive"
_CLASSES = {"plane": "PlanarSurface", "sphere": "SphericalSurface", "disk": "ConeSurface",
            "cone": "ConeSurface", "cylinder": "CylinderSurface", "box": "BoxShape"}
_SIZES = {"plane": 2, "sphere": 1, "cylinder": 2, "box": 3, "disk": 2, "cone": 3}
_CALLBACKS = {"create_PlanarSurface": "plane", "create_SphericalSurface": "sphere",
              "create_CylinderSurface": "cylinder", "create_DiskSurface": "disk", "create_ConeSurface": "cone"}


def menu_preset(action):
    if action.get("kind") != "script":
        return None
    callback, args = action.get("callback"), action.get("arguments")
    if callback == "create_Box" and args == []:
        return {"primitive": "box", "as_shape": True}
    if callback in _CALLBACKS and args in (["0"], ["1"]):
        return {"primitive": _CALLBACKS[callback], "as_shape": args == ["1"]}
    return None


def _values(values, length, name, low):
    if not isinstance(values, list) or len(values) != length or any(
        isinstance(value, bool) or not isinstance(value, (float, int))
        or not math.isfinite(value) or not low <= value <= 1e6 for value in values
    ):
        raise ValueError(f"{name} requires {length} finite values in [{low}, 1000000]")
    return [float(value) for value in values]


def validate(args):
    primitive = args.get("primitive")
    if not isinstance(primitive, str) or primitive not in _CLASSES:
        raise ValueError("Unrecognized geometry primitive")
    center = args.get("center_mm")
    as_shape = args.get("as_shape")
    if as_shape is None:
        as_shape = primitive == "box"
    if not isinstance(as_shape, bool) or (primitive == "box" and not as_shape):
        raise ValueError("as_shape must be boolean; the native box is always a shape")
    low = 0 if primitive in ("disk", "cone") else 0.001
    sizes = _values(args.get("dimensions_mm"), _SIZES[primitive], "dimensions_mm", low)
    if primitive == "disk" and (sizes[0] - sizes[1] < 0.001):
        raise ValueError("Disk dimensions require outer radius at least 0.001 mm larger than inner radius")
    if primitive == "cone" and (abs(sizes[0] - sizes[1]) < 0.001 or sizes[2] < 0.001):
        raise ValueError("Cone dimensions require different end radii and a positive axial length")
    return {"primitive": primitive, "dimensions_mm": sizes, "as_shape": as_shape,
        "center_mm": _values(center if center is not None else [0, 0, 0], 3, "center_mm", -1e6)}


def stage(directory, p):
    shape = p["primitive"]
    size = p["dimensions_mm"]
    coordinates = ",".join(format(value, ".17g") for value in p["center_mm"])
    # All program fragments are chosen here; caller input supplies only checked numeric literals.
    constructors = {
        "plane": 'PlanarSurface("primitive",(1,0,0,1),axis,(-a/2,a/2,-b/2,b/2))',
        "sphere": 'SphericalSurface("primitive",(1,0,0,1),axis,a)',
        "cylinder": 'CylinderSurface("primitive",(1,0,0,1),axis,a,a,-b/2,b/2,$,$)',
        "disk": 'ConeSurface("primitive",(1,0,0,1),axis,a,b,0,0,$,$,1)',
        "cone": 'ConeSurface("primitive",(1,0,0,1),axis,a,b,-c/2,c/2,$,$,1)',
        "box": 'BoxShape("primitive",(1,0,0,1),Absorber("Absorber"),axis,(-a/2,a/2,-b/2,b/2,-c/2,c/2),0,(1,1,1))',
    }
    values = {"CENTER": coordinates, "CONSTRUCT": constructors[shape], "CLASS": _CLASSES[shape],
              "WRAP": str(int(p["as_shape"] and shape != "box")),
              "CONE": str(int(shape in ("disk", "cone")))}
    values.update(zip(("A", "B", "C"), (format(v, ".17g") for v in (size + [0, 0])[:3])))
    script = Path(__file__).with_name("templates").joinpath("ui_geometry.do").read_text(encoding="ascii")
    for key, value in values.items():
        script = script.replace(f"@@{key}@@", value)
    (directory / "job.do").write_text(script, encoding="ascii")
    return [], ("engine-result.json", "primitive.lue")


def verify(rows, p):
    loaded, matches, object_name, class_name, computed_bounds, bounds, shape_info, radial = sequence(rows, 8)
    shape, dimensions = p["primitive"], p["dimensions_mm"]
    if (count(loaded), count(matches), object_name, class_name, count(computed_bounds)) != (
            1, 1, "primitive", _CLASSES[shape], 1):
        raise ValueError("Reloaded geometry identity or bounding box was not verified")
    if shape == "plane":
        extents = [dimensions[0]/2, dimensions[1]/2, 0]
    elif shape == "sphere":
        extents = dimensions * 3
    elif shape == "cylinder":
        extents = [dimensions[0], dimensions[0], dimensions[1]/2]
    elif shape in ("disk", "cone"):
        radius = max(dimensions[:2])
        extents = [radius, radius, dimensions[2]/2 if shape == "cone" else 0]
    else:
        extents = [v/2 for v in dimensions]
    expected = [value for c, h in zip(p["center_mm"], extents) for value in (c - h, c + h)]
    bounds = [number(v) for v in sequence(bounds, 6)]
    if any(not math.isclose(a, b, abs_tol=1e-6, rel_tol=1e-9) for a, b in zip(bounds, expected)):
        raise ValueError(f"Reloaded {shape} bounding box differs from the requested geometry")
    expected_material = "Absorber" if shape == "box" else "Specular" if p["as_shape"] else ""
    expected_shape = "BoxShape" if shape == "box" else "SurfaceShape" if p["as_shape"] else ""
    n, wrapper, material, reflectance = sequence(shape_info, 4)
    if (count(n), wrapper, material, number(reflectance)) != (
            int(p["as_shape"]), expected_shape, expected_material, 1 if expected_material == "Specular" else -1):
        raise ValueError("Reloaded shape or its actor material differs from the requested construction")
    if shape in ("disk", "cone"):
        half = dimensions[2]/2 if shape == "cone" else 0
        expected_radial = [*dimensions[:2], -half, half]
        actual = [number(v) for v in sequence(radial, 4)]
        if any(not math.isclose(a, b, abs_tol=1e-6, rel_tol=1e-9) for a, b in zip(actual, expected_radial)):
            raise ValueError("Reloaded cone or disk radii and axial limits differ from request")
    return {**p, "native_class": class_name, "reread_bounds_mm": bounds, "model": "primitive.lue",
            "scope": "private new model", "shape_class": wrapper or None, "material": material or None}, []
