"""Exercise the complete CAD-to-optics workflow using MCP tool calls only.

The client imports no CATIA COM, LucidShape engine runner, or computer-use API.
It only starts the MCP service via the official stdio transport, calls tools,
and reads artifact bytes to verify hashes. Application work stays in the server.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
CN_TIME = timezone(timedelta(hours=8))


def write_report(directory, report):
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def verify_optics(metrics, tilt_deg, flux_lm):
    peak = metrics["peak"]
    deflection = math.hypot(peak["u_deg"], peak["v_deg"])
    error = abs(deflection - 2 * abs(tilt_deg))
    if error > 0.51:
        raise AssertionError(f"Unexpected reflection direction: {metrics}")
    if abs(metrics["flux_in_data_domain_lm"] - flux_lm) > 1:
        raise AssertionError(f"Unexpected reflected flux: {metrics}")
    return {"estimated_deflection_deg": deflection, "deflection_error_deg": error}


async def validate(executable, directory):
    report = {"started_at": datetime.now(CN_TIME).isoformat(), "transport": "MCP stdio",
              "computer_use_calls": 0, "client_direct_com_calls": 0, "client_direct_lush_calls": 0,
              "jobs": [], "cases": [], "verified": False}
    write_report(directory, report)
    env = {**os.environ, "PYTHONPATH": str(ROOT), "LUCIDSHAPE_EXE": str(executable),
           "LUCIDSHAPE_WORK_DIR": str(directory / "jobs")}
    params = StdioServerParameters(command=sys.executable, args=["-m", "lucidshape_mcp.server"], env=env)
    try:
        with (directory / "mcp-server.log").open("w", encoding="utf-8") as errlog, (directory / "mcp-calls.jsonl").open("w", encoding="utf-8") as audit:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    report["server"] = initialized.serverInfo.model_dump()
                    report["tools"] = [tool.name for tool in (await session.list_tools()).tools]
                    assert {"catia_get_state", "catia_create_plane_mirror", "prepare_cad_mirror_scene", "inspect_model", "simulate_model", "analyze_lid"} <= set(report["tools"])

                    async def call(name, arguments):
                        response = await session.call_tool(name, arguments)
                        payload = json.loads(response.content[0].text) if not response.isError else [c.text for c in response.content if c.type == "text"]
                        audit.write(json.dumps({"time": datetime.now(CN_TIME).isoformat(), "tool": name,
                                                "arguments": arguments, "is_error": response.isError, "response": payload}, ensure_ascii=False) + "\n")
                        audit.flush()
                        if response.isError:
                            raise RuntimeError(f"{name}: {payload}")
                        return payload

                    async def run(name, arguments):
                        job = await call(name, {**arguments, "timeout_seconds": 90})
                        async with asyncio.timeout(110):
                            while job["status"] in ("queued", "running"):
                                await asyncio.sleep(0.5)
                                job = await call("get_job", {"job_id": job["job_id"]})
                        log = await call("read_job_log", {"job_id": job["job_id"]})
                        report["jobs"].append({"tool": name, "arguments": arguments, "job": job, "log": log})
                        write_report(directory, report)
                        print(name, job["status"], job["job_id"], flush=True)
                        if job["status"] != "succeeded":
                            raise RuntimeError(f"{name}: {job.get('error')}\n{log['text']}")
                        for artifact in job["artifacts"]:
                            path = Path(artifact["path"])
                            assert path.is_file() and path.stat().st_size == artifact["bytes"]
                        return job

                    report["capabilities"] = await call("get_capabilities", {})
                    before = await run("catia_get_state", {})
                    report["catia_before"] = before["result"]
                    for tilt in (0, 5):
                        cad = await run("catia_create_plane_mirror", {"width_mm": 100, "height_mm": 80, "tilt_deg": tilt})
                        prepared = await run("prepare_cad_mirror_scene", {"cad_path": cad["result"]["step_file"], "flux_lm": 1000, "reflection_factor": 1})
                        box = prepared["result"]["bounding_box_mm"]
                        expected = cad["result"]["expected_bounding_box_mm"]
                        assert max(abs(a-b) for a,b in zip(box, expected)) < 0.001
                        scene = prepared["result"]["scene_file"]
                        inspected = await run("inspect_model", {"model_path": scene})
                        assert inspected["result"]["groups"]["actors"]["count"] == 1
                        assert inspected["result"]["groups"]["emitters"]["count"] == 1
                        assert inspected["result"]["groups"]["sensors"]["count"] == 1
                        simulated = await run("simulate_model", {"model_path": scene, "rays": 10000, "intensity_threshold": 0.0001})
                        analyzed = await run("analyze_lid", {"lid_path": simulated["result"]["sensors"][0]["result_file"]})
                        case = {"tilt_deg": tilt, "cad": cad["result"], "prepared_scene": prepared["result"],
                                "metrics": analyzed["result"], **verify_optics(analyzed["result"], tilt, 1000)}
                        assert hashlib.sha256(Path(cad["result"]["step_file"]).read_bytes()).hexdigest() == cad["result"]["step_sha256"]
                        report["cases"].append(case)
                        write_report(directory, report)

                    # Independent material check: reuse the exported 5-degree CAD,
                    # prepare through MCP with R=0.8 and verify 800 lm, not 1000 lm.
                    step = report["cases"][1]["cad"]["step_file"]
                    lossy = await run("prepare_cad_mirror_scene", {"cad_path": step, "flux_lm": 1000, "reflection_factor": 0.8})
                    lossy_trace = await run("simulate_model", {"model_path": lossy["result"]["scene_file"], "rays": 10000, "intensity_threshold": 0.0001})
                    lossy_result = await run("analyze_lid", {"lid_path": lossy_trace["result"]["sensors"][0]["result_file"]})
                    report["material_check"] = {"reflection_factor": 0.8, "expected_flux_lm": 800,
                                                "metrics": lossy_result["result"], **verify_optics(lossy_result["result"], 5, 800)}
                    after = await run("catia_get_state", {})
                    report["catia_after"] = after["result"]
                    original = {(d["name"], d["full_name"]) for d in before["result"]["documents"]}
                    remaining = {(d["name"], d["full_name"]) for d in after["result"]["documents"]}
                    assert original <= remaining
                    assert after["result"]["document_count"] == before["result"]["document_count"] + 2
                    report["original_documents_preserved"] = True
                    report["verified"] = True
                    report["completed_at"] = datetime.now(CN_TIME).isoformat()
                    write_report(directory, report)
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        write_report(directory, report)
        raise
    lines = ["# CATIA → LucidShape 全流程 MCP 测试", "", "结果：通过。", "",
             "本次客户端只调用 MCP 工具。CATIA 建模和导出、STEP 场景准备、仿真和分析全部经同一 MCP 服务执行；computer-use 调用次数为 0。服务内部使用 CATIA COM 与 LucidShell 作为后端。", "",
             "调用顺序：catia_get_state → catia_create_plane_mirror → prepare_cad_mirror_scene → inspect_model → simulate_model → analyze_lid。", "",
             "| 镜面转角 | 测得峰值 (u,v) | 近轴估算偏转 | 理论偏转 | 收集光通量 |", "|---|---|---|---|---|"]
    for case in report["cases"]:
        peak = case["metrics"]["peak"]
        lines.append(f"| {case['tilt_deg']}° | ({peak['u_deg']:.2f}°, {peak['v_deg']:.2f}°) | {case['estimated_deflection_deg']:.2f}° | {2*case['tilt_deg']}° | {case['metrics']['flux_in_data_domain_lm']:.2f} lm |")
    lines += ["", "镜面尺寸 100×80 mm；源光通量 1000 lm；追迹 10,000 条光线；角度网格 0.5°。偏转比较容差 0.51°，几何包围盒比较容差 0.001 mm。", "",
              f"材料参数对照：将 5° 镜面的反射率通过 MCP 设置为 0.8 后，收集光通量为 {report['material_check']['metrics']['flux_in_data_domain_lm']:.2f} lm，符合 800 lm 预期。", "",
              "原有 CATIA 文档仍在；仅新增两份测试 CATPart。原始 STEP 哈希保持一致。", "",
              "本次通过本地 MCP 客户端启动和验证服务；没有修改任何客户端的全局 MCP 配置。这验证固定镜面用例，不等同于任意 CATIA 建模或 LucidStudio 当前窗口控制。", "",
              "report.json 保存 15 个任务记录；mcp-calls.jsonl 保存每次工具请求和响应；jobs 内保存工作进程请求、日志、模型和 LID 产物。"]
    (directory / "全流程MCP测试报告.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print("FULL_MCP_VERIFIED", directory, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", default=os.environ.get('LUCIDSHAPE_EXE'), help='Absolute lush.exe path, or set LUCIDSHAPE_EXE')
    args = parser.parse_args()
    if not args.exe:
        parser.error('Provide --exe or set LUCIDSHAPE_EXE')
    directory = ROOT / "local-data" / "work" / f"full-mcp-{datetime.now(CN_TIME):%Y%m%dT%H%M%S}"
    directory.mkdir(parents=True)
    print("REPORT_DIRECTORY", directory, flush=True)
    asyncio.run(validate(Path(args.exe).resolve(strict=True), directory))
