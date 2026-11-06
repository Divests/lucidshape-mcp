"""Check the installed stdio service without starting or connecting CAD apps."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    with tempfile.TemporaryDirectory(prefix='lucidshape-mcp-smoke-') as folder:
        # Import from the installed package, even when invoked from a source tree.
        env = {**os.environ, 'LUCIDSHAPE_EXE': '', 'LUCIDSHAPE_WORK_DIR': folder}
        params = StdioServerParameters(
            command=sys.executable, args=['-m', 'lucidshape_mcp.server'],
            env=env, cwd=folder,
        )
        async with asyncio.timeout(30):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    names = sorted(t.name for t in (await client.list_tools()).tools)
                    response = await client.call_tool('get_capabilities', {})
                    if response.isError:
                        raise RuntimeError('Capability discovery failed')
                    capabilities = json.loads(response.content[0].text)
                    if capabilities['executable_exists']:
                        raise RuntimeError('Smoke test must run without an engine')
                    templates = Path(__import__('lucidshape_mcp').__file__).parent / 'templates'
                    if not (templates / 'healthcheck.do').is_file():
                        raise RuntimeError('Installed package is missing its native templates')
                    print(json.dumps({
                        'status': 'passed', 'tool_count': len(names),
                        'application_started': False, 'application_connected': False,
                        'templates_present': True, 'tools': names,
                    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
