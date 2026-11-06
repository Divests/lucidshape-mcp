"""A subprocess fixture, NOT a LucidShell interpreter or optical simulator."""
from pathlib import Path
import os
import json
import hashlib
import math
import sys
import time

mode = sys.argv[1]
script = Path(sys.argv[-1])
text = script.read_text(encoding="ascii")
if mode == "catia":
    request = json.loads(text)
    result = {"backend": "CATIA COM Automation"}
    if request["action"] == "catia_get_state":
        result.update(connected=True, document_count=0, documents=[], active_document=None)
    elif request["action"] == "catia_export_step":
        source = script.parent / "input.CATPart"
        step = script.parent / "export.stp"
        step.write_text("mock exported STEP, not real CAD")
        result.update(source_path=request["parameters"]["catia_path"], source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                      step_file=str(step), step_sha256=hashlib.sha256(step.read_bytes()).hexdigest(), body_count=1, hybrid_body_count=0, bodies=[])
    else:
        p = request["parameters"]
        theta = math.radians(p["tilt_deg"])
        x = p["width_mm"] / 2 * math.cos(theta)
        z = p["width_mm"] / 2 * abs(math.sin(theta))
        native = script.parent / "mirror.CATPart"
        step = script.parent / "mirror.stp"
        native.write_text("mock CATPart, not real CAD")
        step.write_text("mock STEP, not real CAD")
        result.update(p, native_part=str(native), step_file=str(step), step_sha256=hashlib.sha256(step.read_bytes()).hexdigest(),
                      expected_bounding_box_mm=[-x,x,-p["height_mm"]/2,p["height_mm"]/2,-z,z])
    (script.parent / "engine-result.json").write_text(json.dumps(result))
    print("LUCIDSHAPE_MCP_OK", flush=True)
    sys.exit(0)
if mode == "timeout":
    time.sleep(30)
    sys.exit(0)
if mode == "missing":
    print("Interpreter error with a zero exit code", flush=True)
    sys.exit(0)
if mode == "serial":
    lock = script.parent.parent / "exclusive.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        time.sleep(0.2)
    finally:
        os.close(descriptor)
        lock.unlink()
report = None
if "Geometry-only STEP import" in text:
    report = [10,4,3,[-1,2,-2,3,0,5],[-1,2,-2,3,0,5]]
    (script.parent / "geometry.lue").write_text("mock imported geometry, not real CAD")
elif "Fixed CAD mirror experiment" in text:
    report = [1,1,[-50,50,-40,40,0,0]]
    (script.parent / "scene.lue").write_text("mock prepared scene, not real CAD")
elif "MCP LID input" in text:
    report = ["mock",0,[60,60,1,1],[-60,60,-60,60],[250,1,3],0,800,25]
elif "MCP model trace" in text:
    report = [10000,1,[["sensor","CandelaSensor","sensor_0.lid"]]]
    if mode != "missing_sensor":
        (script.parent / "sensor_0.lid").write_text("mock LID")
elif "Runtime-verified LS2.0" in text:
    report = [1,[["source","PointLight"]],[[name,0,[]] for name in ("emitters","sensors","geometry","actors","light_data")]]
elif "ForwardRayTrace" in text:
    (script.parent / "demo.lid").write_text("mock LID; no optical data")
    (script.parent / "demo.lue").write_text("mock model; no optical data")
else:
    (script.parent / "point.lug").write_text("mock Point3")
if report is not None:
    output = json.dumps({"List": {f"p{i}": value for i,value in enumerate(report)}})
    (script.parent / "engine-result.json").write_text("invalid" if mode == "bad_json" else output)
if mode == "engine_error":
    print("error (ArgumentSource) in function call", flush=True)
if mode == "unknown_member":
    print("do not understand double member function call: unsupportedFunction", flush=True)
print("LUCIDSHAPE_MCP_OK", flush=True)
