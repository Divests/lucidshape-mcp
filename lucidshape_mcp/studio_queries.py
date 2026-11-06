"""Fixed, read-only queries against an existing Studio DDE session.

Native help is discovery evidence, not a complete signature or permission to
invoke arbitrary members. Every query writes a versioned, correlated receipt.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

from .inputs import decode_text
from .live_studio import run_dde, validate_pid
from .results import count, number, sequence, string

TEMPLATES = Path(__file__).with_name("templates")
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
OBJECT_TYPES = {"Shape", "Surface", "Geometry", "EmitterMaterial", "SensorMaterial", "ActorMaterial"}
PARAMETERS = {
    "monte_carlo": ("MONTE_CARLO", {
        "ray_count": "NUM_RAYS", "repeat_count": "REPEAT_COUNT",
        "intensity_level": "INTENSITY_LEVEL", "recursion_level": "RECURSION_LEVEL",
        "random_seed": "RANDOM_SEED", "global_scale": "GLOBAL_SCALE",
        "more_rays": "MORE_RAYS", "intersection_mode": "ASAP", "spectral": "SPECTRAL",
        "channel_count": "NUM_CHANNELS", "last_traced_ray_count": "NUMER_OF_LAST_TRACED_RAYS",
        "start_ray_index": "START_RAY_INDEX", "radiometric": "RADIOMETRIC",
        "randomized_ray_selection": "RANDOMIZED_RAY_SELECTION",
        "total_ray_count": "TOTAL_NUMBER_OF_RAYS",
        "fixed_fresnel_reflections": "FIXED_NUMBER_OF_FRRESNEL_REFLECTIONS",
    }),
    "light_mapping": ("LIGHT_MAPPING", {
        "with_default_material": "WITH_DEFAULT_MATERIAL",
        "split_result_sensors": "SPLIT_THE_RESULT_SENSORS", "ray_count": "NUM_RAYS_LM",
        "surface_cell_size": "SURFACE_CELL_SIZE", "mapping_2d": "MAPPING_2D",
        "use_all_actors_as_absorbers": "USE_ALL_ACTORS_AS_ABSORBER_TOO",
    }),
    "multiprocessing": ("MULTI_PROCESSING", {
        "run_immediately": "RUN_IMMEDIATELY", "enabled": "MULTIPROC",
        "local_processor_count": "NUMBER_OF_LOCAL_PROCESSORS", "priority": "PROCESSOR_PRIORITY",
    }),
}


def bounded_int(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")
    return value


def read_text(path: Path, maximum=MAX_RECEIPT_BYTES):
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError(f"Native query output is too large: {path.name}")
    return decode_text(data)[0]


def flag(value):
    if isinstance(value, bool) or value not in (0, 1):
        raise ValueError("Invalid native query flag")
    return bool(value)


def receipt(path, nonce, action):
    data = json.loads(read_text(path))
    if not isinstance(data, dict) or set(data) != {"List"}:
        raise ValueError("Unexpected native query envelope")
    fields = data["List"]
    if not isinstance(fields, dict) or set(fields) != {f"p{i}" for i in range(10)}:
        raise ValueError("Unexpected native query fields")
    token, version, operation, present, name, models, busy, payload, same, done = [fields[f"p{i}"] for i in range(10)]
    if token != nonce or version != 1 or isinstance(version, bool) or operation != action or done != "completed":
        raise ValueError("Native query receipt does not match this request and schema")
    present, busy, same = flag(present), flag(busy), flag(same)
    name, models = string(name), count(models)
    if (not present and name) or (present and models == 0) or not same:
        raise ValueError("Current model changed or native model identity is inconsistent")
    return {"has_current_model": present, "model_name": name, "open_model_count": models,
            "simulation_running": busy, "model_still_current": same}, sequence(payload)


def common_script():
    # JSONStore in LS2.0 writes string contents without JSON escaping. Escape
    # once in the native script; Python's JSON decoder restores the exact name.
    slash = "\\"
    replacements = [(slash * 2, slash * 4), ('"', slash * 2 + '"')]
    replacements += [(rf"\x{i:02x}", slash * 2 + f"u{i:04x}") for i in range(32)]
    lines = ["#pragma NOPP", "string mcpJsonString(string s){", "string t;"]
    for pattern, replacement in replacements:
        lines.append(f"regex_replace(s,{json.dumps(pattern)},{json.dumps(replacement)},t); s=t;")
    lines += ["return s;", "}", "object mcpDescribe(object item){",
              'if(!item) return (0,"","");',
              "return (1,mcpJsonString(item.getName()),mcpJsonString(item.nameOfClass()));", "}"]
    return "\n".join(lines) + "\n"


def build_script(directory, nonce, action, body):
    return common_script() + r'''
object mcpNull;
object mcpApp=getApplication();
if(!mcpApp) return;
object mcpModel=mcpApp.getModel($,1);
object mcpModels=mcpApp.getAllModels();
double mcpHasModel=0,mcpRunning=mcpApp.isRunningSimulation();
string mcpName="";
if(mcpModel){mcpHasModel=1; mcpName=mcpJsonString(mcpModel.getName());}
object mcpPayload=();
if(!mcpRunning){
@@BODY@@
}
object mcpAfter=mcpApp.getModel($,1);
double mcpSame=0;
if(mcpModel){if(mcpAfter) mcpSame=mcpModel.isSameObject(mcpAfter);}
else {if(!mcpAfter) mcpSame=1;}
object mcpOut=JSONStore(@@PATH@@,0,1);
mcpOut << (@@NONCE@@,1,@@ACTION@@,mcpHasModel,mcpName,mcpModels.size(),mcpRunning,mcpPayload,mcpSame,"completed");
mcpOut=mcpNull;
'''.replace("@@BODY@@", body).replace("@@PATH@@", json.dumps((directory / "query-receipt.json").as_posix())).replace("@@NONCE@@", json.dumps(nonce)).replace("@@ACTION@@", json.dumps(action))


async def query(root, action, make_body, parse_payload, *, process_id=None,
                expected_model_name=None, timeout_seconds=30, transport=None, needs_model=True):
    validate_pid(process_id)
    bounded_int(timeout_seconds, 5, 120, "timeout_seconds")
    if expected_model_name is not None and (not isinstance(expected_model_name, str) or not expected_model_name or len(expected_model_name) > 4096):
        raise ValueError("expected_model_name must be a nonempty string of at most 4096 characters")
    directory = root.resolve() / ("studio-query-" + action + "-" + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    transport = transport or run_dde
    phase = "connection"
    try:
        state = await transport(directory / "connection", {"action": "probe"}, 30)
        if not state.get("connected"):
            raise RuntimeError("Existing Studio DDE is unavailable; no application was started and no query was sent")
        pid = state["process_id"]
        validate_pid(pid)
        if pid is None or (process_id is not None and process_id != pid):
            raise RuntimeError("DDE partner differs from the selected Studio process")
        executable = Path(state["executable"])
        identity = {"executable": str(executable), "sha256": hashlib.sha256(executable.read_bytes()).hexdigest()}
        nonce = uuid.uuid4().hex
        body, context = make_body(directory, executable)
        (directory / "query-request.json").write_text(json.dumps({"nonce": nonce, "action": action,
            "process_id": pid, "engine_identity": identity, "expected_model_name": expected_model_name,
            "context": context}, indent=2), encoding="utf-8")
        (directory / "query.do").write_text(build_script(directory, nonce, action, body), encoding="ascii")
        phase = "execution"
        deadline = time.monotonic() + timeout_seconds
        reply = await transport(directory, {"action": "execute_file", "script_name": "query.do",
            "receipt_name": "query-receipt.json", "process_id": pid, "timeout_ms": timeout_seconds * 1000}, timeout_seconds + 5)
        if reply.get("process_id") != pid or not reply.get("acknowledged"):
            raise RuntimeError("Studio did not acknowledge this query from the pinned process")
        while True:
            try:
                model, payload = receipt(directory / "query-receipt.json", nonce, action)
                break
            except (FileNotFoundError, json.JSONDecodeError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Query acknowledged but its complete native receipt is missing")
                await asyncio.sleep(0.1)
        if expected_model_name is not None and model["model_name"].replace("\\", "/") != expected_model_name.replace("\\", "/"):
            raise RuntimeError("Current model does not match expected_model_name")
        unavailable = "simulation_running" if model["simulation_running"] else (
            "no_current_model" if needs_model and not model["has_current_model"] else None)
        if unavailable and payload:
            raise ValueError("Unexpected payload for an unavailable query")
        parsed = {"status": "unavailable", "reason": unavailable} if unavailable else parse_payload(payload, directory, context)
        result = {**model, **parsed, "query": action, "process_id": pid, "engine_identity": identity,
            "read_only": True, "new_studio_process_started": False, "computer_use_calls": 0,
            "persistent_dde_session": reply.get("persistent_dde_session", False),
            "helper_process_id": reply.get("helper_process_id"), "backend": "existing Studio DDE / LucidShell",
            "record_directory": str(directory), "native_receipt_file": str(directory / "query-receipt.json")}
        (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    except asyncio.CancelledError:
        (directory / "result.json").write_text(json.dumps({"status": "interrupted", "phase": phase,
            "automatic_resubmission_allowed": False, "record_directory": str(directory)}, indent=2), encoding="utf-8")
        raise
    except Exception as exc:
        (directory / "result.json").write_text(json.dumps({"status": "unverified", "phase": phase,
            "error": f"{type(exc).__name__}: {exc}", "automatic_resubmission_allowed": False,
            "record_directory": str(directory)}, indent=2), encoding="utf-8")
        raise RuntimeError(f"{exc}; query unverified, not resubmitted; records: {directory}") from exc


def parse_catalog(payload, directory, context):
    raw = sequence(sequence(payload, 1)[0])
    if not raw or len(raw) > 20000:
        raise ValueError("Empty or excessive native API catalog")
    entries, groups, group = [], [], ""
    for value in raw:
        value = string(value)
        if value.startswith("/"):
            group = value[1:]
            if group not in groups:
                groups.append(group)
        else:
            name = value[1:] if value.startswith("-") else value
            if not name:
                raise ValueError("Empty catalog entry")
            entries.append({"name": name, "group": group, "native_entry": value})
    matched = [e for e in entries if context["filter_text"].casefold() in (e["group"] + " " + e["name"]).casefold()]
    offset, limit = context["offset"], context["limit"]
    page = matched[offset:offset + limit]
    docs = None
    if context["type_name"]:
        methods = read_text(directory / "type-methods.txt", 512 * 1024)
        description = read_text(directory / "type-description.txt", 512 * 1024)
        parsed = [{"return_type": m[0], "name": m[1], "parameters_text": m[2],
                   "signature_complete": "..." not in m[2]} for m in re.findall(
                       r"^\s*(double|object|string|void)\s+o\.(\w+)\(([^\r\n]*)\);", methods, re.M)]
        maximum = context["max_documentation_chars"]
        docs = {"type_name": context["type_name"], "status": "available" if methods.strip() or description.strip() else "unavailable",
                "methods": parsed[:500], "total_methods": len(parsed),
                "methods_text": methods[:maximum], "description_text": description[:maximum],
                "truncated": len(methods) > maximum or len(description) > maximum or len(parsed) > 500,
                "scope": "Native help for this name; inherited members and complete parameter signatures may be absent"}
    return {"status": "succeeded", "total_entries": len(entries), "matching_entries": len(matched),
            "groups": groups, "offset": offset, "entries": page,
            "next_offset": offset + len(page) if offset + len(page) < len(matched) else None,
            "documentation": docs, "scope": "Runtime command/type help inventory, not a complete callable API schema"}


async def get_api_catalog(root, *, process_id=None, type_name=None, filter_text="", offset=0, limit=200,
                          max_documentation_chars=12000, timeout_seconds=30, transport=None):
    if type_name is not None and (not isinstance(type_name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", type_name)):
        raise ValueError("type_name must be a simple native identifier")
    if not isinstance(filter_text, str) or len(filter_text) > 256:
        raise ValueError("filter_text must be a string of at most 256 characters")
    bounded_int(offset, 0, 20000, "offset")
    bounded_int(limit, 1, 1000, "limit")
    bounded_int(max_documentation_chars, 100, 50000, "max_documentation_chars")
    def make(directory, executable):
        body = '''object mcpEntries=();
listObjectsWithType(5,$,$,mcpEntries);
object mcpSafeEntries=();
for(double mcpI=0;mcpI<mcpEntries.size();mcpI++){
    string mcpEntry=mcpEntries[mcpI];
    mcpSafeEntries << mcpJsonString(mcpEntry);
}
mcpPayload << mcpSafeEntries;
'''
        if type_name:
            for mode, filename in ((1, "type-methods.txt"), (3, "type-description.txt")):
                body += f"listObjectsWithType({mode},{json.dumps((directory / filename).as_posix())},{json.dumps(type_name)});\n"
        return body, dict(type_name=type_name, filter_text=filter_text, offset=offset, limit=limit,
                          max_documentation_chars=max_documentation_chars)
    return await query(root, "api_catalog", make, parse_catalog, process_id=process_id,
                       timeout_seconds=timeout_seconds, transport=transport, needs_model=False)


def descriptor(value):
    exists, name, kind = sequence(value, 3)
    exists, name, kind = flag(exists), string(name), string(kind)
    if not exists and (name or kind) or exists and not kind:
        raise ValueError("Inconsistent object descriptor")
    return {"name": name, "type": kind} if exists else None


def parse_objects(payload, directory, context):
    total, rows = sequence(payload, 2)
    total, rows = count(total), sequence(rows)
    expected = min(context["limit"], max(0, total - context["offset"]))
    if len(rows) != expected:
        raise ValueError("Native object page count differs")
    objects = []
    for index, row in enumerate(rows, context["offset"]):
        position, item, edges = sequence(row, 3)
        if count(position) != index or descriptor(item) is None:
            raise ValueError("Invalid object page position or null object")
        relationships = {}
        for edge in sequence(edges, 3):
            label, queried, target = sequence(edge, 3)
            if label not in ("material", "exit_material", "surface") or label in relationships:
                raise ValueError("Invalid object relationship")
            supported, target = flag(queried), descriptor(target)
            if not supported and target is not None:
                raise ValueError("Unexpected unqueried relationship")
            relationships[label] = {"status": "available" if target else ("not_assigned" if supported else "not_queried"), "target": target}
        objects.append({"index": index, **descriptor(item), "relationships": relationships})
    return {"status": "succeeded", "object_type": context["object_type"], "selected_only": context["selected_only"],
            "total_references": total, "offset": context["offset"], "objects": objects,
            "next_offset": context["offset"] + len(rows) if context["offset"] + len(rows) < total else None,
            "count_scope": "Model.getObjects filtered references; not unique CAD faces or persistent object handles",
            "relationship_scope": "Direct getters for supported concrete classes only; names/types are not unique IDs or optical validation"}


async def inspect_objects(root, *, process_id=None, expected_model_name=None, object_type=None,
                          selected_only=False, offset=0, limit=100, timeout_seconds=30, transport=None):
    if object_type is not None and (not isinstance(object_type, str) or object_type not in OBJECT_TYPES):
        raise ValueError("object_type must be null or one of " + ", ".join(sorted(OBJECT_TYPES)))
    if not isinstance(selected_only, bool):
        raise ValueError("selected_only must be boolean")
    bounded_int(offset, 0, 10000000, "offset")
    bounded_int(limit, 1, 500, "limit")
    def make(directory, executable):
        body = (TEMPLATES / "studio_inspect_objects.do").read_text(encoding="ascii")
        for key, value in {"SELECTED": str(int(selected_only)), "TYPES": "()" if object_type is None else "(" + json.dumps(object_type) + ")",
                           "OFFSET": str(offset), "LIMIT": str(limit)}.items():
            body = body.replace("@@" + key + "@@", value)
        return body, dict(object_type=object_type, selected_only=selected_only, offset=offset, limit=limit)
    return await query(root, "objects", make, parse_objects, process_id=process_id,
                       expected_model_name=expected_model_name, timeout_seconds=timeout_seconds, transport=transport)


def parameter_mapping(executable):
    path = executable.parent.parent / "include" / "tasks" / "raytracemapping.hdo"
    data = path.read_bytes()
    if len(data) > 128 * 1024:
        raise ValueError("Simulation parameter mapping is too large")
    pairs = re.findall(rb"^[ \t]*#define[ \t]+(\w+)[ \t]+(\d+)[ \t]*(?://[^\r\n]*)?\r?$", data, re.M)
    values = {name.decode("ascii"): int(value) for name, value in pairs}
    required = {name for block, fields in PARAMETERS.values() for name in (block, *fields.values())}
    if any(sum(key.decode("ascii") == name for key, _ in pairs) != 1 for name in required):
        raise ValueError("Installed simulation parameter definitions are missing or ambiguous")
    if not required <= values.keys() or any(not 0 <= values[name] < 64 for name in required):
        raise ValueError("Installed simulation parameter mapping is incomplete or unsupported")
    if len({values[block] for block, fields in PARAMETERS.values()}) != len(PARAMETERS):
        raise ValueError("Ambiguous simulation parameter group mapping")
    for block, fields in PARAMETERS.values():
        if len({values[name] for name in fields.values()}) != len(fields):
            raise ValueError("Ambiguous simulation parameter field mapping")
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "definitions": {name: values[name] for name in sorted(required)}}


def parse_settings(payload, directory, context):
    available, rows = sequence(payload, 2)
    if not flag(available):
        if sequence(rows):
            raise ValueError("Unexpected unavailable simulation parameters")
        return {"status": "unavailable", "reason": "native_parameters_unavailable", "source_mapping": context}
    groups = {}
    mapping = context["definitions"]
    for row, (label, (block, fields)) in zip(sequence(rows, len(PARAMETERS)), PARAMETERS.items()):
        actual, block_index, values = sequence(row, 3)
        if actual != label or count(block_index) != mapping[block]:
            raise ValueError("Simulation parameter group differs from mapping")
        decoded = {}
        for cell, (name, macro) in zip(sequence(values, len(fields)), fields.items()):
            key, index, value = sequence(cell, 3)
            if key != name or count(index) != mapping[macro]:
                raise ValueError("Simulation parameter field differs from mapping")
            decoded[name] = number(value)
            if name in {"ray_count", "repeat_count", "recursion_level", "channel_count", "last_traced_ray_count",
                        "start_ray_index", "total_ray_count", "local_processor_count", "fixed_fresnel_reflections"}:
                decoded[name] = count(value)
        groups[label] = decoded
    return {"status": "succeeded", "settings": groups, "raw_numeric_groups": rows, "source_mapping": context,
            "units": {"ray_count": "individual rays; no GUI thousands conversion applied", "counts": "dimensionless",
                      "surface_cell_size": "native model units"},
            "scope": "Numeric Monte Carlo, light mapping and multiprocessing settings; wavelength arrays, filenames and other groups are omitted",
            "simulation_run": False, "settings_modified": False}


def simulation_settings_body(mapping):
    values = mapping["definitions"]
    lines = ['if(mcpHasModel){', 'double mcpAvailable=0;', 'object mcpRows=();',
             'object mcpRaw=mcpModel.getMonteCarloRayTraceParameter();',
             'if(mcpRaw){if(mcpRaw.size()>0){', 'object mcpGroups=mcpRaw[0];', 'if(mcpGroups){',
             f'if(mcpGroups.size()>{max(values[b] for b, _ in PARAMETERS.values())}){{', 'mcpAvailable=1;']
    for label, (block, fields) in PARAMETERS.items():
        lines += [f'object mcp_{label}=mcpGroups[{values[block]}];',
                  f'if(!mcp_{label}) mcpAvailable=0;',
                  f'else if(mcp_{label}.size()<={max(values[n] for n in fields.values())}) mcpAvailable=0;']
    lines.append('if(mcpAvailable){')
    for label, (block, fields) in PARAMETERS.items():
        lines.append(f'object mcp_{label}_values=();')
        for name, macro in fields.items():
            lines.append(f'mcp_{label}_values << ("{name}",{values[macro]},mcp_{label}[{values[macro]}]);')
        lines.append(f'mcpRows << ("{label}",{values[block]},mcp_{label}_values);')
    lines += ['}', '}', '}}}', 'mcpPayload << mcpAvailable << mcpRows;', '}']
    return "\n".join(lines)


async def get_simulation_settings(root, *, process_id=None, expected_model_name=None, timeout_seconds=30, transport=None):
    def make(directory, executable):
        mapping = parameter_mapping(executable)
        return simulation_settings_body(mapping), mapping
    return await query(root, "simulation_settings", make, parse_settings, process_id=process_id,
                       expected_model_name=expected_model_name, timeout_seconds=timeout_seconds, transport=transport)
