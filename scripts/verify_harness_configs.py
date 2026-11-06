"""Parse each generated client config and verify its launch over real MCP stdio.

This uses the MCP SDK, NOT the client applications. It does not certify their UI,
approval policies, sandbox or version-specific behavior. No CAD app is launched.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import tempfile
import tomllib

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from lucidshape_mcp.harnesses import HARNESSES
from lucidshape_mcp.onboarding import client_config, write_client_configs


async def main(config_dir=None):
    results = []
    with tempfile.TemporaryDirectory(prefix='lucidshape-harness-') as folder:
        root = Path(folder)
        if config_dir is None:
            config_dir = root / '配置 with spaces'
            write_client_configs(client_config('', str(root / 'jobs')), config_dir, harnesses=['all'])
        for name, profile in HARNESSES.items():
            raw = (config_dir / profile.filename).read_text(encoding='utf-8')
            if name == 'codex':
                spec = tomllib.loads(raw)['mcp_servers']['lucidshape']
            else:
                data = json.loads(raw)
                if name == 'opencode':
                    entry = data['mcp']['lucidshape']
                    assert entry['type'] == 'local' and entry['enabled'] is True
                    spec = {'command': entry['command'][0], 'args': entry['command'][1:],
                            'env': entry['environment']}
                elif name == 'vscode':
                    spec = data['servers']['lucidshape']
                    assert spec['type'] == 'stdio'
                else:
                    spec = data['mcpServers']['lucidshape']
            if spec['env'].get('LUCIDSHAPE_EXE') != '':
                raise RuntimeError('This check only accepts configs with an explicitly empty native engine')
            params = StdioServerParameters(command=spec['command'], args=spec['args'],
                env={**os.environ, **spec['env'], 'PYTHONDONTWRITEBYTECODE': '1'}, cwd=folder)
            async with asyncio.timeout(30):
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = (await session.list_tools()).tools
                        names = {tool.name for tool in tools}
                        if 'get_capabilities' not in names or not all(t.outputSchema and t.annotations for t in tools):
                            raise RuntimeError('Tool discovery mismatch for ' + name)
                        response = await session.call_tool('get_capabilities', {})
                        if response.isError or json.loads(response.content[0].text)['executable_exists']:
                            raise RuntimeError('Capability check failed for ' + name)
                        if names != set(response.structuredContent['policy']['enabled_tools']):
                            raise RuntimeError('Configured policy mismatch for ' + name)
            results.append({'harness': name, 'tool_count': len(names), 'status': 'passed'})
        print(json.dumps({'status': 'passed', 'configurations': results,
                          'client_applications_tested': False, 'cad_applications_started': False}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-dir', type=Path)
    asyncio.run(main(parser.parse_args().config_dir))
