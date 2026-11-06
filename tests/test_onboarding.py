import json
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from lucidshape_mcp import cli, onboarding
from lucidshape_mcp.config import executable_path


class OnboardingTests(unittest.TestCase):
    def test_generated_configs_roundtrip_unicode_spaces_and_virtual_environment(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder) / '下载 with spaces'
            root.mkdir()
            engine = root / 'lush.exe'
            engine.write_bytes(b'not executed')
            python = str(root / 'venv' / 'bin' / 'python')
            with patch.object(sys, 'executable', python):
                config = onboarding.client_config(str(engine), str(root / 'job files'))
            spec = config['mcpServers']['lucidshape']
            self.assertEqual(spec['command'], os.path.abspath(python))
            self.assertNotIn('PYTHONPATH', spec['env'])
            self.assertEqual(spec, tomllib.loads(onboarding.codex_toml(config))['mcp_servers']['lucidshape'])
            paths = onboarding.write_client_configs(config, root / 'config')
            self.assertEqual(json.loads(paths[0].read_text(encoding='utf-8')), config)

    def test_no_engine_configuration_is_explicit_and_inherited_values_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'LUCIDSHAPE_EXE': 'missing.exe'}):
            result = onboarding.client_config('', folder)
            self.assertEqual(result['mcpServers']['lucidshape']['env']['LUCIDSHAPE_EXE'], '')
            with self.assertRaisesRegex(ValueError, 'lush.exe'):
                onboarding.client_config(None, folder)

    def test_configuration_does_not_overwrite_existing_snippets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            existing = root / 'codex.local.toml'
            existing.write_text('unrelated settings', encoding='utf-8')
            config = onboarding.client_config('', str(root / 'jobs'))
            with self.assertRaises(FileExistsError):
                onboarding.write_client_configs(config, root)
            self.assertFalse((root / 'mcp.local.json').exists())
            self.assertEqual(existing.read_text(), 'unrelated settings')

    def test_environment_variables_in_engine_paths_are_expanded(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'LS_INSTALL_TEST': folder}):
            self.assertEqual(executable_path('$LS_INSTALL_TEST/lush.exe'), Path(folder).resolve() / 'lush.exe')

    def test_doctor_distinguishes_optional_dependencies_from_required_features(self):
        def probe(name):
            return {'import_ok': name == 'mcp', 'version': None, 'error': None}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            'LUCIDSHAPE_WORK_DIR': folder, 'LUCIDSHAPE_EXE': '',
        }, clear=True), patch.object(onboarding, 'probe_dependency', side_effect=probe):
            result = onboarding.doctor()
            self.assertEqual(result['status'], 'passed')
            self.assertFalse(result['native_execution_verified'])
            self.assertEqual(result['license_status'], 'not_checked')
            self.assertEqual(list(Path(folder).iterdir()), [])
            required = onboarding.doctor(('geometry', 'engine'))
            self.assertEqual(required['status'], 'failed')
            self.assertTrue(any('geometry' in message for message in required['errors']))

    def test_doctor_rejects_missing_core_sdk_even_when_optional_dependencies_exist(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            'LUCIDSHAPE_WORK_DIR': folder, 'LUCIDSHAPE_EXE': '',
        }, clear=True), patch.object(onboarding, 'probe_dependency', side_effect=lambda name: {'import_ok': name != 'mcp'}):
            result = onboarding.doctor()
            self.assertEqual(result['status'], 'failed')
            self.assertTrue(any('MCP SDK' in message for message in result['errors']))

    def test_doctor_does_not_treat_an_unknown_engine_as_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = Path(folder) / 'lush.exe'
            engine.write_bytes(b'unknown engine fixture')
            with patch.dict(os.environ, {'LUCIDSHAPE_WORK_DIR': folder, 'LUCIDSHAPE_EXE': str(engine)}, clear=True), \
                    patch.object(onboarding, 'probe_dependency', return_value={'import_ok': True}):
                result = onboarding.doctor(('extensions',))
                self.assertEqual(result['status'], 'failed')
                self.assertFalse(result['engine']['optical_extensions_profile_matches'])
                self.assertFalse(result['application_started'])

    def test_no_arguments_preserves_stdio_entry(self):
        with patch('lucidshape_mcp.server.main') as serve:
            self.assertEqual(cli.main([]), 0)
            serve.assert_called_once_with()

    def test_configure_persists_policy_for_all_client_adapters(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder)
            with contextlib.redirect_stdout(io.StringIO()):
                code = cli.main(['configure', '--output-dir', str(root / 'config'), '--harness', 'all',
                                 '--work-dir', str(root / 'jobs'), '--toolsets', 'analysis', '--read-only',
                                 '--allow-root', str(root)])
            self.assertEqual(code, 0)
            raw = json.loads((root / 'config/mcp.local.json').read_text(encoding='utf-8'))
            env = raw['mcpServers']['lucidshape']['env']
            self.assertEqual(env['LUCIDSHAPE_READ_ONLY'], 'true')
            self.assertEqual(env['LUCIDSHAPE_TOOLSETS'], 'analysis')
            self.assertEqual(env['LUCIDSHAPE_ALLOW_SOURCE_WRITES'], 'false')
            self.assertEqual(json.loads(env['LUCIDSHAPE_ALLOWED_ROOTS']), [str(root.resolve())])


if __name__ == '__main__':
    unittest.main()
