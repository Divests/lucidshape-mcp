"""Native Static Road LightData scene staging and verification.

Mirrors installed LS2.0 static-road sensor/camera/display semantics without
opening dialogs or calling UI input. A private Studio launcher is supplied by
the sibling native export module. This module never launches external commands
from user text; generated native statements are fixed, validated templates.
"""
from __future__ import annotations
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
import uuid
import zlib

# This script mirrors the installed LS2.0 Static Road profile and field semantics.
# Keep the same verified executable profile as uv_view_export.STUDIO_SHA256.
STUDIO_SHA256 = "60c8339e075346ffd224816810fdb50ecb2c5097d3ce891b5e06df13548312ab"


def _number(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be finite in [{low}, {high}]")
    return float(value)


def _vector(value, n, low, high, name):
    if not isinstance(value, (list, tuple)) or len(value) != n:
        raise ValueError(f"{name} requires {n} numbers")
    return [_number(x, low, high, name) for x in value]


def validate(arguments):
    p = dict(arguments)
    allowed = {"lid_path", "lamp_positions_m", "road_bounds_m", "road_cell_size_m", "driver_position_m",
               "driver_target_m", "top_position_m", "top_target_m", "lane_width_m", "illumination_type", "timeout_seconds"}
    if set(p) - allowed:
        raise ValueError("Unknown Static Road arguments")
    source = Path(p["lid_path"])
    if not source.is_absolute() or not source.is_file() or source.suffix.lower() not in {".ies", ".lid"}:
        raise ValueError("lid_path must be an existing absolute .ies/.lid file")
    p["lid_path"] = str(source.resolve())
    positions = p["lamp_positions_m"]
    if not isinstance(positions, (list, tuple)) or len(positions) != 2:
        raise ValueError("Static Road profile requires exactly two lamp positions sharing the same already-aimed data")
    p["lamp_positions_m"] = [_vector(x, 3, -100, 100, "lamp_positions_m") for x in positions]
    if any(pos[2] <= 0 for pos in p["lamp_positions_m"]):
        raise ValueError("Lamp height above road z=0 must be positive")
    b = p["road_bounds_m"] = _vector(p["road_bounds_m"], 4, -1000, 1000, "road_bounds_m")
    if b[1] <= b[0] or b[3] <= b[2] or b[0] < max(x[0] for x in positions):
        raise ValueError("Road bounds must increase and begin at or forward of the lamps")
    step = p["road_cell_size_m"] = _number(p.get("road_cell_size_m", .25), .1, 5, "road_cell_size_m")
    dims = [(b[1] - b[0]) / step, (b[3] - b[2]) / step]
    if any(not math.isclose(n, round(n), abs_tol=1e-8) or n < 2 or n > 4000 for n in dims) or math.prod(dims) > 1_000_000:
        raise ValueError("Road requires 2..4000 whole cells per axis and at most one million cells")
    for key, default in (("driver_position_m", [0, 0, 1.2]), ("top_position_m", [-25, 0, 20])):
        p[key] = _vector(p.get(key, default), 3, -1000, 1000, key)
        if p[key][2] <= 0:
            raise ValueError(f"{key} height must be positive")
    p["driver_target_m"] = _number(p.get("driver_target_m", 20), 1, 1000, "driver_target_m")
    p["top_target_m"] = _number(p.get("top_target_m", 50), 1, 1000, "top_target_m")
    p["lane_width_m"] = _number(p.get("lane_width_m", 3.6), 1, 10, "lane_width_m")
    if not b[2] < -p["lane_width_m"] < p["lane_width_m"] < b[3]:
        raise ValueError("Road lateral bounds must contain both native lane edge lines")
    p["illumination_type"] = p.get("illumination_type", "radial")
    if p["illumination_type"] != "radial":
        raise ValueError("This verified Static Road profile supports radial illuminance only")
    timeout = _number(p.get("timeout_seconds", 180), 30, 600, "timeout_seconds")
    if timeout != int(timeout):
        raise ValueError("timeout_seconds must be an integer")
    p["timeout_seconds"] = int(timeout)
    return p


def _native(value):
    if isinstance(value, (list, tuple)):
        return "(" + ",".join(_native(x) for x in value) + ")"
    return format(float(value), ".17g")


def _quote(value):
    return json.dumps(str(value).replace("\\", "/"), ensure_ascii=False)


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stage_static_road(directory, parameters):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    p = parameters
    source = Path(p["lid_path"])
    name = "input" + source.suffix.lower()
    before = _hash(source)
    shutil.copyfile(source, directory / name)
    if _hash(source) != before or _hash(directory / name) != before:
        raise ValueError("Photometry source changed while snapshotting")
    nonce = uuid.uuid4().hex
    p0, p1 = p["lamp_positions_m"]
    b = p["road_bounds_m"]
    substitutions = {
        "DIRECTORY": _quote(directory), "INPUT": _quote(name), "IS_IES": "1" if source.suffix.lower() == ".ies" else "0",
        "NONCE": _quote(nonce), "P0": _native(p0), "P1": _native(p1), "BOUNDS": _native(b),
        "CELL": _native(p["road_cell_size_m"]), "BACK_X": _native(b[1]),
        "BACK_BOUNDS": _native([b[2], b[3], 0, 8]), "WIDTH": _native(p["lane_width_m"]),
        "X0": _native(b[0]), "X1": _native(b[1]), "DRIVER": _native(p["driver_position_m"]),
        "DRIVER_TARGET": _native(p["driver_target_m"]), "TOP": _native(p["top_position_m"]),
        "TOP_TARGET": _native(p["top_target_m"])}
    template = (Path(__file__).with_name("templates") / "static_road_scene.lus").read_text(encoding="utf-8")
    script = re.sub(r"@@([A-Z0-9_]+)@@", lambda m: substitutions[m[1]], template)
    (directory / "job.lus").write_text(script, encoding="utf-8")
    manifest = {"nonce": nonce, "parameters": p, "source": str(source), "source_sha256": before,
                "snapshot": name, "script_sha256": _hash(directory / "job.lus"),
                "script_text_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
                "profile": "LS2.0 Static Road LuxSensor(radial=1), native Geo3D LightData/Munsell grey",
                "native_reference_files": ["include/road/roadSimulation.hdo", "include/road/setViewing.hdo", "include/road/roadGlobals.hdo", "tasks/snapshot3D.do"],
                "road_z_m": 0, "lane_marker_z_m": .001, "back_wall_height_m": 8,
                "lane_semantics": "native default: white edges Y=+-lane_width, yellow dashed center Y=0; width .15m, solid3m/gap6m",
                "display": {"type": "LightData", "surface_quantity": "lux", "radial": True, "map": "Munsell", "data_colors": "grey", "background_rgb255": [30, 30, 40], "field_of_view_y_deg": 40},
                "camera_target_semantics": "native tilt=eye_z/target; target is forward distance from eye, not world X coordinate",
                "photometric_quantity": "radial_lx", "extra_aim_deg": 0, "extra_scale": 1,
                "no_reflectance_or_cd_m2_claim": True}
    (directory / "request.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _unpack(value):
    if isinstance(value, dict) and set(value) == {"List"}:
        return _unpack(value["List"])
    if isinstance(value, dict) and set(value) == {f"p{i}" for i in range(len(value))}:
        return [_unpack(value[f"p{i}"]) for i in range(len(value))]
    return value


def verify_csv(path, metadata, bounds, step):
    if not isinstance(metadata, list) or len(metadata) != 7:
        raise ValueError("Bad native grid metadata")
    nu, nv, datatype, angle, in_lux, actual_bounds, peak = metadata
    if not all(isinstance(x, (int, float)) and x == int(x) and 2 <= x <= 4002 for x in (nu, nv)):
        raise ValueError("Bad grid dimensions")
    nu, nv = int(nu), int(nv)
    expected_u, expected_v = (round((bounds[i+1] - bounds[i]) / step) for i in (0, 2))
    if nu not in (expected_u, expected_u + 1) or nv not in (expected_v, expected_v + 1):
        raise ValueError("Native grid cardinality differs from requested cell geometry")
    if datatype != 3 or angle != 3 or in_lux != 1:
        raise ValueError("Road result must be native planar Lux data")
    if not isinstance(actual_bounds, list) or len(actual_bounds) != 4 or not isinstance(peak, list) or len(peak) != 3:
        raise ValueError("Native grid bounds/peak metadata is incomplete")
    if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in actual_bounds + peak):
        raise ValueError("Native grid bounds/peak metadata is nonfinite")
    for got, want in zip(actual_bounds, bounds):
        if not math.isfinite(got) or abs(got - want) > step + 1e-8:
            raise ValueError("Native grid lies outside requested geometry")
    values = []
    ux, vy = {}, {}
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.reader(stream)
        if next(rows, None) != ["u_index", "v_index", "u", "v", "value"]:
            raise ValueError("Bad native CSV header")
        for index, row in enumerate(rows):
            if len(row) != 5 or index >= nu * nv:
                raise ValueError("CSV cardinality mismatch")
            i, j = int(row[0]), int(row[1])
            if (i, j) != (index % nu, index // nu):
                raise ValueError("CSV index mismatch")
            u, v, value = map(float, row[2:])
            if not all(math.isfinite(t) for t in (u, v, value)) or value < 0:
                raise ValueError("Nonfinite or negative road value")
            if i in ux and not math.isclose(u, ux[i], abs_tol=1e-8) or j in vy and not math.isclose(v, vy[j], abs_tol=1e-8):
                raise ValueError("CSV inconsistent coordinate grid")
            ux[i], vy[j] = u, v
            values.append(value)
    if len(values) != nu * nv:
        raise ValueError("CSV truncated")
    for axis, start, stop in ((ux, bounds[0], bounds[1]), (vy, bounds[2], bounds[3])):
        data = [axis[i] for i in range(len(axis))]
        if not math.isclose(data[0], start, abs_tol=step + 1e-8) or not math.isclose(data[-1], stop, abs_tol=step + 1e-8):
            raise ValueError("CSV axis extent mismatch")
        if any(not math.isclose(y - x, step, abs_tol=1e-7) for x, y in zip(data, data[1:])):
            raise ValueError("CSV grid spacing mismatch")
    maximum = max(values)
    if not math.isclose(maximum, peak[0], rel_tol=1e-6, abs_tol=1e-9):
        raise ValueError("Native peak differs from CSV maximum")
    pi = next((i for i, x in ux.items() if math.isclose(x, peak[1], abs_tol=1e-8)), None)
    pj = next((j for j, y in vy.items() if math.isclose(y, peak[2], abs_tol=1e-8)), None)
    if pi is None or pj is None or not math.isclose(values[pj * nu + pi], maximum, rel_tol=1e-6, abs_tol=1e-9):
        raise ValueError("Native peak coordinate differs from CSV")
    return {"shape_u_v": [nu, nv], "unit": "radial_lx", "min": min(values), "mean": math.fsum(values) / len(values),
            "max": maximum, "peak_u_v_m": peak[1:], "actual_sample_bounds_m": [ux[0], ux[nu-1], vy[0], vy[nv-1]]}


def _png(path):
    from .uv_view_export import verify_png
    path = Path(path)
    if not 24 <= path.stat().st_size <= 64*1024*1024:
        raise ValueError("Native snapshot is empty or oversized")
    with path.open("rb") as stream:
        header = stream.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError("Native snapshot must be a verified PNG; unexpected format retained unchanged")
    width, height = struct.unpack(">II", header[16:24])
    if not 100 <= width <= 10000 or not 100 <= height <= 10000:
        raise ValueError("Unexpected native snapshot dimensions")
    try:
        verified = verify_png(path, width, height)
    except zlib.error as error:
        raise ValueError("Native PNG compressed pixel stream is invalid") from error
    return {"path": str(path), "sha256": verified["sha256"], "format": "PNG", "width": width,
            "height": height, "native_snapshot": True,
            "native_png_crc_and_pixel_stream_verified": verified["native_png_crc_and_pixel_stream_verified"]}


def verify_camera(camera, position, target):
    if len(camera) != 7 or camera[0] != position or not math.isclose(camera[1], target, abs_tol=1e-9):
        raise ValueError("Camera receipt differs from request")
    if len(camera[2]) != 3 or any(not math.isclose(a, b, abs_tol=1e-7) for a, b in zip(camera[2], position)):
        raise ValueError("Native camera position differs from request")
    expected = [1., 0., -position[2] / target]
    magnitude = math.sqrt(sum(x*x for x in expected))
    actual = camera[3]
    if len(actual) != 3 or not all(math.isfinite(x) for x in actual):
        raise ValueError("Native camera direction invalid")
    actual_magnitude = math.sqrt(sum(x*x for x in actual))
    if actual_magnitude <= 0 or any(not math.isclose(a / actual_magnitude, b / magnitude, abs_tol=1e-7) for a, b in zip(actual, expected)):
        raise ValueError("Native camera direction differs from manual Static Road profile")
    if not math.isclose(camera[4], 40, abs_tol=1e-7) or camera[6] != "Perspective":
        raise ValueError("Native camera projection/FOV differs from request")
    up = camera[5]
    if not isinstance(up, (list, tuple)) or len(up) != 3 or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in up):
        raise ValueError("Native camera up direction invalid")
    direction = [x / actual_magnitude for x in actual]
    dot = sum(a*b for a, b in zip(up, direction))
    projected_up = [a-dot*b for a, b in zip(up, direction)]
    up_length = math.sqrt(sum(x*x for x in projected_up))
    expected_up = [float(i == 2)-direction[2]*x for i, x in enumerate(direction)]
    expected_up_length = math.sqrt(sum(x*x for x in expected_up))
    if up_length <= 1e-10 or not math.isfinite(up_length) or any(not math.isclose(a/up_length, b/expected_up_length, abs_tol=1e-7) for a, b in zip(projected_up, expected_up)):
        raise ValueError("Native camera up direction is parallel, inverted, or rolled relative to world Z up")
    return {"position_m": camera[2], "view_direction": camera[3], "up_direction": camera[5], "fov_y_deg": camera[4], "projection": camera[6], "target_forward_m": target}


def finalize_static_road(directory, parameters, launch=None):
    directory = Path(directory)
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    data = _unpack(json.loads((directory / "native-result.json").read_text(encoding="utf-8-sig")))
    if len(data) != 12 or data[0] != 1 or data[1] != request["nonce"]:
        raise ValueError("Static Road receipt correlation failed")
    if _hash(directory / request["snapshot"]) != request["source_sha256"]:
        raise ValueError("Input snapshot changed")
    if _hash(Path(request["source"])) != request["source_sha256"]:
        raise ValueError("Original input changed during job")
    if _hash(directory / "job.lus") != request["script_sha256"]:
        raise ValueError("Native job script changed")
    if launch and not launch.get("private_process_started"):
        raise ValueError("Static Road verification supports only a private owned Studio process")
    if _hash(directory / "native-job.lus") != request["script_sha256"]:
        raise ValueError("Native job script changed")
    source, road, back, driver, top, remaining_models, prior_models, preserved, model_name, view_name = data[2:]
    if not isinstance(prior_models, (int, float)) or prior_models < 0 or prior_models != int(prior_models) or remaining_models != prior_models + 1 or preserved != 1:
        raise ValueError("Static Road did not preserve prior models and add exactly one new model")
    if len(source) != 5 or source[0] not in (0, 1) or min(source[1:3]) < 2 or source[3] <= 0 or source[4] <= 0:
        raise ValueError("Native input photometry invalid")
    road_metrics = verify_csv(directory / "road.csv", road, parameters["road_bounds_m"], parameters["road_cell_size_m"])
    back_bounds = [parameters["road_bounds_m"][2], parameters["road_bounds_m"][3], 0, 8]
    back_metrics = verify_csv(directory / "back-wall.csv", back, back_bounds, .5)
    cameras = {name: verify_camera(camera, parameters[key], parameters[target_key])
               for name, camera, key, target_key in (("driver", driver, "driver_position_m", "driver_target_m"), ("top20", top, "top_position_m", "top_target_m"))}
    images = {name: _png(directory / f"{name}.png") for name in ("driver", "top20", "road-map")}
    if any((images[name]["width"], images[name]["height"]) != (1600, 900) for name in ("driver", "top20")):
        raise ValueError("Native Geo3D snapshot dimensions differ from 1600x900")
    for filename in ("road.lid", "back-wall.lid", "scene.lue"):
        if not (directory / filename).is_file() or (directory / filename).stat().st_size < 32:
            raise ValueError("Native saved data missing: " + filename)
    result = {"status": "succeeded", "method": request["profile"], "parameters": parameters,
              "native_source": {"angle_type": source[0], "size_u_v": source[1:3], "domain_flux_lm": source[3], "peak_cd": source[4]},
              "road": road_metrics, "back_wall": back_metrics, "images": images, "cameras": cameras,
              "native_models_before": prior_models, "native_models_after": remaining_models,
              "prior_models_preserved": True, "native_views_retained_during_export": True,
              "native_scene_model_name": model_name, "native_scene_view_name": view_name,
              "road_lid": str(directory / "road.lid"), "road_csv": str(directory / "road.csv"),
              "scene_file": str(directory / "scene.lue"), "provenance": request,
              "scene_file_kind": "Geometry/light/sensor scene snapshot; not the original optical design project",
              "limitations": ["Radial lux omits the horizontal incidence cosine and is not horizontal road illuminance.",
                              "Native LightData Munsell grayscale is a display mapping, not a photometric luminance image or calibrated visual prediction.",
                              "Two identical already-aimed module patterns; no additional aim or scale and no optical CAD retracing."]}
    (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return result


async def generate_views(root: Path, executable: Path, *, process_id=None, transport=None, **arguments) -> dict:
    if process_id is not None:
        raise ValueError("Existing-Studio export is not supported by the verified Static Road profile; use the private owned process with no Studio already running")
    try:
        from .private_studio import run_private_studio, close_private_studio
    except ImportError:
        from private_studio import run_private_studio, close_private_studio
    p = validate(arguments)
    executable = Path(executable).resolve()
    if executable.name.lower() == "lush.exe":
        executable = executable.with_name("LucidShape.exe")
    if executable.name.lower() != "lucidshape.exe" or not executable.is_file():
        raise ValueError("Static Road views require an installed LucidShape.exe")
    if _hash(executable) != STUDIO_SHA256:
        raise ValueError("Static Road native export requires the verified LS2.0 Studio binary profile")
    directory = Path(root).resolve() / ("static-road-" + uuid.uuid4().hex)
    stage_static_road(directory, p)
    script = (directory / "job.lus").read_text(encoding="utf-8")
    launched = None
    result = {"status": "unverified", "work_directory": str(directory),
              "automatic_resubmission_allowed": False, "computer_use_calls": 0}
    try:
        launched = await run_private_studio(executable, directory, script, "native-result.json", p["timeout_seconds"])
        result["launch"] = launched
        result["process_left_open"] = launched.get("process_left_open", False)
        if launched.get("status") != "receipt_ready":
            result["status"] = launched.get("status", "unverified")
            result["error_details"] = launched.get("error_details")
            result["message"] = launched.get("error", "Native Static Road job did not provide a complete verified receipt")
            if "private_process_cleanup" in launched:
                result["private_process_cleanup"] = launched["private_process_cleanup"]
        else:
            result.update(finalize_static_road(directory, p, launched))
    except Exception as error:
        result.update(status="unverified", message=f"Native Static Road export was not verified: {type(error).__name__}: {error}")
        result["error_details"] = {"code": "NATIVE_ARTIFACT_VALIDATION_FAILED",
                                   "message": result["message"], "retryable": False}
    finally:
        if launched is not None and launched.get("status") == "receipt_ready":
            result["private_process_cleanup"] = await close_private_studio(launched)
            result["process_left_open"] = not result["private_process_cleanup"].get("closed", False)
        result["created_scene_view_retained"] = result.get("process_left_open", False)
        (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return result
