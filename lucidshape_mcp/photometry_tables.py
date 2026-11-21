"""Fixed GB 4599-2024 engineering test tables for the native LS2.0 engine."""
import json
import math
from pathlib import Path

from .inputs import input_file, snapshot
from .results import number, sequence

TEMPLATES = Path(__file__).with_name("templates")
TABLES = {"gb4599_2024_lb": "LB", "gb4599_2024_hb": "LB+HB"}


def validate(parameters):
    if parameters.get("table") not in TABLES:
        raise ValueError("Select gb4599_2024_lb or gb4599_2024_hb")
    return {"table": parameters["table"],
            "lid_path": str(input_file(parameters["lid_path"], {".ies", ".lid"}))}


def rules(table):
    catalog = json.loads((TEMPLATES / "gb4599_2024.json").read_text(encoding="utf-8"))
    return [item for item in catalog["rules"] if item["beam"] == TABLES[table]]


def native_task(rule):
    """Construct native classes only from the packaged catalog."""
    operation = rule["operation"]
    xy = rule["positions_deg"]
    args = [json.dumps(rule["test_id"])]
    if operation == "point":
        kind = "Point"
        args.extend(map(str, xy[0]))
    elif operation == "sum":
        kind = "3Point"
        args.extend(str(v) for pair in xy for v in pair)
        args.append("1")
    elif operation in ("line_min", "line_max"):
        kind = "Line"
        args.extend(map(str, (xy[0][0], xy[1][0], xy[0][1], xy[1][1], 0.1)))
    elif operation == "polygon_max":
        kind = "Boundary"
        args.append("(" + ",".join(f"({u},{v})" for u, v in xy) + ")")
    elif operation == "domain_max":
        kind = "Max"
    elif operation == "line10_below_max":
        # Test only the part represented by this grid. The full lower extent
        # is intentionally left open in the overall verdict.
        return (f'tasks << new LidMeasureZoneRelativeToValueAt({args[0]},'
                f'{xy[0][0]},{xy[1][0]},y0,{xy[0][1]},1.72,-0.86,-1,'
                f'{rule["max_relative"]["factor"]});')
    else:
        raise ValueError(f"Unsupported native table operation: {operation}")
    low = str(rule.get("min_cd", -1))
    if "min_relative" in rule:
        assert rule["min_relative"]["test_id"] == "Imax"
        low = f"peak*{rule['min_relative']['factor']}"
    args.extend([low, str(rule.get("max_cd", -1))])
    return f"tasks << new LidMeasure{kind}({','.join(args)});"


def stage(directory, p):
    path = Path(p["lid_path"])
    name = "input" + path.suffix.lower()
    inputs = snapshot(path, directory, name)
    supported = rules(p["table"])
    h, v = zip(*(pair for rule in supported for pair in rule["positions_deg"]))
    tokens = {"IS_IES": int(name.endswith(".ies")), "INPUT": json.dumps(name),
              "TABLE": json.dumps(p["table"]), "TASKS": "\n".join(map(native_task, supported)),
              "XMIN": min(h), "XMAX": max(h), "YMIN": min(v), "YMAX": max(v)}
    script = (TEMPLATES / "measure_photometry_table.do").read_text(encoding="utf-8")
    for key, value in tokens.items():
        script = script.replace(f"@@{key}@@", str(value))
    (directory / "job.do").write_text(script, encoding="ascii")
    return inputs, ("engine-result.json", "native-measurements.txt")


def close(a, b):
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6)


def verify(directory, fields, table):
    selected, bounds, peak, rows = sequence(fields, 4)
    if selected != table:
        raise ValueError("Native test table identity mismatch")
    h0, h1, v0, v1 = [number(v) for v in sequence(bounds, 4)]
    peak_value, peak_h, peak_v = [number(v) for v in sequence(peak, 3)]
    if peak_value < 0 or not (h0 <= peak_h <= h1 and v0 <= peak_v <= v1):
        raise ValueError("Invalid sampled native peak or coverage")
    expected = rules(table)
    rows = sequence(rows, len(expected))
    results = {}
    for rule, raw in zip(expected, rows):
        name, value, low, high, nok, positions, extreme = sequence(raw, 7)
        if name != rule["test_id"] or nok not in (0, 1) or isinstance(nok, bool):
            raise ValueError("Invalid native measurement identity or verdict")
        value, low, high = map(number, (value, low, high))
        minimum = rule.get("min_cd", -1)
        if "min_relative" in rule:
            minimum = peak_value * rule["min_relative"]["factor"]
        maximum = rule.get("max_cd", -1)
        partial = rule["operation"] == "line10_below_max"
        if partial:
            reference = rule["max_relative"]
            maximum = results[reference["test_id"]]["actual_cd"] * reference["factor"]
        if value < 0 or not close(low, minimum) or not close(high, maximum):
            raise ValueError(f"{name}: invalid measurement or rule limits")
        failed = (low >= 0 and value < low) or (high >= 0 and value > high)
        if bool(nok) != failed:
            raise ValueError(f"{name}: limits disagree with native verdict")
        pairs = [[number(u), number(v)] for u, v in sequence(positions)]
        found = [number(v) for v in sequence(extreme)]
        if len(found) not in (0, 2):
            raise ValueError("Invalid native extremum")
        coords = rule["positions_deg"]
        if rule["operation"] in ("line_min", "line_max", "polygon_max", "line10_below_max") and len(found) != 2:
            raise ValueError("Native extreme position missing")
        if partial:
            coords = [[coords[0][0], v0], coords[1]]
            if not (coords[0][0] <= found[0] <= coords[1][0] and v0 <= found[1] <= coords[1][1]):
                raise ValueError("Native extreme outside the covered part of the Line 10 region")
        if rule["operation"] == "domain_max":
            coords = [[peak_h, peak_v]]
            if not close(value, peak_value):
                raise ValueError("Maximum disagrees with the native input peak")
        if rule["operation"] != "polygon_max" and (len(pairs) != len(coords) or
                any(not close(a, b) for p, q in zip(pairs, coords) for a, b in zip(p, q))):
            raise ValueError(f"{name}: native test coordinates disagree with the table")
        if any(not (h0 <= u <= h1 and v0 <= v <= v1) for u, v in coords + ([found] if found else [])):
            raise ValueError(f"{name}: outside the sampled grid")
        results[name] = {**rule, "actual_cd": value, "evaluated_min_cd": None if low < 0 else low,
                         "evaluated_max_cd": None if high < 0 else high, "native_found_deg": found or None,
                         "status": "fail" if failed else "pass"}
        if partial:
            results[name].update(sampled_rectangle_deg=coords, sampled_status=results[name]["status"],
                                 reason="Full lower angular extent not established by this finite grid.")
            if not failed:
                results[name]["status"] = "not_evaluated"
    return {"table": table, "beam": TABLES[table], "rows": list(results.values()), "sample_bounds_deg": bounds,
            "source_peak_cd_h_v": peak, "scope": "Named photometric requirements on the supplied module grid at its existing aim",
            "standard": "GB 4599-2024", "clause": "8.2.2 Table 1 A" if table.endswith("_lb") else "8.3.2 Table 5",
            "native_api": "LidMeasureTable.measure", "native_text": str(directory / "native-measurements.txt"),
            "aim_shift_deg": [0, 0], "scale": 1, "regulatory_certification": False}, []
