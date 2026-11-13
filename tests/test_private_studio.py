"""Native launcher lifecycle tests; no LucidShape process is started."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lucidshape_mcp import private_studio as studio


class PrivateStudioTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.executable = self.root / 'LucidShape.exe'
        self.executable.write_bytes(b'fake executable: never launched')
        self.directory = self.root / 'job'
        self.directory.mkdir()
        self.process = Mock()
        self.process.pid = 4242
        self.process.returncode = None
        self.process.poll.side_effect = lambda: self.process.returncode
        self.process.terminate.side_effect = self.terminate
        self.process.wait.side_effect = lambda timeout=None: self.process.returncode
        self.now = 0.0
        self.addCleanup(studio._PROCESSES.clear)
        studio._PROCESSES.clear()

    def terminate(self):
        self.process.returncode = 1

    async def expire(self, _seconds):
        self.now = 2.0

    async def run_job(self, launch=None, sleep=None):
        with patch.object(studio, 'studio_processes', return_value=[]), \
             patch.object(studio.subprocess, 'Popen', side_effect=launch or (lambda *args, **kwargs: self.process)) as popen, \
             patch.object(studio, 'time', SimpleNamespace(monotonic=lambda: self.now)), \
             patch.object(studio.asyncio, 'sleep', side_effect=sleep or self.expire):
            result = await studio.run_private_studio(
                self.executable, self.directory, 'void main(){}', 'native-receipt.json', 1)
        popen.assert_called_once()
        return result

    async def test_existing_studio_fails_preflight_without_launch_or_cleanup(self):
        existing = {'process_id': 7272, 'executable_name': 'LucidShape.exe'}
        with patch.object(studio, 'studio_processes', return_value=[existing]), \
             patch.object(studio.subprocess, 'Popen') as popen:
            result = await studio.run_private_studio(
                self.executable, self.directory, 'void main(){}', 'native-receipt.json', 1)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['error_details']['code'], 'STUDIO_ALREADY_RUNNING')
        self.assertFalse(result['private_process_started'])
        self.assertFalse(result['existing_processes_modified'])
        popen.assert_not_called()
        self.process.terminate.assert_not_called()
        self.assertFalse(studio._PROCESSES)

    async def test_inventory_error_does_not_start_speculative_process(self):
        with patch.object(studio, 'studio_processes', side_effect=OSError('inventory unavailable')), \
             patch.object(studio.subprocess, 'Popen') as popen:
            result = await studio.run_private_studio(
                self.executable, self.directory, 'void main(){}', 'native-receipt.json', 1)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['error_details']['code'], 'STUDIO_PREFLIGHT_FAILED')
        self.assertFalse(result['private_process_started'])
        popen.assert_not_called()

    async def test_timeout_without_native_progress_closes_only_owned_process(self):
        unrelated = Mock(pid=7272)
        studio._PROCESSES[unrelated.pid] = unrelated
        result = await self.run_job()
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['error_details']['code'], 'STUDIO_STARTUP_UNCONFIRMED')
        self.assertTrue(result['private_process_cleanup']['closed'])
        self.assertFalse(result['process_left_open'])
        self.assertFalse(result['automatic_resubmission_allowed'])
        self.process.terminate.assert_called_once()
        unrelated.terminate.assert_not_called()
        self.assertNotIn(self.process.pid, studio._PROCESSES)
        self.assertIs(studio._PROCESSES[unrelated.pid], unrelated)
        self.assertTrue((self.directory / 'native-job.lus').is_file())
        self.assertTrue((self.directory / 'launch.log').is_file())
        recorded = json.loads((self.directory / 'launch.json').read_text(encoding='utf-8'))
        self.assertEqual(recorded['error_details']['code'], 'STUDIO_STARTUP_UNCONFIRMED')
        self.assertFalse(recorded['process_left_open'])

    async def test_timeout_with_native_progress_distinguishes_export_incomplete(self):
        def launch(*args, **kwargs):
            (self.directory / 'native-progress.txt').write_text('entered main\nloaded input\n', encoding='utf-8')
            return self.process
        result = await self.run_job(launch=launch)
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['error_details']['code'], 'NATIVE_EXPORT_INCOMPLETE')
        self.assertTrue(result['private_process_cleanup']['closed'])
        self.process.terminate.assert_called_once()

    async def test_receipt_ready_retains_process_for_artifact_validation(self):
        def launch(*args, **kwargs):
            (self.directory / 'native-receipt.json').write_text(json.dumps(['native receipt']), encoding='utf-8')
            return self.process
        result = await self.run_job(launch=launch)
        self.assertEqual(result['status'], 'receipt_ready')
        self.assertTrue(result['process_left_open'])
        self.process.terminate.assert_not_called()
        self.assertIs(studio._PROCESSES[self.process.pid], self.process)
        cleanup = await studio.close_private_studio(result)
        self.assertTrue(cleanup['closed'])
        self.process.terminate.assert_called_once()
        self.assertNotIn(self.process.pid, studio._PROCESSES)

    async def test_incomplete_receipt_does_not_report_ready_or_retry(self):
        def launch(*args, **kwargs):
            (self.directory / 'native-receipt.json').write_text('{"partial":', encoding='utf-8')
            return self.process
        result = await self.run_job(launch=launch)
        self.assertEqual(result['status'], 'unverified')
        self.assertTrue(result['private_process_cleanup']['closed'])
        self.assertFalse(result['automatic_resubmission_allowed'])
        self.process.terminate.assert_called_once()

    async def test_cancel_cleans_owned_process_and_propagates_cancellation(self):
        async def cancel(_seconds):
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.run_job(sleep=cancel)
        self.process.terminate.assert_called_once()
        self.assertNotIn(self.process.pid, studio._PROCESSES)
        recorded = json.loads((self.directory / 'launch.json').read_text(encoding='utf-8'))
        self.assertEqual(recorded['status'], 'interrupted')
        self.assertFalse(recorded['process_left_open'])
        self.assertTrue(recorded['private_process_cleanup']['closed'])

    async def test_preexisting_receipt_is_rejected_before_launch(self):
        (self.directory / 'native-receipt.json').write_text('[]', encoding='utf-8')
        with patch.object(studio, 'studio_processes', return_value=[]), \
             patch.object(studio.subprocess, 'Popen') as popen:
            with self.assertRaises(ValueError):
                await studio.run_private_studio(
                    self.executable, self.directory, 'void main(){}', 'native-receipt.json', 1)
        popen.assert_not_called()

    async def test_preexisting_progress_is_rejected_before_launch(self):
        (self.directory / 'native-progress.txt').write_text('entered main', encoding='utf-8')
        with patch.object(studio, 'studio_processes', return_value=[]), \
             patch.object(studio.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(ValueError, 'refuse replay'):
                await studio.run_private_studio(
                    self.executable, self.directory, 'void main(){}', 'native-receipt.json', 1)
        popen.assert_not_called()

    async def test_launch_error_is_distinct_from_native_failure(self):
        with patch.object(studio, 'studio_processes', return_value=[]), \
             patch.object(studio.subprocess, 'Popen', side_effect=OSError('cannot create process')) as popen:
            result = await studio.run_private_studio(
                self.executable, self.directory, 'void main(){}', 'native-receipt.json', 1)
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['error_details']['code'], 'STUDIO_LAUNCH_FAILED')
        self.assertFalse(result['private_process_started'])
        self.assertFalse(result['process_left_open'])
        popen.assert_called_once()
        self.assertFalse(studio._PROCESSES)

    async def test_early_exit_does_not_terminate_or_reuse_pid(self):
        self.process.returncode = 9
        result = await self.run_job()
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['error_details']['code'], 'STUDIO_EXITED')
        self.assertEqual(result['exit_code'], 9)
        self.assertFalse(result['process_left_open'])
        self.process.terminate.assert_not_called()
        self.assertNotIn(self.process.pid, studio._PROCESSES)

    async def test_initial_launch_record_failure_cleans_process_and_persists_error(self):
        original_write = studio._write_launch
        calls = 0
        def fail_once(directory, result):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError('temporary launch record write failure')
            return original_write(directory, result)
        with patch.object(studio, '_write_launch', side_effect=fail_once):
            result = await self.run_job()
        self.assertEqual(calls, 2)
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['error_details']['code'], 'EXPORT_OBSERVATION_FAILED')
        self.assertTrue(result['private_process_cleanup']['closed'])
        self.assertFalse(result['process_left_open'])
        self.process.terminate.assert_called_once()
        self.assertNotIn(self.process.pid, studio._PROCESSES)
        recorded = json.loads((self.directory / 'launch.json').read_text(encoding='utf-8'))
        self.assertEqual(recorded['error_details']['code'], 'EXPORT_OBSERVATION_FAILED')
        self.assertTrue(recorded['private_process_cleanup']['closed'])
        self.assertFalse(recorded['process_left_open'])

    async def test_arbitrary_pid_cannot_be_closed(self):
        result = await studio.close_private_studio({'launcher_pid': 1010, 'private_process_started': True})
        self.assertFalse(result['closed'])
        self.process.terminate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
