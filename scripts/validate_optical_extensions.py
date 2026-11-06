"""Opt-in real stdio MCP validation using only private synthetic optical scenes."""
import argparse
import asyncio
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]

async def main(executable, output):
    output.mkdir(parents=True, exist_ok=False)
    fixture = output / "fixture"; fixture.mkdir()
    source = '''object nil;
object medium=Medium("air-like",1.0,0);
object em=LambertEmitter("shared emitter",100,medium,45,70);
object ax=AxisSystem("",$,(0,0,0),(0,0,1),(1,0,0));
object one=PointLight("source A",ax,em);
object two=PointLight("source B",AxisSystem("",$,(1,0,0),(0,0,1),(1,0,0)),em);
object lid=LIDData("sensor data",(),0,12,12,(-60,60,-60,60),1,$);
object sensor=SurfaceShape("far field",InfinitySurface(""),CandelaSensor("sensor",1,lid));
object scene=ObjectVector("fixture",(one,two,sensor));
object out=Store("scene.lue");out << scene;out=nil;
object p=em.getDataVector(),types=();
for(double i=0;i<p.size();i++) types << p.typeAt(i);
out=JSONStore("types.json",0,1);out << (types,one.nameOfClass());out=nil;
object reference=LIDData("reference",(),0,4,4,(-20,20,-20,20),1,$);
reference.setAllValues(200);
object referenceCopy=reference.deepClone();
out=Store("reference.lid");out << referenceCopy;out=nil;
object candidate=reference.deepClone();candidate.scaleByFactor(0.5);
out=Store("candidate.lid");out << candidate;out=nil;
cout << "FIXTURE_COMPLETE" << endl;
'''
    (fixture / "create.do").write_text(source, encoding="ascii")
    with (fixture / "engine.log").open("wb") as log:
        proc = subprocess.run([str(executable), "-nopp", "create.do"], cwd=fixture, stdout=log, stderr=subprocess.STDOUT,
                              timeout=45, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    text = (fixture / "engine.log").read_text(errors="replace")
    if proc.returncode or "FIXTURE_COMPLETE" not in text or "do not understand" in text:
        raise RuntimeError("Fixture creation failed")
    env = {**os.environ, "LUCIDSHAPE_EXE": str(executable), "LUCIDSHAPE_WORK_DIR": str(output / "jobs"), "PYTHONPATH": str(ROOT)}
    result = {"status": "running", "fixture": str(fixture), "calls": [], "Studio_accessed": False,
              "source_sha256": hashlib.sha256((fixture / "scene.lue").read_bytes()).hexdigest()}
    def save():
        (output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "server.log").open("w", encoding="utf-8") as log:
        async with stdio_client(StdioServerParameters(command=sys.executable, args=["-m", "lucidshape_mcp.server"], env=env, cwd=str(ROOT)), errlog=log) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                result["tool_count"] = len(tools)
                result["new_tools"] = [t.model_dump(mode="json") for t in tools if t.name in ("compare_photometry", "inspect_optical_parameters", "set_emitter_parameters", "run_parameter_sweep")]
                async def call(name, arguments):
                    response = await session.call_tool(name, arguments)
                    payload = response.structuredContent
                    if payload is None:
                        payload = json.loads(next(c.text for c in response.content if c.type == "text"))
                    if response.isError: raise RuntimeError(str(payload))
                    while payload.get("status") in ("queued", "running"):
                        await asyncio.sleep(.3)
                        response = await session.call_tool("get_job", {"job_id": payload["job_id"]})
                        payload = response.structuredContent or json.loads(next(c.text for c in response.content if c.type == "text"))
                    result["calls"].append({"tool": name, "arguments": arguments, "response": payload}); save()
                    print(json.dumps({"tool": name, "status": payload.get("status"), "job_id": payload.get("job_id"), "error": payload.get("error")}), flush=True)
                    return payload
                model = str(fixture / "scene.lue")
                compare = await call("compare_photometry", {"reference_path": str(fixture / "reference.lid"), "candidate_path": str(fixture / "candidate.lid")})
                inspect = await call("inspect_optical_parameters", {"model_path": model})
                edit = await call("set_emitter_parameters", {"model_path": model, "source_name": "source A", "flux_lm": 200})
                sweep = await call("run_parameter_sweep", {"model_path": model, "source_name": "source A", "cases": [{"flux_lm": 100}, {"flux_lm": 100}, {"flux_lm": 200}], "rays": 3000})
                if all(j["status"] == "succeeded" for j in (compare, inspect, edit, sweep)):
                    assert compare["result"]["min_delta_cd"] == -100 and compare["result"]["decreased_cells"] == 16
                    inspected_edit = await call("inspect_optical_parameters", {"model_path": edit["result"]["modified_model"]})
                    assert inspected_edit["status"] == "succeeded"
                    sources = {r["name"]: r["parameters"] for r in inspected_edit["result"]["groups"]["Shape"]["objects"] if r["type"] in ("PointLight", "PointLightSource")}
                    assert sources["source A"]["emitter"]["flux"]["value"] == 200
                    assert sources["source B"]["emitter"]["flux"]["value"] == 100
                    trials = sweep["result"]["cases"]
                    repeated = await call("compare_photometry", {"reference_path": trials[0]["sensors"][0]["file"], "candidate_path": trials[1]["sensors"][0]["file"]})
                    assert repeated["status"] == "succeeded" and repeated["result"]["rmse_cd"] == 0
                    assert 1.35 < trials[2]["sensors"][0]["flux_in_domain_lm"] / trials[0]["sensors"][0]["flux_in_domain_lm"] < 1.65
                    result["status"] = "passed"
                else:
                    result["status"] = "failed"
    result["source_preserved"] = hashlib.sha256((fixture / "scene.lue").read_bytes()).hexdigest() == result["source_sha256"]
    assert result["source_preserved"]
    save(); print(json.dumps({"status": result["status"], "report": str(output / "report.json")}))
    if result["status"] != "passed": raise SystemExit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "local-data" / "work" / ("optical-extension-validation-" + datetime.now().strftime("%Y%m%dT%H%M%S")))
    args = parser.parse_args()
    asyncio.run(main(args.executable.resolve(), args.output_dir.resolve()))
