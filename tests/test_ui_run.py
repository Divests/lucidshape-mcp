from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from lucidshape_mcp import ui_action_catalog, ui_run, ui_tools
from lucidshape_mcp.policy import ACTIVE_POLICY, Policy
from lucidshape_mcp.tool_api import registrar


class RunCatalogActionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        install = root / "LucidShape"
        exe = install / "bin" / "lush.exe"
        exe.parent.mkdir(parents=True)
        exe.touch()
        tasks = install / "include" / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "allCommandIDs.hdo").write_text("#define CM_UV_FILTER 3883\n#define CM_OTHER 42\n")
        (tasks / "mainMenu.hdo").write_text(
            'Menu("Edit", ActionControl("Smooth",CM_UV_FILTER), ActionControl("Other",CM_OTHER));\n'
            'Menu("Help", MenuItem("Guide","general_openTheHelpFile","$DOCPATH/Guide.pdf","Contents"));\n')
        (install / "doc").mkdir()
        (install / "doc" / "Guide.pdf").write_bytes(b"%PDF-1.7\n")
        self.work = root / "work"
        self.work.mkdir()
        self.source = self.work / "data.ies"
        self.source.touch()
        self.outside = root / "outside.ies"
        self.outside.touch()
        self.engine = SimpleNamespace(command=(str(exe),), policy=Policy(self.work, allowed_roots=(self.work,)))
        self.server = FastMCP("action routing tests")
        self.calls = []
        tool = registrar(self.server, self.engine)

        @tool()
        async def photometry_apply_ui_action(data_path: str, command: str, passes: int = 1) -> dict:
            self.calls.append((data_path, command, passes, ACTIVE_POLICY.get()))
            return {"status": "queued", "job_id": "fixture-job"}

        @tool()
        def studio_get_help_resource(action_id: str, profile: str = "studio") -> dict:
            self.calls.append((action_id, profile))
            return {"status": "available", "receipt": {"guide": "Guide.pdf"}}

        self.ids = {a["label"]: a["action_id"] for a in ui_action_catalog.action_inventory(install)[0]}

    async def run_item(self, label, **args):
        return await ui_run.run_action(self.server, self.engine, self.ids[label], args)

    async def test_runs_queued_and_direct_tools_with_trusted_presets(self):
        result = await self.run_item("Smooth", data_path=str(self.source), passes=2)
        self.assertEqual((result["status"], result["job_id"]), ("queued", "fixture-job"))
        self.assertEqual(self.calls.pop(), (str(self.source), "CM_UV_FILTER", 2, self.engine.policy))
        direct = await self.run_item("Guide")
        self.assertEqual(direct["receipt"], {"guide": "Guide.pdf"})
        self.assertEqual(self.calls.pop(), (self.ids["Guide"], "studio"))

    async def test_disabled_unbound_missing_and_conflicting_arguments_do_not_run(self):
        with self.assertRaisesRegex(ValueError, "catalog_only"):
            await self.run_item("Other")
        with self.assertRaisesRegex(ValueError, "Unknown action_id"):
            await ui_run.run_action(self.server, self.engine, "arbitrary-script", {})
        for args, message in [
            ({"command": "CM_UV_SCALE"}, "presets"), ({"command": "CM_UV_FILTER"}, "presets"),
            ({"command_id": 3883}, "Unknown arguments"), ({}, "Missing arguments"),
        ]:
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, message):
                await self.run_item("Smooth", **args)
        with self.assertRaisesRegex(ValueError, "JSON object"):
            await ui_run.run_action(self.server, self.engine, self.ids["Smooth"], 3883)
        with self.assertRaisesRegex(PermissionError, "not registered"):
            await ui_run.run_action(FastMCP("empty"), self.engine, self.ids["Guide"], {})
        self.engine.policy = Policy(self.work, read_only=True)
        with self.assertRaisesRegex(PermissionError, "disabled"):
            await self.run_item("Smooth", data_path=str(self.source))
        self.assertFalse(self.calls)

    async def test_inner_policy_error_keeps_structured_code_and_runs_no_native_operation(self):
        result = await self.run_item("Smooth", data_path=str(self.outside))
        self.assertIsInstance(result, CallToolResult)
        self.assertTrue(result.isError)
        self.assertEqual(result.structuredContent["error_details"]["code"], "ACCESS_DENIED")
        self.assertFalse(self.calls)

    async def test_refuses_recursive_target(self):
        with patch.object(ui_tools, "describe", return_value={
                "execution": {"status": "available", "tool": "studio_run_ui_action"}}):
            with self.assertRaisesRegex(ValueError, "Recursive"):
                await self.run_item("Smooth")
