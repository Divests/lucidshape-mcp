"""Configuration contracts from the client docs, independent of running CAD."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import tomllib
import unittest

from lucidshape_mcp.harnesses import HARNESSES, catalog, config_outputs, render_config, select_harnesses
from lucidshape_mcp.onboarding import write_client_configs


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.spec = {
            'command': 'C:/工具 with spaces/python.exe', 'args': ['-m', 'lucidshape_mcp'],
            'env': {'LUCIDSHAPE_EXE': 'C:/光学 & CAD/BIN64GPU/lush.exe',
                    'LUCIDSHAPE_WORK_DIR': 'D:/任务 文件', 'TEST_QUOTE': 'a"b\\c'},
        }
        self.config = {'mcpServers': {'lucidshape': deepcopy(self.spec)}}

    def test_basic_json_clients_preserve_all_launch_fields(self):
        for name in ('generic', 'claude-desktop', 'cursor', 'windsurf', 'continue', 'kiro'):
            with self.subTest(harness=name):
                self.assertEqual(json.loads(render_config(self.config, name)), self.config)

    def test_codex_toml_roundtrip_does_not_lose_unicode_or_escaping(self):
        config = tomllib.loads(render_config(self.config, 'codex'))
        self.assertEqual(config, {'mcp_servers': {'lucidshape': self.spec}})

    def test_vscode_requires_servers_and_stdio_type(self):
        config = json.loads(render_config(self.config, 'vscode'))
        self.assertEqual(config, {'servers': {'lucidshape': {'type': 'stdio', **self.spec}}})

    def test_opencode_uses_command_array_and_environment(self):
        config = json.loads(render_config(self.config, 'opencode'))
        self.assertEqual(config['mcp']['lucidshape'], {
            'type': 'local', 'command': [self.spec['command'], '-m', 'lucidshape_mcp'],
            'environment': self.spec['env'], 'enabled': True,
        })
        self.assertEqual(config['$schema'], 'https://opencode.ai/config.json')

    def test_copilot_tool_visibility_does_not_grant_execution_permission(self):
        config = json.loads(render_config(self.config, 'copilot-cli'))
        self.assertEqual(config, {'mcpServers': {'lucidshape': {
            **self.spec, 'type': 'stdio', 'tools': ['*'],
        }}})

    def test_approval_lists_stay_empty_and_gemini_is_not_trusted(self):
        additions = {
            'claude-code': {'type': 'stdio'},
            'cline': {'disabled': False, 'autoApprove': []},
            'roo-code': {'disabled': False, 'alwaysAllow': []},
            'gemini-cli': {'trust': False},
        }
        for name, fields in additions.items():
            with self.subTest(harness=name):
                self.assertEqual(json.loads(render_config(self.config, name)),
                                 {'mcpServers': {'lucidshape': {**self.spec, **fields}}})

    def test_rendering_all_profiles_never_mutates_shared_launch_data(self):
        before = deepcopy(self.config)
        outputs = config_outputs(self.config, ['all'])
        self.assertEqual(self.config, before)
        self.assertEqual(len(outputs), 14)
        self.assertEqual(len(outputs), len(HARNESSES))
        self.assertTrue(all(Path(name).name == name and '.local.' in name for name in outputs))

    def test_selection_defaults_deduplication_and_rejection(self):
        self.assertEqual(select_harnesses(), ('generic', 'codex'))
        self.assertEqual(select_harnesses(['cursor', 'cursor', 'codex']), ('cursor', 'codex'))
        for invalid in ([], ['all', 'codex'], ['../client'], ['remote-http']):
            with self.assertRaises(ValueError):
                select_harnesses(invalid)

    def test_selected_outputs_leave_other_existing_files_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            preserved = root / 'codex.local.toml'
            preserved.write_text('existing settings', encoding='utf-8')
            paths = write_client_configs(self.config, root, harnesses=['vscode', 'opencode'])
            self.assertEqual({p.name for p in paths}, {'vscode.local.json', 'opencode.local.json'})
            self.assertEqual(preserved.read_text(), 'existing settings')
            with self.assertRaises(FileExistsError):
                write_client_configs(self.config, root, harnesses=['all'])
            self.assertFalse((root / 'mcp.local.json').exists())

    def test_catalog_discloses_documentation_scope_and_test_limits(self):
        entries = catalog()
        self.assertEqual({x['id'] for x in entries}, set(HARNESSES))
        self.assertTrue(all(x['documentation'].startswith('https://') for x in entries))
        self.assertTrue(all(x['transport'] == 'stdio' and not x['client_application_tested'] for x in entries))


if __name__ == '__main__':
    unittest.main()
