"""Apply documented UV menu operations to a job-local copy of photometry."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .inputs import input_file, snapshot
from .results import count, number, read_report, sequence


OPERATION = "photometry_apply_ui_action"
TEMPLATES = Path(__file__).with_name("templates")
# Keys come from include/tasks/menuUVView.hdo. Values are native LightData methods.
NATIVE_METHODS = {
    "CM_UV_FILTER": "filter",
    "CM_UV_MAP_FILTER": "filterMAP",
    "CM_UV_SCALE": "scaleByFactor",
    "CM_UV_MIRROR_IN_U": "mirrorInU",
    "CM_UV_MIRROR_IN_V": "mirrorInV",
    "CM_UV_SWAP_UV": "swapUV",
    "CM_UV_SYMMETRY_U": "symmetryU",
    "CM_UV_SYMMETRY_V": "symmetryV",
    "CM_UV_SYMMETRY_UV": "symmetryUV",
    "CM_ABSOLUTE": "absolute",
    "CM_UV_NEGATIVE_TO_ZERO": "setNegativeToZero",
}


def validate(arguments):
    if set(arguments) - {"data_path", "command", "passes", "factor"}:
        raise ValueError("Unknown photometry action arguments")
    command = arguments.get("command")
    if not isinstance(command, str) or command not in NATIVE_METHODS:
        raise ValueError("Unsupported photometry command")
    passes = arguments.get("passes", 1)
    if type(passes) is not int or not 1 <= passes <= 20:
        raise ValueError("passes must be an integer in [1, 20]")
    if command not in {"CM_UV_FILTER", "CM_UV_MAP_FILTER"} and passes != 1:
        raise ValueError("Only smoothing commands accept multiple passes")
    factor = arguments.get("factor")
    if command == "CM_UV_SCALE":
        if type(factor) not in {float, int} or not 0 <= factor <= 1e6 or not math.isfinite(factor):
            raise ValueError("CM_UV_SCALE needs a finite factor in [0, 1000000]")
    elif factor is not None:
        raise ValueError("factor applies only to CM_UV_SCALE")
    path = input_file(arguments.get("data_path"), {".ies", ".lid"})
    return {"command": command, "data_path": str(path), "passes": passes, "factor": factor}


def stage(directory, p):
    path = Path(p["data_path"])
    name = "source" + path.suffix.lower()
    inputs = snapshot(path, directory, name)
    command = p["command"]
    method = NATIVE_METHODS[command]
    if command == "CM_UV_SCALE":
        action = f"if (!lid.scaleByFactor({p['factor']:.17g})) {{cout << \"LUCIDSHAPE_MCP_FAILED: scale failed\" << endl;return;}}"
    elif command in {"CM_UV_FILTER", "CM_UV_MAP_FILTER"}:
        # Standard-mode defaults in the installed tasks/uvDataFilterMAPDialog.do.
        arguments = "0.005,0.1,0.1,1.1,1.1,2" if command == "CM_UV_MAP_FILTER" else ""
        action = f"for (double pass=0;pass<{p['passes']};pass++) lid.{method}({arguments});"
    else:
        action = f"lid.{method}();"
    script = (TEMPLATES / "photometry_apply_ui_action.do").read_text(encoding="ascii")
    script = script.replace("@@SOURCE_IS_IES@@", str(int(path.suffix.lower() == ".ies")))
    script = script.replace("@@NATIVE_ACTION@@", action)
    script = script.replace("@@WRITE_CHECKED_IES@@", (TEMPLATES / "write_checked_ies.do").read_text(encoding="ascii"))
    (directory / "job.do").write_text(script, encoding="ascii")
    return inputs, ("engine-result.json", "action-result.json", "photometry.ies")


def _metrics(row):
    nu, nv, peak, flux = sequence(row, 4)
    return {"grid_uv": [count(nu), count(nv)], "peak_cd": number(peak),
            "flux_in_data_domain_lm": number(flux)}


def verify(directory, p):
    result, extras = read_report(directory, "export_ies", {"lid_path": p["data_path"]})
    result.pop("source_lid")
    receipt = directory / "action-result.json"
    if receipt.stat().st_size > 4096:
        raise ValueError("Native action receipt exceeds size limit")
    fields = json.loads(receipt.read_text(encoding="utf-8-sig"))["List"]
    if set(fields) != {"p0", "p1"}:
        raise ValueError("Missing photometry action metrics")
    before, after = (_metrics(fields[key]) for key in ("p0", "p1"))
    if after["grid_uv"] != result["source_grid_uv"]:
        raise ValueError("Action output grid differs from the verified export")
    expected_grid = before["grid_uv"][::-1] if p["command"] == "CM_UV_SWAP_UV" else before["grid_uv"]
    if after["grid_uv"] != expected_grid:
        raise ValueError("Unexpected native action dimensions")
    if not math.isclose(after["peak_cd"], result["file_inspection"]["peak_cd"], rel_tol=6e-6, abs_tol=1e-5):
        raise ValueError("Action output peak differs from the verified export")
    if p["command"] == "CM_UV_SCALE":
        for key in ("peak_cd", "flux_in_data_domain_lm"):
            if not math.isclose(before[key] * p["factor"], after[key], rel_tol=6e-6, abs_tol=1e-5):
                raise ValueError("Native data failed to scale by the requested factor")
    result.update(command=p["command"], native_method=NATIVE_METHODS[p["command"]],
                  passes=p["passes"], factor=p["factor"], source_data=p["data_path"],
                  before=before, after=after, target="isolated_file_copy")
    if p["command"] == "CM_UV_MAP_FILTER":
        result["filterMAP_parameters"] = [0.005, 0.1, 0.1, 1.1, 1.1, 2]
    return result, extras
