"""MCP boundary tests for native road views; no LucidShape installation needed."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from lucidshape_mcp.engine import BatchEngine
from lucidshape_mcp.inputs import snapshot
from lucidshape_mcp.policy import Policy
from lucidshape_mcp.server import create_server
from lucidshape_mcp import road_simulation

ENV = {"LUCIDSHAPE_TOOLSETS": "core,analysis", "LUCIDSHAPE_TOOLS": "",
       "LUCIDSHAPE_READ_ONLY": "false", "LUCIDSHAPE_ALLOWED_ROOTS": "[]",
       "LUCIDSHAPE_ALLOW_SOURCE_WRITES": "false"}


class RoadMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_defaults_and_readonly_policy(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, ENV):
            root = Path(temp)
            source = root / "input.ies"
            source.write_text("synthetic input")
            engine = BatchEngine(root / "work")
            try:
                server = create_server(engine)
                tool = next(t for t in await server.list_tools() if t.name == "generate_road_views")
                self.assertFalse(tool.annotations.readOnlyHint)
                self.assertFalse(tool.annotations.destructiveHint)
                self.assertIn("lamp_positions_m", tool.inputSchema["required"])
                self.assertIn("road_bounds_m", tool.inputSchema["required"])
                arguments = {"lid_path": str(source), "lamp_positions_m": [[0, -.75, .8]],
                             "driver_position_m": [-1.5, .35, 1.2], "road_bounds_m": [5, 100, -5, 5]}
                with patch.object(engine, "submit_road_views", AsyncMock(return_value={"status": "queued"})) as submit:
                    await server.call_tool("generate_road_views", arguments)
                    self.assertEqual(submit.call_args.kwargs["view_bounds_deg"], [-20, 20, -12, 8])
                    self.assertEqual(submit.call_args.kwargs["view_angles_deg"], [0, 0, 0])
                    await server.call_tool("generate_road_views", {**arguments, "view_bounds_deg": []})
                    self.assertEqual(submit.call_args.kwargs["view_bounds_deg"], [])
                engine.policy = Policy(engine.root, toolsets=("core", "analysis"), read_only=True)
                self.assertNotIn("generate_road_views", {t.name for t in await create_server(engine).list_tools()})
                with self.assertRaises(PermissionError):
                    await engine.submit_road_views(**arguments)
            finally:
                await engine.close()

    async def test_source_path_policy_is_checked_before_staging(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, ENV):
            root = Path(temp); allowed = root / "allowed"; allowed.mkdir()
            outside = root / "outside.ies"; outside.write_text("private")
            engine = BatchEngine(root / "work", (sys.executable,))
            engine.policy = Policy(engine.root, allowed_roots=(allowed,))
            try:
                with patch.object(road_simulation, "validate") as validate:
                    with self.assertRaises(PermissionError):
                        await engine.submit_road_views(lid_path=str(outside))
                    validate.assert_not_called()
            finally:
                await engine.close()

    async def test_private_job_verification_and_source_change_detection(self):
        for modify_source in (False, True):
            with self.subTest(modify_source=modify_source), tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, ENV):
                root = Path(temp)
                source = root / "input.ies"; source.write_text("unchanged")
                worker = root / "mock.py"
                worker.write_text('from pathlib import Path\nPath("engine-result.json").write_text("{}")\nprint("LUCIDSHAPE_MCP_OK")\n')
                engine = BatchEngine(root / "work", (sys.executable, str(worker)))
                digest = hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
                def stage(directory, parameters, executable):
                    self.assertEqual(executable, Path(sys.executable))
                    entries = snapshot(source, directory, "input.ies")
                    (directory / "job.do").write_text("fixed template")
                    return entries, ("engine-result.json",)
                try:
                    with patch.object(road_simulation, "VERIFIED_ENGINE", digest), \
                         patch.object(road_simulation, "validate", side_effect=lambda p: p), \
                         patch.object(road_simulation, "stage", side_effect=stage), \
                         patch.object(road_simulation, "finalize", return_value=({"native": True}, ())) as finalize:
                        queued = await engine.submit_road_views(lid_path=str(source), timeout_seconds=20)
                        self.assertEqual(queued["status"], "queued")
                        if modify_source:
                            source.write_text("changed after snapshot")
                        await asyncio.gather(*engine._tasks.values())
                        result = engine.get_job(queued["job_id"])
                        if modify_source:
                            self.assertEqual(result["status"], "failed")
                            self.assertIn("Source or dependency changed", result["error"])
                        else:
                            self.assertEqual(result["status"], "succeeded")
                            self.assertTrue(result["result"]["source_files_unchanged"])
                            self.assertEqual(result["result"]["engine_sha256"], digest)
                            self.assertTrue(any(a["name"] == "result.json" for a in result["artifacts"]))
                        finalize.assert_called_once()
                finally:
                    await engine.close()

    async def test_unvalidated_engine_refuses_before_staging(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, ENV):
            root = Path(temp); source = root / "input.ies"; source.write_text("fixture")
            engine = BatchEngine(root / "work", (sys.executable,))
            try:
                with patch.object(road_simulation, "VERIFIED_ENGINE", "0"*64), \
                     patch.object(road_simulation, "validate", side_effect=lambda p: p), \
                     patch.object(road_simulation, "stage") as stage:
                    with self.assertRaisesRegex(ValueError, "validated LS2.0"):
                        await engine.submit_road_views(lid_path=str(source))
                    stage.assert_not_called()
            finally:
                await engine.close()

