import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from lucidshape_mcp.studio_queries import (
    PARAMETERS, get_api_catalog, inspect_objects, get_simulation_settings,
    parameter_mapping, parse_catalog, parse_objects, parse_settings, receipt,
)


def envelope(nonce, action, payload, *, model=True, name='C:\\models\\灯"1.lug', busy=False, same=True):
    values = [nonce, 1, action, int(model), name if model else "", int(model), int(busy), payload, int(same), "completed"]
    return {"List": {f"p{i}": value for i, value in enumerate(values)}}


def object_page():
    return [1, [[0, [1, 'reflector "A"', "SurfaceShape"], [
        ["material", 1, [1, "mirror", "Specular"]],
        ["exit_material", 1, [0, "", ""]],
        ["surface", 0, [0, "", ""]],
    ]]]]


def mapping_fixture(root):
    """A distinct installed-mapping fixture; indices are deliberately nonzero."""
    path = root / "include" / "tasks" / "raytracemapping.hdo"
    path.parent.mkdir(parents=True)
    lines = []
    for i, (block, fields) in enumerate(PARAMETERS.values()):
        lines.append(f"#define {block} {i + 2} // block")
        for j, name in enumerate(fields.values()):
            lines.append(f"#define {name} {j + 1} // field")
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("ascii"))
    return path


class ReceiptTests(unittest.TestCase):
    def test_stale_action_version_partial_and_model_change_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt.json"
            original = envelope("nonce", "objects", object_page())
            for field, value in (("p0", "old"), ("p1", 2), ("p2", "api_catalog"),
                                 ("p9", "started"), ("p8", 0), ("p3", True), ("p5", -1)):
                raw = json.loads(json.dumps(original))
                raw["List"][field] = value
                path.write_text(json.dumps(raw), encoding="utf-8")
                with self.subTest(field=field), self.assertRaises(ValueError):
                    receipt(path, "nonce", "objects")
            path.write_text(json.dumps(original), encoding="utf-8")
            model, _ = receipt(path, "nonce", "objects")
            self.assertEqual(model["model_name"], 'C:\\models\\灯"1.lug')

    def test_object_edges_distinguish_absent_and_unqueried_and_reject_bad_page(self):
        context = dict(offset=0, limit=10, object_type="Shape", selected_only=False)
        result = parse_objects(object_page(), None, context)
        edges = result["objects"][0]["relationships"]
        self.assertEqual(edges["material"]["target"]["type"], "Specular")
        self.assertEqual(edges["exit_material"]["status"], "not_assigned")
        self.assertEqual(edges["surface"]["status"], "not_queried")
        self.assertIsNone(result["next_offset"])
        for payload in ([2, object_page()[1]], [1, []], [0, object_page()[1]]):
            with self.assertRaises(ValueError):
                parse_objects(payload, None, context)
        self.assertEqual(parse_objects([1, []], None, {**context, "offset": 5})["objects"], [])

    def test_catalog_groups_filter_pagination_and_incomplete_signatures(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "type-methods.txt").write_text("methods of Shape\n double o.isSensor(...);\n object o.getMaterial(...);\n")
            (directory / "type-description.txt").write_text("Shape help")
            context = dict(type_name="Shape", filter_text="shape", offset=1, limit=1, max_documentation_chars=100)
            payload = [["/Base", "-Object", "/Shape", "-Shape", "-SurfaceShape", "-PointLight"]]
            result = parse_catalog(payload, directory, context)
            self.assertEqual(result["entries"][0]["name"], "SurfaceShape")
            self.assertEqual(result["matching_entries"], 3)
            self.assertEqual(result["next_offset"], 2)
            self.assertFalse(result["documentation"]["methods"][0]["signature_complete"])
            (directory / "type-methods.txt").write_text("")
            (directory / "type-description.txt").write_text("")
            self.assertEqual(parse_catalog(payload, directory, context)["documentation"]["status"], "unavailable")
            with self.assertRaises(ValueError):
                parse_catalog([[]], directory, context)

    def test_mapping_uses_installed_indices_and_preserves_ray_units(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = mapping_fixture(root)
            mapping = parameter_mapping(root / "BIN64GPU" / "LucidShape.exe")
            rows = []
            for label, (block, fields) in PARAMETERS.items():
                rows.append([label, mapping["definitions"][block], [
                    [name, mapping["definitions"][macro], 2500 if name == "ray_count" else 1]
                    for name, macro in fields.items()]])
            result = parse_settings([1, rows], root, mapping)
            self.assertEqual(result["settings"]["monte_carlo"]["ray_count"], 2500)
            self.assertEqual(result["raw_numeric_groups"][0][2][0][1], 1)
            self.assertFalse(result["settings_modified"])
            rows[0][2][0][1] = 0
            with self.assertRaises(ValueError):
                parse_settings([1, rows], root, mapping)
            source.write_text(source.read_text() + "\n#define NUM_RAYS 55\n")
            with self.assertRaises(ValueError):
                parameter_mapping(root / "BIN64GPU" / "LucidShape.exe")


class QueryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.exe = self.root / "BIN64GPU" / "LucidShape.exe"
        self.exe.parent.mkdir()
        self.exe.write_bytes(b"not executable; query transport fixture only")
        self.calls = []
        self.writers = []

    async def asyncTearDown(self):
        await asyncio.gather(*self.writers)
        self.temporary.cleanup()

    def transport(self, payload=None, *, model=True, busy=False, same=True, pid=42,
                  connected=True, acknowledged=True, partial=False, wrong_nonce=False):
        async def call(directory, request, timeout_seconds):
            self.calls.append(request)
            if request["action"] == "probe":
                return {"connected": connected, "process_id": 42, "executable": str(self.exe)}
            info = json.loads((directory / "query-request.json").read_text())
            body = payload if payload is not None else object_page()
            raw = envelope("stale" if wrong_nonce else info["nonce"], info["action"], body,
                           model=model, busy=busy, same=same)
            path = directory / "query-receipt.json"
            if partial:
                path.write_text('{"List":')
                async def finish():
                    await asyncio.sleep(0.02)
                    path.write_text(json.dumps(raw))
                self.writers.append(asyncio.create_task(finish()))
            else:
                path.write_text(json.dumps(raw))
            return {"process_id": pid, "acknowledged": acknowledged,
                    "persistent_dde_session": True, "helper_process_id": 101}
        return call

    async def test_delayed_receipt_and_auto_pid_pinning_without_resend(self):
        result = await inspect_objects(self.root, limit=10, transport=self.transport(partial=True))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual([x["action"] for x in self.calls], ["probe", "execute_file"])
        self.assertEqual(self.calls[-1]["process_id"], 42)
        self.assertTrue(result["read_only"])
        self.assertEqual(len(result["engine_identity"]["sha256"]), 64)

    async def test_no_model_busy_connection_failure_and_no_resubmit(self):
        for args, reason in ((dict(model=False), "no_current_model"), (dict(busy=True), "simulation_running")):
            result = await inspect_objects(self.root, transport=self.transport(payload=[], **args))
            self.assertEqual((result["status"], result["reason"]), ("unavailable", reason))
        self.calls.clear()
        with self.assertRaisesRegex(RuntimeError, "DDE is unavailable"):
            await inspect_objects(self.root, transport=self.transport(connected=False))
        self.assertEqual(len(self.calls), 1)

    async def test_identity_ack_nonce_and_expected_model_fail_closed(self):
        cases = [(dict(pid=43), {}), (dict(wrong_nonce=True), {}), (dict(same=False), {}),
                 (dict(acknowledged=False), {}), ({}, dict(expected_model_name="different.lug"))]
        for fixture, arguments in cases:
            self.calls.clear()
            with self.subTest(fixture=fixture, arguments=arguments), self.assertRaises(RuntimeError):
                await inspect_objects(self.root, transport=self.transport(**fixture), **arguments)
            self.assertEqual(len(self.calls), 2)
        self.calls.clear()
        with self.assertRaisesRegex(RuntimeError, "selected Studio process"):
            await inspect_objects(self.root, process_id=999, transport=self.transport())
        self.assertEqual(len(self.calls), 1)

    async def test_invalid_arguments_never_contact_application(self):
        transport = AsyncMock()
        for args in (dict(object_type='Shape");system'), dict(limit=True), dict(offset=-1), dict(selected_only=1), dict(process_id=0)):
            with self.assertRaises(ValueError):
                await inspect_objects(self.root, transport=transport, **args)
        for args in (dict(type_name='X");'), dict(limit=1001), dict(filter_text=[])):
            with self.assertRaises(ValueError):
                await get_api_catalog(self.root, transport=transport, **args)
        with self.assertRaises(ValueError):
            await get_simulation_settings(self.root, timeout_seconds=True, transport=transport)
        transport.assert_not_called()

    async def test_catalog_works_without_a_current_model(self):
        result = await get_api_catalog(self.root, transport=self.transport(payload=[["/Base", "-Object"]], model=False))
        self.assertEqual(result["status"], "succeeded")
        self.assertFalse(result["has_current_model"])
        self.assertEqual(result["entries"][0]["name"], "Object")

    async def test_model_name_matches_existing_inspector_slash_normalization(self):
        result = await inspect_objects(self.root, expected_model_name='C:/models/灯"1.lug', transport=self.transport())
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["model_name"], 'C:\\models\\灯"1.lug')

    async def test_ack_without_receipt_and_cancellation_are_recorded_not_retried(self):
        base = self.transport()
        async def missing(directory, request, timeout_seconds):
            if request["action"] == "probe":
                return await base(directory, request, timeout_seconds)
            self.calls.append(request)
            return {"process_id": 42, "acknowledged": True}
        ticks = iter([0, 100])
        with patch("lucidshape_mcp.studio_queries.time", SimpleNamespace(monotonic=lambda: next(ticks))):
            with self.assertRaisesRegex(RuntimeError, "complete native receipt is missing"):
                await inspect_objects(self.root, transport=missing)
        self.assertEqual(len(self.calls), 2)
        self.calls.clear()
        async def cancelled(directory, request, timeout_seconds):
            if request["action"] == "probe":
                return await base(directory, request, timeout_seconds)
            self.calls.append(request)
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await inspect_objects(self.root, transport=cancelled)
        self.assertEqual(len(self.calls), 2)
        results = [json.loads(path.read_text()) for path in self.root.glob("studio-query-*/result.json")]
        self.assertEqual({row["status"] for row in results}, {"unverified", "interrupted"})


if __name__ == "__main__":
    unittest.main()
