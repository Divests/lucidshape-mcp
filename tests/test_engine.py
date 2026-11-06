import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from lucidshape_mcp.engine import BatchEngine


FIXTURE = Path(__file__).with_name("mock_engine.py").resolve()


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lucidshape test ")
        self.root = Path(self.temporary.name)
        self.engine = BatchEngine(self.root, (sys.executable, str(FIXTURE), "ok"))

    async def asyncTearDown(self):
        await self.engine.close()
        self.temporary.cleanup()

    async def completed(self, job):
        task = self.engine._tasks[job["job_id"]]
        await asyncio.wait_for(asyncio.shield(task), 10)
        return self.engine.get_job(job["job_id"])

    async def test_healthcheck_uses_isolated_directory_with_spaces(self):
        job = await self.engine.submit("healthcheck")
        self.assertEqual(job["status"], "queued")
        result = await self.completed(job)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["artifacts"][0]["name"], "point.lug")
        self.assertIn("LUCIDSHAPE_MCP_OK", self.engine.read_log(job["job_id"])["text"])

    async def test_demo_artifacts_and_parameter_validation(self):
        result = await self.completed(await self.engine.submit("demo_simulation", rays=200, flux_lm=50))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual({a["name"] for a in result["artifacts"]}, {"demo.lid", "demo.lue"})
        for arguments in ({"rays": True}, {"rays": "100;system"}, {"flux_lm": float("nan")}, {"timeout_seconds": 0}):
            with self.assertRaises(ValueError):
                await self.engine.submit("demo_simulation", **arguments)
        with self.assertRaises(ValueError):
            self.engine.get_job("../outside")

    async def test_zero_exit_code_without_outputs_is_not_success(self):
        self.engine.command = (sys.executable, str(FIXTURE), "missing")
        result = await self.completed(await self.engine.submit("healthcheck"))
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["artifacts"], [])

    async def test_main_lens_preparation_rejects_wrong_mode_before_staging(self):
        for arguments in ({'include_main_lens':True,'geometry_representation':'step'},
                          {'include_main_lens':1,'geometry_representation':'normal_mesh'}):
            with self.assertRaises(ValueError):
                await self.engine.submit('prepare_inner_lens_regions',region_manifest_path='missing.json',**arguments)
        self.assertEqual(self.engine._tasks,{})

    async def test_timeout_waits_for_owned_process_to_exit(self):
        self.engine.command = (sys.executable, str(FIXTURE), "timeout")
        result = await self.completed(await self.engine.submit("healthcheck", timeout_seconds=1))
        self.assertEqual(result["status"], "timed_out")

    async def test_jobs_never_overlap(self):
        self.engine.command = (sys.executable, str(FIXTURE), "serial")
        first = await self.engine.submit("healthcheck")
        second = await self.engine.submit("healthcheck")
        results = await asyncio.gather(self.completed(first), self.completed(second))
        self.assertEqual([r["status"] for r in results], ["succeeded", "succeeded"])

    async def test_stale_running_record_is_not_reported_as_running(self):
        job = await self.engine.submit("healthcheck")
        await self.completed(job)
        path = self.root / job["job_id"] / "job.json"
        record = json.loads(path.read_text())
        record["status"] = "running"
        path.write_text(json.dumps(record))
        restarted = BatchEngine(self.root)
        self.assertEqual(restarted.get_job(job["job_id"])["status"], "interrupted")

    async def test_stdio_tool_discovery_and_unconfigured_error(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        environment = {**os.environ, "LUCIDSHAPE_EXE": "", "LUCIDSHAPE_WORK_DIR": str(self.root),
                       "LUCIDSHAPE_TOOLSETS": "all", "LUCIDSHAPE_TOOLS": "", "LUCIDSHAPE_READ_ONLY": "false"}
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "lucidshape_mcp.server"], env=environment)
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                self.assertTrue({'list_jobs', 'cancel_job'} <= names)
                names -= {'list_jobs', 'cancel_job'}
                from lucidshape_mcp.policy import ALL_TOOLS
                self.assertEqual(names | {'list_jobs','cancel_job'}, ALL_TOOLS)
                result = await session.call_tool("get_capabilities", {})
                self.assertFalse(result.isError)
                self.assertFalse(json.loads(result.content[0].text)["executable_exists"])
                unavailable = await session.call_tool("health_check", {})
                self.assertTrue(unavailable.isError)

    async def test_model_report_and_input_snapshot_are_returned(self):
        source = self.root / "model with spaces.lue"
        source.write_text('@1=PointLight("source");')
        job = await self.completed(await self.engine.submit("inspect_model", model_path=str(source)))
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["result"]["objects"][0]["name"], "source")
        self.assertEqual(job["inputs"][0]["source"], str(source.resolve()))
        self.assertTrue((self.root / job["job_id"] / "result.json").is_file())

    async def test_analysis_validation_and_structured_metrics(self):
        source = self.root / "test.lid"
        source.write_text('@1=LIDData("example");')
        for region in ([10,-10,0,1], [0,1,0,float("nan")], [0,1], "not a region"):
            with self.assertRaises(ValueError):
                await self.engine.submit("analyze_lid", lid_path=str(source), region_deg=region)
        job = await self.completed(await self.engine.submit("analyze_lid", lid_path=str(source), region_deg=[-10,10,-10,10]))
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["result"]["peak"]["intensity_cd"], 250)
        self.assertEqual(job["result"]["region"]["flux_lm"], 25)

    async def test_malformed_report_and_missing_sensor_output_fail(self):
        source = self.root / "test.lue"
        source.write_text('@1=PointLight("source");')
        for mode, template in (("bad_json", "inspect_model"), ("missing_sensor", "simulate_model")):
            self.engine.command = (sys.executable, str(FIXTURE), mode)
            job = await self.completed(await self.engine.submit(template, model_path=str(source)))
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["artifacts"], [])

    async def test_analysis_snapshots_ies_with_its_format_extension(self):
        source = self.root / "photometry with spaces.IES"
        source.write_text('IESNA:LM-63-1995\nTILT=NONE\n')
        job = await self.completed(await self.engine.submit("analyze_lid", lid_path=str(source)))
        self.assertEqual(job["status"], "succeeded")
        directory = self.root / job["job_id"]
        self.assertEqual((directory / "input.ies").read_bytes(), source.read_bytes())
        self.assertIn('if(1)', (directory / "job.do").read_text())
        self.assertIn('IESData("MCP IES analysis","input.ies")', (directory / "job.do").read_text())
        self.assertNotIn('@@DATA_FILENAME@@', (directory / "job.do").read_text())

    async def test_engine_error_is_not_hidden_by_success_marker(self):
        self.engine.command = (sys.executable, str(FIXTURE), "engine_error")
        job = await self.completed(await self.engine.submit("healthcheck"))
        self.assertEqual(job["status"], "failed")
        self.assertIn("ArgumentSource", job["error"])

    async def test_unknown_native_member_is_not_hidden_by_success_marker(self):
        self.engine.command = (sys.executable, str(FIXTURE), "unknown_member")
        job = await self.completed(await self.engine.submit("healthcheck"))
        self.assertEqual(job["status"], "failed")
        self.assertIn("do not understand", job["error"])

    async def test_catia_worker_does_not_require_lucid_executable(self):
        self.engine.command = ()
        self.engine._catia_command = (sys.executable, str(FIXTURE), "catia")
        state = await self.completed(await self.engine.submit("catia_get_state"))
        self.assertEqual(state["status"], "succeeded")
        self.assertTrue(state["result"]["connected"])
        created = await self.completed(await self.engine.submit("catia_create_plane_mirror", tilt_deg=5))
        self.assertEqual(created["status"], "succeeded")
        self.assertEqual(created["result"]["tilt_deg"], 5)
        self.assertEqual({a["name"] for a in created["artifacts"]}, {"engine-result.json", "result.json", "mirror.CATPart", "mirror.stp"})
        self.assertTrue(Path(created["result"]["step_file"]).is_file())

    async def test_catia_parameters_rejected_before_process_launch(self):
        for arguments in ({"width_mm":0}, {"height_mm":True}, {"tilt_deg": float("nan")}, {"tilt_deg":16}):
            with self.assertRaises(ValueError):
                await self.engine.submit("catia_create_plane_mirror", **arguments)
        self.assertFalse(self.engine._tasks)

    async def test_step_preparation_returns_scene_and_keeps_source(self):
        source = self.root / "external mirror with spaces.step"
        source.write_text("mock CAD source")
        original = source.read_bytes()
        result = await self.completed(await self.engine.submit("prepare_cad_mirror_scene", cad_path=str(source)))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["result"]["root_surface_count"], 1)
        self.assertEqual(result["result"]["bounding_box_mm"], [-50,50,-40,40,0,0])
        self.assertTrue(Path(result["result"]["scene_file"]).is_file())
        self.assertEqual(source.read_bytes(), original)
        for bad in ({"reflection_factor": 2}, {"source_z_mm": 0}, {"flux_lm": float("inf")}):
            with self.assertRaises(ValueError):
                await self.engine.submit("prepare_cad_mirror_scene", cad_path=str(source), **bad)

    async def test_existing_catia_file_export_uses_private_copy(self):
        source = self.root / "source.CATPart"
        source.write_bytes(b"opaque native CAD bytes")
        self.engine.command = ()
        self.engine._catia_command = (sys.executable, str(FIXTURE), "catia")
        job = await self.completed(await self.engine.submit("catia_export_step", catia_path=str(source)))
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(source.read_bytes(), b"opaque native CAD bytes")
        self.assertEqual((self.root / job["job_id"] / "input.CATPart").read_bytes(), source.read_bytes())
        self.assertTrue(Path(job["result"]["step_file"]).is_file())

    async def test_multiple_surface_geometry_import(self):
        source = self.root / "geometry.step"
        source.write_text("mock STEP geometry")
        job = await self.completed(await self.engine.submit("import_cad_geometry", cad_path=str(source)))
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["result"]["root_surface_count"], 3)
        self.assertEqual(job["result"]["dimensions_mm"], [3,5,5])
        self.assertTrue(job["result"]["native_roundtrip_verified"])
        self.assertFalse(job["result"]["optical_objects_added"])


if __name__ == "__main__":
    unittest.main()
