import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from lucidshape_mcp.config import (
    configure_geometry_runtime, geometry_available, geometry_runtime_path, work_directory,
)


class ConfigTests(unittest.TestCase):
    def test_default_jobs_are_in_user_directory(self):
        with patch.dict(os.environ, {}, clear=True), patch('pathlib.Path.home', return_value=Path('/test-user')):
            self.assertEqual(work_directory(), Path('/test-user/.lucidshape-mcp/work'))

    def test_explicit_work_directory_preserves_existing_configuration(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'LUCIDSHAPE_WORK_DIR':folder}):
            self.assertEqual(work_directory(), Path(folder).resolve())

    def test_explicit_kernel_path_must_contain_ocp_and_is_used_by_worker(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'LUCIDSHAPE_KERNEL_PATH':folder}), patch.object(sys, 'path', list(sys.path)):
            self.assertEqual(geometry_runtime_path(), Path(folder).resolve())
            self.assertFalse(geometry_available())
            with self.assertRaisesRegex(RuntimeError, 'OCP'):
                configure_geometry_runtime()
            (Path(folder)/'OCP').mkdir()
            self.assertTrue(geometry_available())
            configure_geometry_runtime()
            self.assertEqual(sys.path[0], str(Path(folder).resolve()))

    def test_installed_geometry_dependency_is_detected_without_local_runtime(self):
        with patch('lucidshape_mcp.config.geometry_runtime_path', return_value=None), patch('lucidshape_mcp.config.importlib.util.find_spec', return_value=object()) as lookup:
            self.assertTrue(geometry_available())
            lookup.assert_called_once_with('OCP')

    def test_local_data_geometry_runtime_precedes_legacy_locations(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder)
            module = root / 'lucidshape_mcp' / 'config.py'
            relocated = root / 'local-data' / 'work' / 'cad-kernel-runtime'
            former = root / 'workspace' / 'work' / 'cad-kernel-runtime'
            legacy = root / 'work' / 'cad-kernel-runtime'
            (legacy / 'OCP').mkdir(parents=True)
            with patch('lucidshape_mcp.config.__file__', str(module)):
                self.assertEqual(geometry_runtime_path(), legacy.resolve())
                (former / 'OCP').mkdir(parents=True)
                self.assertEqual(geometry_runtime_path(), former.resolve())
                (relocated / 'OCP').mkdir(parents=True)
                self.assertEqual(geometry_runtime_path(), relocated.resolve())
