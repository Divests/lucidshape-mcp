"""Documented local stdio configuration adapters; never edits client settings."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import json


@dataclass(frozen=True)
class Harness:
    title: str
    filename: str
    destination: str
    documentation: str


# Locations are instructions, not paths that the generator writes to.
HARNESSES = {
    'generic': Harness('Generic mcpServers JSON', 'mcp.local.json',
        'Merge into the client MCP configuration',
        'https://modelcontextprotocol.io/docs/develop/connect-local-servers'),
    'codex': Harness('Codex', 'codex.local.toml', '~/.codex/config.toml',
        'https://learn.chatgpt.com/docs/extend/mcp'),
    'claude-desktop': Harness('Claude Desktop', 'claude-desktop.local.json',
        '%APPDATA%/Claude/claude_desktop_config.json (Windows)',
        'https://modelcontextprotocol.io/docs/develop/connect-local-servers'),
    'claude-code': Harness('Claude Code', 'claude-code.local.json', 'Project .mcp.json',
        'https://code.claude.com/docs/en/mcp'),
    'cursor': Harness('Cursor', 'cursor.local.json', '~/.cursor/mcp.json or project .cursor/mcp.json',
        'https://prod.cursor.com/help/customization/mcp'),
    'vscode': Harness('VS Code local MCP client', 'vscode.local.json', 'Project .vscode/mcp.json',
        'https://code.visualstudio.com/docs/agents/reference/mcp-configuration'),
    'copilot-cli': Harness('GitHub Copilot CLI / portable Copilot configuration', 'copilot-cli.local.json',
        '~/.copilot/mcp-config.json (or COPILOT_HOME); portable project .mcp.json',
        'https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference'),
    'gemini-cli': Harness('Gemini CLI', 'gemini-cli.local.json', '~/.gemini/settings.json',
        'https://geminicli.com/docs/tools/mcp-server/'),
    'windsurf': Harness('Windsurf / Devin Desktop Cascade', 'windsurf.local.json',
        'Open the Cascade raw MCP config in settings; current Devin Windows path: %APPDATA%/devin/mcp_config.json',
        'https://docs.devin.ai/desktop/cascade/mcp'),
    'cline': Harness('Cline', 'cline.local.json', 'MCP Servers > Configure MCP Servers',
        'https://docs.cline.bot/mcp/mcp-overview'),
    'roo-code': Harness('Roo Code', 'roo-code.local.json', 'Project .roo/mcp.json or Edit Global MCP',
        'https://roocodeinc.github.io/Roo-Code/features/mcp/using-mcp-in-roo/'),
    'opencode': Harness('OpenCode', 'opencode.local.json', 'Project opencode.json / opencode.jsonc',
        'https://opencode.ai/docs/mcp-servers/'),
    'continue': Harness('Continue', 'continue.local.json', 'Project .continue/mcpServers/lucidshape.json',
        'https://docs.continue.dev/customize/deep-dives/mcp'),
    'kiro': Harness('Kiro local IDE / CLI', 'kiro.local.json', '~/.kiro/settings/mcp.json or project .kiro/settings/mcp.json',
        'https://kiro.dev/docs/mcp/configuration/'),
}
DEFAULT_HARNESSES = ('generic', 'codex')


def select_harnesses(names=None) -> tuple[str, ...]:
    names = tuple(DEFAULT_HARNESSES if names is None else names)
    if names == ('all',):
        return tuple(HARNESSES)
    if not names or any(name not in HARNESSES for name in names):
        raise ValueError('Choose known harness IDs; use all by itself')
    return tuple(dict.fromkeys(names))


def catalog() -> list[dict]:
    return [{'id': name, **asdict(profile), 'transport': 'stdio',
             'documentation_checked': '2026-10-27', 'client_application_tested': False}
            for name, profile in HARNESSES.items()]


def codex_toml(config: dict) -> str:
    server = config['mcpServers']['lucidshape']
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    lines = ['[mcp_servers.lucidshape]', 'command = ' + quote(server['command']),
             'args = ' + quote(server['args']), '', '[mcp_servers.lucidshape.env]']
    lines.extend(quote(key) + ' = ' + quote(value) for key, value in server['env'].items())
    return '\n'.join(lines) + '\n'


def render_config(config: dict, harness: str) -> str:
    """Translate launch fields only; do not relax any client's approval policy."""
    if harness not in HARNESSES:
        raise ValueError('Unknown harness: ' + harness)
    if harness == 'codex':
        return codex_toml(config)
    spec = deepcopy(config['mcpServers']['lucidshape'])
    if harness == 'vscode':
        payload = {'servers': {'lucidshape': {'type': 'stdio', **spec}}}
    elif harness == 'opencode':
        payload = {'$schema': 'https://opencode.ai/config.json', 'mcp': {'lucidshape': {
            'type': 'local', 'command': [spec['command'], *spec['args']],
            'environment': spec['env'], 'enabled': True,
        }}}
    else:
        if harness == 'claude-code':
            spec['type'] = 'stdio'
        elif harness == 'copilot-cli':
            # A visibility filter, NOT automatic approval of tool execution.
            spec.update(type='stdio', tools=['*'])
        elif harness == 'cline':
            spec.update(disabled=False, autoApprove=[])
        elif harness == 'roo-code':
            spec.update(disabled=False, alwaysAllow=[])
        elif harness == 'gemini-cli':
            spec['trust'] = False
        payload = {'mcpServers': {'lucidshape': spec}}
    return json.dumps(payload, ensure_ascii=False, indent=2) + '\n'


def config_outputs(config: dict, names=None) -> dict[str, str]:
    return {HARNESSES[name].filename: render_config(config, name)
            for name in select_harnesses(names)}
