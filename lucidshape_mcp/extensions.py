"""Version-bound optical edits and signed photometry comparisons in private jobs."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

from .inputs import decode_text, input_file, snapshot
from .results import count, number, sequence, string
from .studio_queries import common_script

OPERATIONS = {"compare_photometry", "inspect_optical_parameters", "set_emitter_parameters", "run_parameter_sweep"}
VERIFIED_ENGINE = "1e64931b79d18a06f6a5eeb00663f23f33bd236428dc97568269ae53d2db9a48"
TEMPLATES = Path(__file__).with_name("templates")


def bounded(value, low, high, name, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high or (integer and not isinstance(value, int)):
        raise ValueError(f"{name} must be {'an integer' if integer else 'finite'} in [{low}, {high}]")
    return value


def changes(value):
    if not isinstance(value, dict) or not value or set(value) - {"flux_lm", "half_angle_deg", "trim_angle_deg"}:
        raise ValueError("Provide only flux_lm, half_angle_deg and/or trim_angle_deg")
    limits = {"flux_lm": (0.001, 1e6), "half_angle_deg": (0.001, 89.999), "trim_angle_deg": (0.001, 90)}
    return {name: bounded(val, *limits[name], name) for name, val in value.items()}


def validate(operation, arguments):
    if operation not in OPERATIONS:
        raise ValueError("Unknown optical extension")
    p = dict(arguments)
    if operation == "compare_photometry":
        for key in ("reference_path", "candidate_path"):
            p[key] = str(input_file(p[key], {".lid", ".ies"}))
        return p
    p["model_path"] = str(input_file(p["model_path"], {".lue", ".lug"}))
    if operation == "inspect_optical_parameters":
        bounded(p["max_objects"], 1, 1000, "max_objects", True)
        return p
    name = p["source_name"]
    if not isinstance(name, str) or not name or len(name) > 256 or any(ord(c) < 32 for c in name):
        raise ValueError("source_name must be an exact nonempty object name, at most 256 characters")
    if operation == "set_emitter_parameters":
        p["changes"] = changes(p["changes"])
    else:
        if not isinstance(p["cases"], list) or not 1 <= len(p["cases"]) <= 16:
            raise ValueError("cases must contain 1..16 parameter dictionaries")
        p["cases"] = [changes(case) for case in p["cases"]]
        bounded(p["rays"], 100, 1000000, "rays", True)
        bounded(p["seed"], 1, 2147483647, "seed", True)
        bounded(p["recursion_limit"], 1, 256, "recursion_limit", True)
        bounded(p["intensity_threshold"], 0, 1, "intensity_threshold")
        if p["rays"] * len(p["cases"]) > 4000000:
            raise ValueError("Sweep is limited to four million requested rays in total")
    return p


def native_changes(change):
    return "(" + ",".join(format(change.get(name, -1), ".17g") for name in ("flux_lm", "half_angle_deg", "trim_angle_deg")) + ")"


def stage(directory, operation, p):
    replacements = {}
    expected = ["engine-result.json"]
    if operation == "compare_photometry":
        inputs = []
        for label, key, token in (("reference", "reference_path", "REF"), ("candidate", "candidate_path", "CAND")):
            path = Path(p[key]); name = label + path.suffix.lower()
            inputs.extend(snapshot(path, directory, name, asset_prefix=label + "_"))
            replacements[label.upper()] = json.dumps(name)
            replacements[token + "_IES"] = "1" if path.suffix.lower() == ".ies" else "0"
        expected += ["grid_0.csv", "grid_1.csv"]
    else:
        if operation == "run_parameter_sweep":
            inputs, hashes = [], {}
            for index in range(len(p["cases"])):
                staged = snapshot(Path(p["model_path"]), directory, f"case_{index}_input.lue", asset_prefix=f"case_{index}_")
                for entry in staged:
                    previous = hashes.setdefault(entry["source"], entry["source_sha256"])
                    if previous != entry["source_sha256"]:
                        raise ValueError("Sweep baseline changed during staging")
                inputs.extend(staged)
                if len(inputs) > 512 or sum(entry["source_bytes"] for entry in inputs) > 512 * 1024 * 1024:
                    raise ValueError("Sweep input copies exceed 512 files or 512 MiB")
        else:
            inputs = snapshot(Path(p["model_path"]), directory, "input.lue")
        if operation == "inspect_optical_parameters":
            replacements["LIMIT"] = str(p["max_objects"])
        else:
            replacements["SOURCE"] = json.dumps(p["source_name"], ensure_ascii=False)
            if operation == "set_emitter_parameters":
                replacements["CHANGE"] = native_changes(p["changes"])
                expected.append("modified.lue")
            else:
                replacements.update(CASE_ROWS="\n".join("cases << " + native_changes(c) + ";" for c in p["cases"]),
                                    RAYS=str(p["rays"]), SEED=str(p["seed"]), RECURSION=str(p["recursion_limit"]),
                                    INTENSITY=format(p["intensity_threshold"], ".17g"))
    script = common_script() + (TEMPLATES / "extension_helpers.do").read_text(encoding="utf-8") + "\n" + (TEMPLATES / (operation + ".do")).read_text(encoding="utf-8")
    # One replacement pass: a user name containing @@...@@ is never interpreted.
    import re
    script = re.sub(r"@@([A-Z_]+)@@", lambda m: replacements[m[1]], script)
    (directory / "job.do").write_text(script, encoding="utf-8")
    return inputs, tuple(expected)


def description(value):
    exists, name, kind = sequence(value, 3)
    if exists not in (0, 1):
        raise ValueError("Invalid native object description")
    return {"present": bool(exists), "name": string(name), "type": string(kind)}


def numeric(value, default=None):
    kind, val = sequence(value, 2)
    if kind == 1:
        return {"status": "explicit", "value": number(val)}
    if kind == 22:
        return {"status": "default", "value": default}
    return {"status": "unsupported", "value": None, "native_type_code": count(kind)}


def emitter(value):
    supported, name, kind, fields = sequence(value, 4)
    result = {"name": string(name), "type": string(kind), "status": "unsupported"}
    if supported == 0:
        return result
    if supported != 1 or kind != "LambertEmitter":
        raise ValueError("Unsupported native emitter profile")
    unit, flux, half, trim, medium = sequence(fields, 5)
    if unit not in (0, 1) or number(flux) < 0:
        raise ValueError("Invalid emitter flux")
    result.update(status="available", flux={"value": flux, "unit": "lm" if unit == 0 else "W"},
                  half_angle_deg=numeric(half, 60), trim_angle_deg=numeric(trim, 90),
                  start_medium=description(medium), spectral=False, split_rays=False)
    return result


def vector(value, origin=False):
    sequence(value)
    row = value[2] if origin and len(value) == 3 else value[0] if len(value) == 1 else None
    return [number(v) for v in sequence(row, 3)]


def source(value):
    name, kind, origin, z, x, efficacy, em = sequence(value, 7)
    if kind not in ("PointLight", "PointLightSource"):
        raise ValueError("Expected a PointLight source")
    return {"name": string(name), "type": kind, "origin_mm": vector(origin, True),
            "z_direction": vector(z), "x_direction": vector(x), "efficacy": numeric(efficacy), "emitter": emitter(em)}


def verify_edit(before, after, change, name):
    if before["name"] != name or after["name"] != name:
        raise ValueError("Modified source identity differs")
    if {k: v for k, v in before.items() if k != "emitter"} != {k: v for k, v in after.items() if k != "emitter"}:
        raise ValueError("Source coordinates or efficacy changed unexpectedly")
    old, new = before["emitter"], after["emitter"]
    if old["status"] != "available" or new["status"] != "available":
        raise ValueError("Unsupported emitter modification")
    expected = json.loads(json.dumps(old))
    for name, value in change.items():
        if name == "flux_lm":
            if old["flux"]["unit"] != "lm":
                raise ValueError("flux_lm cannot modify a radiometric emitter")
            expected["flux"]["value"] = value
        else:
            expected[name] = {"status": "explicit", "value": value}
    if expected != new:
        raise ValueError("Emitter readback differs or unrelated decoded parameters changed")


def grid_meta(raw):
    name, angle, nu, nv, bounds, u, v, peak, flux = sequence(raw, 9)
    nu, nv = count(nu), count(nv)
    if angle not in (0, 1, 2) or min(nu, nv) < 2 or nu * nv > 1000000:
        raise ValueError("Invalid photometry dimensions")
    u, v = [number(n) for n in sequence(u, nu)], [number(n) for n in sequence(v, nv)]
    if any(b <= a for axis in (u, v) for a, b in zip(axis, axis[1:])):
        raise ValueError("Grid directions must be strictly increasing")
    return {"name": string(name), "angle_type": {0: "B", 1: "A", 2: "C"}[angle], "unit": "cd",
            "size": [nu, nv], "bounds_deg": [number(n) for n in sequence(bounds, 4)],
            "u_deg": u, "v_deg": v, "peak": dict(zip(("cd", "u_deg", "v_deg"), map(number, sequence(peak, 3)))),
            "flux_in_domain_lm": number(flux)}


def read_grid(path, nu, nv):
    if path.stat().st_size > 80 * 1024 * 1024:
        raise ValueError("Photometry grid is too large")
    values = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.reader(stream)
        if next(rows, None) != ["u_index", "v_index", "intensity_cd"]:
            raise ValueError("Invalid grid CSV header")
        for index, row in enumerate(rows):
            if len(row) != 3 or index >= nu * nv or float(row[0]) != index % nu or float(row[1]) != index // nu:
                raise ValueError("Grid coverage/order differs")
            val = number(float(row[2]))
            if val < 0:
                raise ValueError("Input photometry contains negative intensity")
            values.append(val)
    if len(values) != nu * nv:
        raise ValueError("Incomplete grid")
    return values


def compare(directory, raw):
    a, b = [grid_meta(row) for row in sequence(raw, 2)]
    if a["angle_type"] != b["angle_type"] or a["size"] != b["size"]:
        raise ValueError("Photometry angle type/dimensions differ; resampling is not implicit")
    for field in ("u_deg", "v_deg", "bounds_deg"):
        if any(not math.isclose(x, y, rel_tol=0, abs_tol=1e-9) for x, y in zip(a[field], b[field])):
            raise ValueError("Photometry sampling directions/ranges differ; resampling is not implicit")
    nu, nv = a["size"]
    av, bv = read_grid(directory / "grid_0.csv", nu, nv), read_grid(directory / "grid_1.csv", nu, nv)
    delta = [y - x for x, y in zip(av, bv)]
    with (directory / "difference.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream); writer.writerow(["u_deg", "v_deg", "reference_cd", "candidate_cd", "candidate_minus_reference_cd", "relative_change_pct"])
        for i, (x, y, d) in enumerate(zip(av, bv, delta)):
            writer.writerow([a["u_deg"][i % nu], a["v_deg"][i // nu], x, y, d, d / x * 100 if x else ""])
    denominator = math.fsum(abs(x) for x in av)
    return {"reference": a, "candidate": b, "signed_difference": "candidate - reference", "cells_compared": len(delta),
            "resampled": False, "min_delta_cd": min(delta), "max_delta_cd": max(delta),
            "rmse_cd": math.sqrt(math.fsum(x * x for x in delta) / len(delta)),
            "mae_cd": math.fsum(abs(x) for x in delta) / len(delta),
            "relative_l1": math.fsum(abs(x) for x in delta) / denominator if denominator else None,
            "relative_error_zero_reference_cells": sum(x == 0 for x in av),
            "increased_cells": sum(x > 0 for x in delta), "decreased_cells": sum(x < 0 for x in delta),
            "peak_delta_cd": b["peak"]["cd"] - a["peak"]["cd"],
            "peak_coordinate_shift_deg": {key: b["peak"][key] - a["peak"][key] for key in ("u_deg", "v_deg")},
            "flux_delta_in_domain_lm": b["flux_in_domain_lm"] - a["flux_in_domain_lm"],
            "difference_csv": str(directory / "difference.csv"), "regulatory_assessment": False}, ["difference.csv"]


def read_report(directory, operation, p):
    path = directory / "engine-result.json"
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Optical extension receipt is too large")
    envelope = json.loads(decode_text(path.read_bytes())[0])
    if not isinstance(envelope, dict) or set(envelope) != {"List"} or not isinstance(envelope["List"], dict):
        raise ValueError("Invalid optical extension receipt")
    fields = envelope["List"]
    if set(fields) != {f"p{i}" for i in range(len(fields))}:
        raise ValueError("Invalid receipt field numbering")
    rows = [fields[f"p{i}"] for i in range(len(fields))]
    if operation == "compare_photometry":
        return compare(directory, rows)
    if operation == "inspect_optical_parameters":
        if len(rows) != 4:
            raise ValueError("Missing optical groups")
        groups = {}
        for expected, row in zip(("Shape", "EmitterMaterial", "Medium", "SensorMaterial"), rows):
            kind, total, items = sequence(row, 3)
            if kind != expected or len(sequence(items)) != min(count(total), p["max_objects"]):
                raise ValueError("Invalid optical group coverage")
            decoded = []
            for item in items:
                name, cls, status, details = sequence(item, 4)
                result = {"name": string(name), "type": string(cls), "status": status}
                if status == "available":
                    result["parameters"] = source(details) if cls in ("PointLight", "PointLightSource") else emitter(details)
                elif status == "partial" and cls == "Medium":
                    refraction, absorption = sequence(details, 2)
                    result["parameters"] = {"refractive_index": numeric(refraction), "absorption_or_scatter": numeric(absorption),
                                            "spectral_parameters": "not_decoded", "absorption_unit": "not_established"}
                elif status == "relationships_only":
                    result["relationships"] = dict(zip(("material", "exit_material"), [description(x) for x in sequence(details, 2)]))
                elif status == "metadata_only":
                    desc, nu, nv, nw, nt, angle, spectral, cd = sequence(details, 8)
                    result["data"] = {**description(desc), "sizes": list(map(count, (nu, nv, nw, nt))), "native_angle_type": count(angle),
                                      "spectral": bool(spectral), "candela": bool(cd), "simulation_provenance": "unknown"}
                elif status != "unsupported":
                    raise ValueError("Unexpected optical inspection status")
                decoded.append(result)
            groups[kind] = {"unique_objects": total, "truncated": total > len(items), "objects": decoded}
        return {"groups": groups, "scope": "saved model snapshot; not current Studio", "profile": "LS2.0 non-spectral Lambert/PointLight and partial Medium"}, []
    if operation == "set_emitter_parameters":
        before, after, reread, original = sequence(rows, 4)
        before, after, reread = map(source, (before, after, reread))
        verify_edit(before, after, p["changes"], p["source_name"])
        if after != reread or emitter(original) != before["emitter"]:
            raise ValueError("Reload differs or original shared emitter changed")
        return {"before": before, "after": after, "reload_verified": True, "original_shared_emitter_unchanged": True,
                "modified_model": str(directory / "modified.lue"), "scope": "selected PointLight gets a separate emitter in a private copy",
                "geometry_roundtrip_certified": False, "keep_job_directory_with_dependencies": True}, []
    seed, rays, cases = sequence(rows, 3)
    if seed != p["seed"] or rays != p["rays"] or len(sequence(cases)) != len(p["cases"]):
        raise ValueError("Sweep coverage or simulation parameters differ")
    result, artifacts = [], []
    for index, row in enumerate(cases):
        ordinal, before, after, sensors = sequence(row, 4)
        if ordinal != index or not 1 <= len(sequence(sensors)) <= 64:
            raise ValueError("Sweep case or sensor coverage differs")
        before, after = source(before), source(after)
        verify_edit(before, after, p["cases"][index], p["source_name"])
        if result and before != result[0]["before"]:
            raise ValueError("Sweep cases did not start from the same source parameters")
        outputs = []
        for j, sensor in enumerate(sensors):
            name, filename, angle, nu, nv, flux, peak, u, v = sequence(sensor, 9)
            if filename != f"case_{index}_sensor_{j}.lid" or angle not in (0, 1, 2) or min(count(nu), count(nv)) < 1:
                raise ValueError("Invalid sweep sensor artifact")
            outputs.append({"name": string(name), "file": str(directory / filename), "angle_type": {0: "B", 1: "A", 2: "C"}[angle],
                            "grid": [nu, nv], "flux_in_domain_lm": number(flux), "peak_cd": number(peak),
                            "peak_u_deg": number(u), "peak_v_deg": number(v)})
            artifacts.append(filename)
        result.append({"case": index, "changes": p["cases"][index], "before": before, "after": after, "sensors": outputs})
    with (directory / "sweep.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream); writer.writerow(["case", "sensor_index", "flux_in_domain_lm", "peak_cd", "peak_u_deg", "peak_v_deg"])
        for trial in result:
            for j, sensor in enumerate(trial["sensors"]):
                writer.writerow([trial["case"], j, *[sensor[k] for k in ("flux_in_domain_lm", "peak_cd", "peak_u_deg", "peak_v_deg")]])
    return {"backend": "CPU", "seed": seed, "rays_per_case": rays, "case_count": len(result), "cases": result,
            "convergence_established": False, "regulatory_assessment": False, "summary_csv": str(directory / "sweep.csv")}, artifacts + ["sweep.csv"]
