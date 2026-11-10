"""Ownership and MCP boundary regressions; fixtures never launch CAD software."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from lucidshape_mcp.coordination import FileLease, lease_held
from lucidshape_mcp.engine import BatchEngine
from lucidshape_mcp.inputs import input_file, snapshot
from lucidshape_mcp.job_control import atomic_json
from lucidshape_mcp.policy import ACTIVE_POLICY, ALL_TOOLS, Policy, READ_ONLY
from lucidshape_mcp.server import create_server

FIXTURE = Path(__file__).with_name('mock_engine.py').resolve()
DEFAULT_ENV = {'LUCIDSHAPE_TOOLSETS': 'core,studio,catia,geometry,analysis,simulation',
               'LUCIDSHAPE_TOOLS': '', 'LUCIDSHAPE_READ_ONLY': 'false',
               'LUCIDSHAPE_ALLOWED_ROOTS': '[]', 'LUCIDSHAPE_ALLOW_SOURCE_WRITES': 'false'}


class JobOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, DEFAULT_ENV)
        self.env.start()
        self.temporary = tempfile.TemporaryDirectory(prefix='mcp ownership ')
        self.root = Path(self.temporary.name)
        self.owner = BatchEngine(self.root, (sys.executable, str(FIXTURE), 'timeout'))
        self.observer = BatchEngine(self.root)

    async def asyncTearDown(self):
        await self.owner.close()
        await self.observer.close()
        self.temporary.cleanup()
        self.env.stop()

    async def wait_status(self, job_id, statuses):
        async with asyncio.timeout(5):
            while True:
                result = self.owner.get_job(job_id)
                if result['status'] in statuses:
                    return result
                await asyncio.sleep(.02)

    async def test_foreign_observer_does_not_mark_live_queued_job_interrupted(self):
        job = await self.owner.submit('healthcheck')
        path = self.root / job['job_id'] / 'job.json'
        before = path.read_bytes()
        observed = self.observer.get_job(job['job_id'])
        self.assertEqual(observed['status'], 'queued')
        self.assertTrue(observed['owner_active'])
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(observed['owner']['instance_id'], self.owner.instance_id)

    async def test_foreign_cancel_is_processed_by_owner(self):
        job = await self.owner.submit('healthcheck')
        await self.wait_status(job['job_id'], {'running'})
        response = self.observer.cancel_job(job['job_id'])
        self.assertTrue(response['cancel_requested'])
        result = await self.wait_status(job['job_id'], {'cancelled'})
        await asyncio.sleep(0)
        self.assertEqual(result['error_details']['code'], 'JOB_CANCELLED')
        self.assertFalse(lease_held(self.root / job['job_id'] / 'owner.lock'))
        self.assertFalse(self.observer.cancel_job(job['job_id'])['cancel_requested'])

    async def test_cancel_queued_job_does_not_cancel_running_job(self):
        first = await self.owner.submit('healthcheck')
        await self.wait_status(first['job_id'], {'running'})
        second = await self.owner.submit('healthcheck')
        self.observer.cancel_job(second['job_id'])
        await self.wait_status(second['job_id'], {'cancelled'})
        self.assertEqual(self.owner.get_job(first['job_id'])['status'], 'running')
        self.assertFalse((self.root / second['job_id'] / 'engine.log').exists())

    async def test_wrong_owner_and_malformed_requests_do_not_stop_worker(self):
        job = await self.owner.submit('healthcheck')
        await self.wait_status(job['job_id'], {'running'})
        request = self.root / job['job_id'] / 'cancel.json'
        request.write_text('invalid json')
        await asyncio.sleep(.3)
        atomic_json(request, {'job_id': job['job_id'], 'owner_instance_id': self.observer.instance_id})
        await asyncio.sleep(.3)
        self.assertEqual(self.owner.get_job(job['job_id'])['status'], 'running')
        self.observer.cancel_job(job['job_id'])
        await self.wait_status(job['job_id'], {'cancelled'})

    async def test_stale_observation_never_rewrites_disk_or_kills_process(self):
        job = await self.owner.submit('healthcheck')
        await self.owner.close()
        path = self.root / job['job_id'] / 'job.json'
        record = json.loads(path.read_text())
        record['status'] = 'running'
        atomic_json(path, record)
        before = path.read_bytes()
        result = self.observer.get_job(job['job_id'])
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(result['recorded_status'], 'running')
        self.assertFalse(self.observer.cancel_job(job['job_id'])['cancel_requested'])
        self.assertEqual(before, path.read_bytes())

    async def test_heartbeat_and_pagination(self):
        job = await self.owner.submit('healthcheck')
        first = await self.wait_status(job['job_id'], {'running'})
        await asyncio.sleep(2.2)
        second = self.observer.get_job(job['job_id'])
        self.assertGreater(second['heartbeat_at'], first['heartbeat_at'])
        self.assertGreater(second['elapsed_seconds'], 2)
        self.assertEqual(second['phase'], 'running')
        self.assertEqual(second['poll_interval_ms'], 1000)
        await self.owner.submit('healthcheck')
        page = self.observer.list_jobs(limit=1)
        self.assertEqual(page['total'], 2)
        self.assertEqual(len(page['jobs']), 1)
        self.assertEqual(len(self.observer.list_jobs(offset=2)['jobs']), 0)
        self.assertEqual(self.observer.list_jobs(status='running')['total'], 1)
        with self.assertRaises(ValueError):
            self.observer.list_jobs(limit=201)

    async def test_native_gate_serializes_different_work_directories(self):
        other = BatchEngine(self.root / 'another')
        entered = asyncio.Event()
        async def contender():
            async with other.native_session():
                entered.set()
        try:
            async with self.owner.native_session():
                task = asyncio.create_task(contender())
                await asyncio.sleep(.15)
                self.assertFalse(entered.is_set())
            await asyncio.wait_for(task, 2)
            self.assertTrue(entered.is_set())
        finally:
            await other.close()

    async def test_cancel_during_launch_waits_for_worker_exit_before_unlock(self):
        launched = asyncio.Event()
        exited = asyncio.Event()
        class Process:
            returncode = None
            def kill(self):
                self.returncode = -1
            async def wait(self):
                await asyncio.sleep(.05)
                exited.set()
        process = Process()
        async def slow_launch(*args, **kwargs):
            launched.set()
            await asyncio.sleep(.1)
            return process
        with patch('lucidshape_mcp.engine.asyncio.create_subprocess_exec', side_effect=slow_launch):
            job = await self.owner.submit('healthcheck')
            await asyncio.wait_for(launched.wait(), 2)
            task = self.owner._tasks[job['job_id']]
            task.cancel()
            async with self.observer.native_session():
                self.assertTrue(exited.is_set())
                self.assertEqual(process.returncode, -1)
            await asyncio.gather(task, return_exceptions=True)

    async def test_lease_is_cross_process_and_released_on_exit(self):
        path = self.root / 'cross-process.lock'
        code = ('import sys; from pathlib import Path; from lucidshape_mcp.coordination import FileLease; '
                'lease=FileLease(Path(sys.argv[1])); print(lease.acquire(),flush=True); sys.stdin.readline()')
        child = await asyncio.create_subprocess_exec(sys.executable, '-B', '-c', code, str(path),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    cwd=Path(__file__).resolve().parents[1])
        try:
            self.assertEqual((await asyncio.wait_for(child.stdout.readline(), 5)).strip(), b'True')
            self.assertTrue(lease_held(path))
            child.stdin.write(b'finish\n')
            await child.stdin.drain()
            await asyncio.wait_for(child.wait(), 5)
            self.assertFalse(lease_held(path))
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()


class PolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_job_and_access_error_keep_structured_contract(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, DEFAULT_ENV):
            engine = BatchEngine(Path(d), (sys.executable, str(FIXTURE), 'ok'))
            server = create_server(engine)
            try:
                _, submitted = await server.call_tool('health_check', {})
                self.assertEqual(submitted['status'], 'queued')
                self.assertEqual(submitted['schema_version'], '1.0')
                await asyncio.gather(*engine._tasks.values())
                _, completed = await server.call_tool('get_job', {'job_id': submitted['job_id']})
                self.assertEqual(completed['status'], 'succeeded')
                self.assertGreater(completed['artifacts'][0]['bytes'], 0)
                denied = await server.call_tool('catia_show_all', {'catia_path': 'irrelevant'})
                self.assertTrue(denied.isError)
                self.assertEqual(denied.structuredContent['error_details']['code'], 'ACCESS_DENIED')
            finally:
                await engine.close()

    async def test_toolsets_and_all_schemas(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, DEFAULT_ENV):
            engine = BatchEngine(Path(d))
            default = await create_server(engine).list_tools()
            self.assertEqual(len(default), 38)
            self.assertNotIn('trace_s9_gpu_control', {t.name for t in default})
            with patch.dict(os.environ, {'LUCIDSHAPE_TOOLSETS': 'all'}):
                engine.policy = Policy.from_env(engine.root)
                server = create_server(engine)
                all_tools = await server.list_tools()
                self.assertEqual({t.name for t in all_tools}, ALL_TOOLS)
                self.assertEqual(len(all_tools), 55)
                for tool in all_tools:
                    self.assertIsNotNone(tool.outputSchema, tool.name)
                    self.assertIsNotNone(tool.annotations, tool.name)
                schema = next(t.inputSchema for t in all_tools if t.name == 'submit_demo_simulation')
                self.assertEqual(schema['properties']['rays']['minimum'], 1)
            await engine.close()

    async def test_readonly_rejects_hidden_mutation_even_when_called_by_name(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, **DEFAULT_ENV, 'LUCIDSHAPE_READ_ONLY': 'true',
                   'LUCIDSHAPE_WORK_DIR': d, 'LUCIDSHAPE_EXE': ''}
            params = StdioServerParameters(command=sys.executable, args=['-B', '-m', 'lucidshape_mcp'], env=env)
            async with asyncio.timeout(20):
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        self.assertEqual({t.name for t in (await session.list_tools()).tools}, READ_ONLY)
                        self.assertTrue((await session.call_tool('catia_show_all', {'catia_path': 'not used'})).isError)
                        error = await session.call_tool('get_job', {'job_id': 'invalid'})
                        self.assertTrue(error.isError)
                        self.assertEqual(error.structuredContent['error_details']['code'], 'INVALID_ARGUMENT')
                        jobs = await session.call_tool('list_jobs', {})
                        self.assertEqual(jobs.structuredContent['total'], 0)
                        invalid = await session.call_tool('list_jobs', {'limit': 0})
                        self.assertTrue(invalid.isError)

    async def test_explicit_tool_selection_and_source_write_policy(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, DEFAULT_ENV):
            with patch.dict(os.environ, {'LUCIDSHAPE_TOOLS': 'inspect_rayfile'}):
                policy = Policy.from_env(Path(d))
                self.assertIn('inspect_rayfile', policy.enabled_tools)
                self.assertNotIn('simulate_model', policy.enabled_tools)
                self.assertIn('get_job', policy.enabled_tools)
            policy = Policy.from_env(Path(d))
            with self.assertRaises(PermissionError):
                policy.authorize('catia_show_all', {})
            with patch.dict(os.environ, {'LUCIDSHAPE_ALLOW_SOURCE_WRITES': 'true'}):
                Policy.from_env(Path(d)).authorize('catia_show_all', {})

    async def test_roots_cover_recursive_assets_and_resolved_links(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); allowed = root / 'allowed'; allowed.mkdir()
            work = root / 'work'; work.mkdir()
            secret = root / 'outside.ray'; secret.write_bytes(b'private')
            model = allowed / 'model.lue'
            model.write_text('@1=RayFile("source",' + json.dumps(str(secret)) + ');')
            policy = Policy(work, allowed_roots=(allowed,))
            token = ACTIVE_POLICY.set(policy)
            try:
                with self.assertRaises(PermissionError):
                    input_file(str(secret), {'.ray'})
                with self.assertRaises(PermissionError):
                    snapshot(model, work, 'input.lue')
                self.assertFalse((work / 'input.lue').exists())
                self.assertEqual(input_file(str(model), {'.lue'}), model.resolve())
                link = allowed / 'linked.ray'
                try:
                    link.symlink_to(secret)
                except OSError:
                    pass  # Windows may prohibit creating symbolic links for this user.
                else:
                    with self.assertRaises(PermissionError):
                        input_file(str(link), {'.ray'})
            finally:
                ACTIVE_POLICY.reset(token)

    async def test_direct_policy_normalizes_roots_without_allowing_outside_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            allowed = root / 'allowed'; allowed.mkdir()
            work = root / 'work'; work.mkdir()
            model = allowed / 'model.lue'; model.write_bytes(b'model')
            result = work / 'result.lid'; result.write_bytes(b'result')
            outside = root / 'outside.ray'; outside.write_bytes(b'outside')
            policy = Policy(work / '..' / 'work', allowed_roots=(allowed / '..' / 'allowed',))
            self.assertEqual(policy.allowed_roots, (allowed,))
            self.assertEqual(policy.work_dir, work)
            self.assertEqual(policy.check_path(model), model)
            self.assertEqual(policy.check_path(result), result)
            with self.assertRaises(PermissionError):
                policy.check_path(outside)

    @unittest.skipUnless(os.name == 'nt', 'Windows short path regression')
    async def test_direct_policy_accepts_windows_short_path_roots(self):
        import ctypes
        with tempfile.TemporaryDirectory(prefix='mcp policy long path ') as d:
            root = Path(d).resolve()
            buffer = ctypes.create_unicode_buffer(32768)
            size = ctypes.windll.kernel32.GetShortPathNameW(str(root), buffer, len(buffer))
            self.assertGreater(size, 0)
            self.assertLess(size, len(buffer))
            short = Path(buffer.value)
            if str(short).lower() == str(root).lower():
                self.skipTest('Volume does not provide an alternate short path')
            model = root / 'model.lue'; model.write_bytes(b'model')
            policy = Policy(short / 'work', allowed_roots=(short,))
            self.assertEqual(policy.check_path(short / model.name), model)
            self.assertEqual(policy.check_path(model), model)

    async def test_invalid_policy_fails_before_registration(self):
        for values in ({'LUCIDSHAPE_TOOLSETS': 'unknown'}, {'LUCIDSHAPE_TOOLS': 'no_tool'},
                       {'LUCIDSHAPE_READ_ONLY': 'maybe'}, {'LUCIDSHAPE_ALLOWED_ROOTS': '["relative"]'}):
            with patch.dict(os.environ, {**DEFAULT_ENV, **values}):
                with self.assertRaises(ValueError):
                    Policy.from_env(Path.cwd())


if __name__ == '__main__':
    unittest.main()
