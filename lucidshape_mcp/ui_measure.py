"""The seven operations in Studio's LID Measure Point or Zone dialog."""
import json
import math
from pathlib import Path

from .inputs import input_file, snapshot
from .results import number, sequence

OPERATION = "measure_photometry_region"
MODES = ("point", "zone_extrema", "minimum", "maximum", "zone_mean", "flux", "zone_flux")
UNITS = {"cd": 1.0, "lx_25m": 0.0016, "lx_10m": 0.01}
ZONES = ("zone_extrema", "zone_mean", "zone_flux")


def validate(args):
    mode = args["mode"]
    if mode not in MODES:
        raise ValueError("Unknown measurement mode")
    flux = mode in ("flux", "zone_flux")
    unit = args.get("unit") or ("lm" if flux else "cd")
    if (flux and unit != "lm") or (not flux and unit not in UNITS):
        raise ValueError("Use lm for flux and cd, lx_25m or lx_10m for intensity measurements")
    coords = args.get("coordinates_deg")
    coords = [] if coords is None else coords
    expected = 4 if mode in ZONES else 2 if mode == "point" else 0
    if not isinstance(coords, list) or len(coords) != expected:
        raise ValueError(f"{mode} requires {expected} coordinate values")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           or abs(v) > 360 for v in coords):
        raise ValueError("Coordinates must be finite angles in degrees")
    if mode in ZONES and not (coords[0] < coords[1] and coords[2] < coords[3]):
        raise ValueError("Zone requires [u_min, u_max, v_min, v_max] with positive extents")
    path = input_file(args["lid_path"], {".ies", ".lid"})
    return dict(lid_path=str(path), mode=mode, coordinates_deg=coords, unit=unit)


def stage(directory, p):
    path = Path(p["lid_path"])
    name = "input" + path.suffix.lower()
    inputs = snapshot(path, directory, name)
    coords = p["coordinates_deg"]
    if p["mode"] == "point":
        coords = [coords[0], coords[0], coords[1], coords[1]]
    coords = coords or [0, 0, 0, 0]
    script = Path(__file__).with_name("templates").joinpath("ui_measure.do").read_text(encoding="ascii")
    values = dict(INPUT=json.dumps(name), IES=int(name.endswith(".ies")), MODE=MODES.index(p["mode"]),
                  U0=coords[0], U1=coords[1], V0=coords[2], V1=coords[3])
    for key, value in values.items():
        script = script.replace(f"@@{key}@@", str(value))
    (directory / "job.do").write_text(script, encoding="ascii")
    return inputs, ("engine-result.json",)


def verify(rows, p):
    mode_id, bounds, raw, positions = sequence(rows, 4)
    mode = p["mode"]
    if number(mode_id) != MODES.index(mode):
        raise ValueError("Native measurement mode differs")
    h0, h1, v0, v1 = [number(v) for v in sequence(bounds, 4)]
    if h0 >= h1 or v0 >= v1:
        raise ValueError("Invalid photometry bounds")
    raw = [number(v) for v in sequence(raw, 2 if mode == "zone_extrema" else 1)]
    if any(v < 0 for v in raw) or (len(raw) == 2 and raw[0] > raw[1]):
        raise ValueError("Invalid native photometry result")
    count = {"zone_extrema": 2, "maximum": 1, "minimum": 1, "point": 1}.get(mode, 0)
    locations = [[number(x) for x in sequence(pt, 2)] for pt in sequence(positions, count)]
    requested = p["coordinates_deg"]
    if mode in ZONES:
        a, b, c, d = requested
        if not (h0-1e-6 <= a < b <= h1+1e-6 and v0-1e-6 <= c < d <= v1+1e-6):
            raise ValueError("Requested zone outside the sampled grid")
        if any(not (a-1e-6 <= u <= b+1e-6 and c-1e-6 <= v <= d+1e-6) for u,v in locations):
            raise ValueError("Native extrema outside the requested zone")
    if mode == "point" and not all(math.isclose(a,b,abs_tol=1e-7,rel_tol=1e-7)
                                  for a,b in zip(locations[0], requested)):
        raise ValueError("Native point differs from requested angles")
    if any(not (h0-1e-6 <= u <= h1+1e-6 and v0-1e-6 <= v <= v1+1e-6) for u,v in locations):
        raise ValueError("Native measurement position outside the sampled grid")
    unit = p["unit"]
    return {"mode": mode, "coordinates_deg": requested, "unit": unit,
            "values": [v * UNITS.get(unit, 1) for v in raw], "positions_deg": locations,
            "native_values": raw, "native_unit": "lm" if unit == "lm" else "cd",
            "sample_bounds_deg": bounds, "dialog": "LID Measure Point or Zone"}, []
