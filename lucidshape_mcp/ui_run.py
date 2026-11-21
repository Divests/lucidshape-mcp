"""Run one installed declaration through its registered typed MCP adapter."""
import asyncio

from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult
from pydantic import ValidationError

from . import ui_action_catalog, ui_tools


async def run_action(server, engine, action_id, arguments, profile="studio"):
    if not isinstance(arguments, dict) or not all(isinstance(key, str) for key in arguments):
        raise ValueError("arguments must be a JSON object with named tool parameters")
    root = ui_tools.install_root(engine.command)
    row = await asyncio.to_thread(ui_action_catalog.get_action, root, action_id, profile=profile)
    binding = ui_tools.describe(row, engine.policy.enabled_tools, profile, root)["execution"]
    status = binding["status"]
    if status == "disabled_by_policy":
        raise PermissionError("The target tool for this UI action is disabled by server policy")
    if status != "available":
        raise ValueError(f"UI action {action_id} cannot run ({status}): "
                         + binding.get("reason", "no executable adapter for this declaration"))
    target = binding["tool"]
    if target == "studio_run_ui_action":
        raise ValueError("Recursive UI action bindings are invalid")
    tool = next((tool for tool in await server.list_tools() if tool.name == target), None)
    if tool is None:
        raise PermissionError("The target tool is not registered on this server")
    presets = binding["preset_arguments"]
    overlap = sorted(presets.keys() & arguments.keys())
    if overlap:
        raise ValueError("Catalog presets are fixed. Omit these arguments: " + ", ".join(overlap))
    all_args = {**arguments, **presets}
    unknown = sorted(all_args.keys() - tool.inputSchema.get("properties", {}).keys())
    if unknown:
        raise ValueError(f"Unknown arguments for {target}: " + ", ".join(unknown))
    missing = sorted(set(tool.inputSchema.get("required", [])) - all_args.keys())
    if missing:
        raise ValueError(f"Missing arguments for {target}: " + ", ".join(missing))
    try:
        # The registered target performs its normal schema validation and policy authorization.
        response = await server.call_tool(target, all_args)
    except ToolError as exc:
        if isinstance(exc.__cause__, ValidationError):
            raise ValueError(str(exc)) from exc
        raise RuntimeError(str(exc)) from exc
    if isinstance(response, CallToolResult):
        if response.isError:
            return response
        payload = response.structuredContent
    elif isinstance(response, tuple) and len(response) == 2:
        _, payload = response
    else:
        payload = response
    if not isinstance(payload, dict):
        raise RuntimeError(f"Target {target} returned no structured result")
    return payload
