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


class FakeWindowTextReader:
    def __init__(self, windows=None, children=None):
        self.windows = windows or {}
        self.children = children or {}
        self.title_reads = []
        self.static_reads = []

    def enumerate(self, callback, parent=None):
        handles = self.children.get(parent, []) if parent is not None else self.windows
        for hwnd in handles:
            if not callback(hwnd):
                break

    def pid(self, hwnd):
        return self.windows[hwnd]['pid']

    def class_name(self, hwnd):
        return self.windows[hwnd]['class']

    def title(self, hwnd):
        self.title_reads.append(hwnd)
        return self.windows[hwnd].get('title', '')

    def visible(self, hwnd):
        return True

    def static_text(self, hwnd):
        self.static_reads.append(hwnd)
        return self.windows[hwnd].get('text')


class OwnedWindowDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.process = Mock(pid=4242)
        self.process.poll.return_value = None
        studio._PROCESSES[4242] = self.process
        self.addCleanup(studio._PROCESSES.clear)

    def test_only_owned_dialog_static_text_is_read(self):
        api = FakeWindowTextReader({
            1: {'pid': 7777, 'class': '#32770', 'title': 'Unrelated private window'},
            2: {'pid': 4242, 'class': 'Main', 'title': 'LucidShape'},
            3: {'pid': 4242, 'class': '#32770', 'title': 'No connection'},
            4: {'pid': 4242, 'class': 'Static', 'text': 'Update check failed'},
            5: {'pid': 4242, 'class': 'Edit', 'text': 'Must not read'},
            6: {'pid': 7777, 'class': 'Static', 'text': 'Other process'},
            7: {'pid': 4242, 'class': 'Button', 'text': 'Do not click'},
            8: {'pid': 4242, 'class': 'Static', 'text': None},
        }, {3: [4, 5, 6, 7, 8]})
        # EnumWindows returns top-level handles only.
        def enumerate_windows(callback, parent=None):
            for hwnd in ([1, 2, 3] if parent is None else api.children.get(parent, [])):
                if not callback(hwnd):
                    break
        api.enumerate = enumerate_windows
        with patch.object(studio, '_WindowTextReader', return_value=api):
            result = studio._owned_window_diagnostics(self.process)
        self.assertEqual(api.title_reads, [2, 3])
        self.assertEqual(api.static_reads, [4, 8])
        self.assertEqual(result['windows'][1]['static_text'], ['Update check failed'])
        self.assertEqual(result['windows'][1]['static_text_query_timeouts'], 1)
        self.assertEqual(result['status'], 'observed')
        self.process.terminate.assert_not_called()

    def test_no_owned_live_handle_never_enumerates(self):
        with patch.object(studio, '_WindowTextReader') as reader:
            studio._PROCESSES.clear()
            self.assertEqual(studio._owned_window_diagnostics(self.process)['status'], 'skipped')
            studio._PROCESSES[4242] = self.process
            self.process.poll.return_value = 1
            self.assertEqual(studio._owned_window_diagnostics(self.process)['status'], 'skipped')
        reader.assert_not_called()

    def test_limits_bound_text_reads_and_length(self):
        windows = {1: {'pid': 4242, 'class': '#32770', 'title': 'x' * 1000}}
        windows.update({i: {'pid': 4242, 'class': 'Static', 'text': 'y' * 1000} for i in range(2, 102)})
        api = FakeWindowTextReader(windows, {1: list(range(2, 102))})
        with patch.object(studio, '_WindowTextReader', return_value=api):
            result = studio._owned_window_diagnostics(self.process)
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(result['windows']), 32)
        self.assertEqual(len(api.static_reads), 32)
        self.assertEqual(len(result['windows'][0]['title']), 512)
        self.assertTrue(all(len(t) == 512 for t in result['windows'][0]['static_text']))

    def test_unavailable_api_does_not_prevent_cleanup(self):
        with patch.object(studio, '_WindowTextReader', side_effect=OSError('API unavailable')):
            result = studio._owned_window_diagnostics(self.process)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('API unavailable', result['reason'])
        self.process.terminate.assert_not_called()


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
             patch.object(studio.asyncio, 'sleep', side_effect=sleep or self.expire), \
             patch.object(studio, '_WindowTextReader', return_value=FakeWindowTextReader()):
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
        self.assertEqual(recorded['owned_window_diagnostics'], result['owned_window_diagnostics'])
        self.assertEqual(result['owned_window_diagnostics']['status'], 'observed')
        self.assertTrue(result['owned_window_diagnostics']['read_only'])

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
