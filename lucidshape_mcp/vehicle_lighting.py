"""Fixed private vehicle-lighting worker and source-preserving JSON/IES staging."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import subprocess
import sys
import zipfile


OPERATION = "evaluate_vehicle_lighting"
OUTPUTS = ("lighting.json", "lighting.xlsx", "receipt.json")
MAX_CONFIG = 4 * 1024 * 1024
MAX_CONFIG_FILE = 64 * 1024 * 1024
MAX_DATA = 256 * 1024 * 1024
TOP = {"project", "has_adb", "standards", "photometry", "measurements", "evidence"}
SCENES = {f"{kind}_{distance}" for kind in ("oncoming", "following") for distance in (50, 100, 200)}


def entrypoint():
    configured = os.environ.get("LUCIDSHAPE_LIGHTING_RUNTIME")
    runtime = Path(configured) if configured else Path(__file__).resolve().parents[1] / "local-data" / "VehicleLighting-Windows"
    if not runtime.is_absolute():
        raise ValueError("LUCIDSHAPE_LIGHTING_RUNTIME must be an absolute directory")
    script = runtime / "vehicle_lighting.py"
    if not script.is_file():
        raise FileNotFoundError("Install the private lighting runtime or set LUCIDSHAPE_LIGHTING_RUNTIME")
    return script.resolve(strict=True)


def command(script, *args):
    return (sys.executable, "-I", "-B", str(script), *args)


async def catalog(standard_id, has_adb):
    if standard_id is not None and (not isinstance(standard_id, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", standard_id)):
        raise ValueError("standard_id must be a catalog identifier")
    if type(has_adb) is not bool:
        raise ValueError("has_adb must be a boolean")
    script = entrypoint()
    args = ["--catalog", *([standard_id] if standard_id else []), *(["--adb"] if has_adb else [])]
    try:
        result = await asyncio.to_thread(subprocess.run, command(script, *args), cwd=script.parent,
                                         stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Lighting catalog timed out") from exc
    if result.returncode:
        raise ValueError("Catalog lookup failed: " + result.stderr[-800:].decode("utf-8", errors="replace"))
    if len(result.stdout) > MAX_CONFIG:
        raise ValueError("Lighting catalog reply exceeds its size limit")
    reply = json.loads(result.stdout)
    if not isinstance(reply, dict) or not isinstance(reply.get("standards"), list):
        raise ValueError("Invalid lighting catalog reply")
    return reply


def _read(path, maximum):
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError(f"{path.name} exceeds the lighting input/output size limit")
    return data


def _json(data):
    def pairs(entries):
        obj = {}
        for name, value in entries:
            if name in obj:
                raise ValueError("Duplicate JSON field: " + name)
            obj[name] = value
        return obj
    def invalid(constant):
        raise ValueError("Nonfinite JSON constant: " + constant)
    try:
        return json.loads(data.decode("utf-8-sig"), object_pairs_hook=pairs, parse_constant=invalid)
    except RecursionError as exc:
        raise ValueError("Lighting project exceeds JSON nesting limit") from exc


def local_path(value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise ValueError("Expected a local filesystem path")
    path = PureWindowsPath(value)
    if (value.replace("/", "\\").startswith("\\\\") or ":" in str(path)[len(path.drive):]
            or path.is_reserved() or (path.drive and not path.root)):
        raise ValueError("Expected a local file, without device, share or stream syntax")
    return Path(value)


def _checked(value, policy, base, suffix):
    path = local_path(value)
    if not path.is_absolute():
        if base is None:
            raise ValueError("Inline lighting project inputs must be absolute local paths")
        path = base / path
    path = policy.check_path(path)
    local_path(str(path))
    if not path.is_file() or path.suffix.lower() != suffix:
        raise ValueError("Expected an existing " + suffix + " file")
    return path


def _dict(data, context, allowed=None):
    if not isinstance(data, dict):
        raise ValueError(context + " must be an object")
    if allowed is not None and not data.keys() <= allowed:
        raise ValueError("Unknown field in " + context)
    return data


def _lamp_specs(project):
    optical_keys = {"projection", "projection_1_5m", "low_beam", "high_beam", "adb_scenarios",
                    "step_m", "max_distance_m", "half_width_m"}
    optics = _dict(project.get("photometry", {}), "photometry", optical_keys)
    scenes = _dict(optics.get("adb_scenarios", {}), "photometry.adb_scenarios", SCENES)
    beams = [(k, optics[k]) for k in ("low_beam", "high_beam") if k in optics]
    for key, pair in [*beams, *scenes.items()]:
        sides = _dict(pair, key, {"left", "right"})
        if set(sides) != {"left", "right"}:
            raise ValueError(key + " requires left and right lamp inputs")
        for side, data in sides.items():
            spec = _dict(data, f"{key}.{side}", {"ies", "x_m", "y_m", "z_m", "yaw_deg", "pitch_deg"})
            if "ies" not in spec:
                raise ValueError(f"{key}.{side}.ies is required")
            yield spec


def stage(directory, policy, project, config_path):
    """Only stage the inputs supported by the version 1 private worker protocol."""
    if (project is None) == (config_path is None):
        raise ValueError("Supply exactly one of project and config_path")
    base = None
    manifest = []
    if config_path is not None:
        source = _checked(config_path, policy, None, ".json")
        base = source.parent
        data = _read(source, MAX_CONFIG_FILE)
        (directory / "source-project.json").write_bytes(data)
        manifest.append({"source": str(source), "staged_name": "source-project.json", "source_bytes": len(data),
                         "source_sha256": hashlib.sha256(data).hexdigest()})
    else:
        try:
            data = json.dumps(project, ensure_ascii=True, allow_nan=False).encode("ascii")
        except (TypeError, RecursionError) as exc:
            raise ValueError("Invalid lighting project JSON") from exc
        if len(data) > MAX_CONFIG:
            raise ValueError("Lighting project exceeds the input size limit")
    request = _dict(_json(data), "lighting project", TOP)
    if "standards" in request:
        for settings in _dict(request["standards"], "standards").values():
            if "photometry" in _dict(settings, "standard input"):
                raise ValueError("Place photometry in the project root")
    cached = {}
    remaining = MAX_DATA
    for spec in _lamp_specs(request):
        path = _checked(spec["ies"], policy, base, ".ies")
        if path not in cached:
            name = f"lamp-{len(cached) + 1:02d}.ies"
            data = _read(path, remaining)
            remaining -= len(data)
            (directory / name).write_bytes(data)
            manifest.append({"source": str(path), "staged_name": name, "source_bytes": len(data),
                             "source_sha256": hashlib.sha256(data).hexdigest()})
            cached[path] = name
        spec["ies"] = cached[path]
    (directory / "request.json").write_text(json.dumps(request, ensure_ascii=True, allow_nan=False), encoding="ascii")
    return manifest


def finalize(directory, *, failed=False):
    receipt = _dict(_json(_read(directory / "receipt.json", MAX_CONFIG)), "lighting receipt")
    statuses = {"error"} if failed else {"partial", "complete"}
    if (receipt.get("schema_version") != "vehicle-lighting/1" or
            receipt.get("evaluation_status") not in statuses or
            not isinstance(receipt.get("summary"), dict) or not receipt["summary"]):
        raise ValueError("Invalid lighting worker receipt")
    if failed and not all(isinstance(s, dict) and s.get("status") == "error" and
                          isinstance(s.get("error"), str) and s["error"].strip() for s in receipt["summary"].values()):
        raise ValueError("Failed lighting receipt lacks per-standard error details")
    hashes = receipt.get("artifact_sha256")
    if not isinstance(hashes, dict) or set(hashes) != {"lighting.json", "lighting.xlsx"}:
        raise ValueError("Lighting receipt must contain both artifact hashes")
    for name, expected in hashes.items():
        with (directory / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError("Lighting artifact hash mismatch: " + name)
    with zipfile.ZipFile(directory / "lighting.xlsx") as workbook:
        if "xl/workbook.xml" not in workbook.namelist():
            raise ValueError("Missing lighting workbook")
    return receipt, ()


def failure_message(directory):
    """The private CLI emits one UTF-8 JSON error line for global validation failures."""
    with (directory / "engine.log").open("rb") as stream:
        stream.seek(max(0, stream.seek(0, 2) - 16384))
        lines = stream.read().splitlines()
    for line in reversed(lines):
        try:
            message = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(message, dict) and isinstance(message.get("error"), str):
            return message["error"][:3500]
    return "Lighting worker exited before producing a verified evaluation; inspect engine.log"
