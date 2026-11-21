"""Exercise the MCP bridge with a small protocol fixture and no private standards."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lucidshape_mcp import vehicle_lighting
from lucidshape_mcp.engine import BatchEngine
from lucidshape_mcp.policy import Policy
from lucidshape_mcp.server import create_server


WORKER = """
import hashlib, json, pathlib, sys, zipfile
if sys.argv[1] == "--catalog":
    print(json.dumps({"standards":[{"id":"sample","adb":"--adb" in sys.argv}], "engine_version":"fixture"}))
else:
    folder = pathlib.Path(sys.argv[2])
    project = json.loads((folder / "request.json").read_text())
    assert sys.flags.isolated and sys.dont_write_bytecode
    if project["project"] == "global error":
        print(json.dumps({"error": "Unknown rule: nope"}), file=sys.stderr)
        sys.exit(2)
    status = "error" if project["project"] == "invalid measure" else "partial"
    summary = {"sample": {"status": status, "total": None}}
    if status == "error":
        summary["sample"]["error"] = "Distance must be nonnegative"
    base = {"project": project["project"], "schema_version": "vehicle-lighting/1", "summary": summary}
    (folder / "lighting.json").write_text(json.dumps(dict(base, status=status)))
    with zipfile.ZipFile(folder / "lighting.xlsx", "w") as book:
        book.writestr("xl/workbook.xml", "<workbook/>")
    hashes = {n: hashlib.sha256((folder / n).read_bytes()).hexdigest() for n in ("lighting.json", "lighting.xlsx")}
    (folder / "receipt.json").write_text(json.dumps(dict(base, evaluation_status=status, artifact_sha256=hashes)))
    if status == "error":
        sys.exit(2)
    print("LUCIDSHAPE_MCP_OK")
"""


class VehicleLightingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.inputs = self.root / "allowed"
        self.inputs.mkdir()
        runtime = self.root / "runtime"
        runtime.mkdir()
        (runtime / "vehicle_lighting.py").write_text(WORKER, encoding="utf-8")
        env = {"LUCIDSHAPE_LIGHTING_RUNTIME": str(runtime), "LUCIDSHAPE_TOOLSETS": "core,analysis",
               "LUCIDSHAPE_READ_ONLY": "false", "LUCIDSHAPE_ALLOWED_ROOTS": json.dumps([str(self.inputs)]),
               "LUCIDSHAPE_TOOLS": "", "LUCIDSHAPE_ALLOW_SOURCE_WRITES": "false"}
        self.env_patch = patch.dict(os.environ, env)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.engine = BatchEngine(self.root / "jobs")
        self.addAsyncCleanup(self.engine.close)
        self.server = create_server(self.engine)

    def project(self, name):
        lamp = {"ies": name, "x_m": 0, "y_m": 0, "z_m": 1}
        return {"project": "sample", "has_adb": False, "standards": {"sample": {}},
                "photometry": {"low_beam": {"left": lamp, "right": lamp}, "projection": "radial"}}

    async def test_readonly_catalog_and_catalog_id_validation(self):
        self.engine.policy = Policy(self.engine.root, toolsets=("analysis",), read_only=True)
        server = create_server(self.engine)
        tools = {t.name: t for t in await server.list_tools()}
        self.assertTrue(tools["list_lighting_standards"].annotations.readOnlyHint)
        self.assertNotIn("evaluate_vehicle_lighting", tools)
        _, data = await server.call_tool("list_lighting_standards", {"standard_id": "sample", "has_adb": True})
        self.assertTrue(data["standards"][0]["adb"])
        failure = await server.call_tool("list_lighting_standards", {"standard_id": "--job"})
        self.assertTrue(failure.isError)

    async def test_job_snapshots_relative_and_duplicate_inputs_and_returns_receipt(self):
        ies = self.inputs / "two-lamps.ies"
        ies.write_text("IES fixture", encoding="ascii")
        project = self.inputs / "model.json"
        project.write_text(json.dumps(self.project(ies.name)), encoding="utf-8")
        _, submitted = await self.server.call_tool("evaluate_vehicle_lighting", {"config_path": str(project)})
        self.assertEqual(submitted["status"], "queued")
        job = self.engine.root / submitted["job_id"]
        self.assertEqual(len(submitted["inputs"]), 2)
        snap = json.loads((job / "request.json").read_text())
        self.assertEqual(snap["photometry"]["low_beam"]["left"]["ies"], "lamp-01.ies")
        self.assertEqual((job / "source-project.json").read_bytes(), project.read_bytes())
        ies.write_text("source changes after staging", encoding="ascii")
        self.assertEqual((job / "lamp-01.ies").read_text(), "IES fixture")
        await asyncio.gather(*self.engine._tasks.values())
        _, record = await self.server.call_tool("get_job", {"job_id": submitted["job_id"]})
        self.assertEqual(record["status"], "succeeded", record)
        self.assertEqual(record["result"]["evaluation_status"], "partial")
        self.assertTrue(set(vehicle_lighting.OUTPUTS) <= {a["name"] for a in record["artifacts"]})
        with (job / "lighting.json").open("ab") as artifact:
            artifact.write(b" ")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            vehicle_lighting.finalize(job)

    def test_large_project_file_limit_is_separate_from_inline_limit(self):
        stem = {"project": "fixture", "standards": {"sample": {"data": "A" * vehicle_lighting.MAX_CONFIG}}}
        directory = self.root / "limits"
        directory.mkdir()
        with self.assertRaisesRegex(ValueError, "size limit"):
            vehicle_lighting.stage(directory, self.engine.policy, stem, None)
        cfg = self.inputs / "large.json"
        cfg.write_text(json.dumps(stem), encoding="ascii")
        manifest = vehicle_lighting.stage(directory, self.engine.policy, None, str(cfg))
        self.assertEqual(manifest[0]["source_bytes"], cfg.stat().st_size)

    async def test_refuses_escape_inline_relative_double_input_and_duplicate_keys(self):
        outside = self.root / "private.ies"
        outside.write_text("not staged")
        cfg = self.inputs / "bad.json"
        cfg.write_text(json.dumps(self.project("../private.ies")))
        for args, code in (({"project": self.project(str(outside))}, "ACCESS_DENIED"),
                           ({"config_path": str(cfg)}, "ACCESS_DENIED"),
                           ({"project": self.project("relative.ies")}, "INVALID_ARGUMENT"),
                           ({"config_path": str(cfg), "project": {}}, "INVALID_ARGUMENT"),
                           ({"project": {"runtime": "different-worker"}}, "INVALID_ARGUMENT")):
            with self.subTest(args=args):
                failure = await self.server.call_tool("evaluate_vehicle_lighting", args)
                self.assertTrue(failure.isError)
                self.assertEqual(failure.structuredContent["error_details"]["code"], code)
        cfg.write_text('{"standards": {}, "standards": {}}')
        failure = await self.server.call_tool("evaluate_vehicle_lighting", {"config_path": str(cfg)})
        self.assertTrue(failure.isError)
        self.assertFalse(self.engine._tasks)

    async def test_failed_jobs_expose_rule_errors_or_global_parser_error(self):
        for label, expected in (("global error", "Unknown rule: nope"), ("invalid measure", "Distance must be nonnegative")):
            _, submitted = await self.server.call_tool("evaluate_vehicle_lighting",
                {"project": {"project": label, "standards": {"sample": {}}}})
            await asyncio.gather(*self.engine._tasks.values())
            _, result = await self.server.call_tool("get_job", {"job_id": submitted["job_id"]})
            self.assertEqual(result["status"], "failed")
            self.assertIn(expected, result["error"])
            if label == "invalid measure":
                self.assertEqual(result["result"]["summary"]["sample"]["error"], expected)
                self.assertEqual({r["name"] for r in result["artifacts"]}, set(vehicle_lighting.OUTPUTS))
            else:
                self.assertFalse(result["artifacts"])


if __name__ == "__main__":
    unittest.main()
