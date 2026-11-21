"""Connect installed Studio declarations to typed MCP tools and their actual scope."""
from collections import Counter
import json
from pathlib import Path
import uuid

from . import ui_action_catalog, ui_action_dispatch, ui_dialog_catalog, ui_geometry, ui_help


def install_root(command):
    if not command:
        raise ValueError("Set LUCIDSHAPE_EXE to your installed lush.exe")
    exe = Path(command[0]).resolve(strict=True)
    root = exe.parent.parent
    if not (root / "include" / "tasks" / "mainMenu.hdo").is_file():
        raise FileNotFoundError(f"Studio menu definitions not found beside {exe}")
    return root


# These controls can be reproduced on a saved input with explicit output settings.
# Fixed settings select a specific interpretation of a toggle on a fresh view.
_VIEW = {
    "CM_CANDELA": {"quantity": "candela"},
    "CM_LUX25": {"quantity": "illuminance_25m"},
    "CM_ONLY_ISO_LINES": {"display_mode": "contours"},
    "CM_ISO_LINES": {"show_contours": True},
    "CM_COLOUR_GRAY": {"display_mode": "grayscale"},
    "CM_LINEAR": {"scale_mode": "linear"},
}


def _binding(action):
    if ui_help.is_help_action(action):
        return "studio_get_help_resource", {"action_id": action["action_id"]}, "resolve the installed help PDF and check its signature"
    geometry = ui_geometry.menu_preset(action)
    if geometry is not None:
        return ui_geometry.OPERATION, geometry, "construct the selected primitive in a new model and reload its geometry"
    symbol = action["command_symbol"]
    callback = action["callback"]
    args = action["arguments"]
    if action["kind"] == "native":
        if symbol in ui_action_dispatch.NATIVE_METHODS:
            return "photometry_apply_ui_action", {"command": symbol}, "saved photometry to a new file"
        if symbol in _VIEW:
            return "studio_export_photometry_view", _VIEW[symbol], "set this display option on a private exported view"
        if symbol in ("CM_EDIT_BITMAP_COPY", "CM_FILEISAVEASBMP"):
            return "studio_export_photometry_view", {}, "photometry view only; saves an image file"
    if action["kind"] == "script":
        if callback == "load_valuesFromLid":
            return "measure_photometry_region", {}, "seven point, zone and flux modes on a saved Type A grid"
        if callback == "load_lidTestTable":
            return "measure_photometry_table", {}, "packaged LB/HB test tables on saved photometry"
        if callback == "general_openFile" and args in ([], ["0"]):
            return "studio_open_file", {}, "open a STEP, LUE or LUG snapshot in Studio"
        if callback == "load_scriptForDF2" and args and args[0] == '"Simulation/montecarlo_v0.luf"':
            return "simulate_model", {}, "Monte Carlo simulation on a saved model with the tool's settings"
        if callback == "doubleInvokeScriptFunction" and args and args[0] == '"/tasks/saveUVDataAsBMPDialog.do"':
            return "studio_export_photometry_view", {}, "native photometry bitmap exported to an image file"
    return None


def describe(action, enabled, profile="studio", root=None):
    binding = _binding(action)
    if binding is None:
        return {**action, "execution": {"status": "catalog_only"}}
    tool, defaults, scope = binding
    if tool not in enabled:
        return {**action, "execution": {"status": "disabled_by_policy"}}
    if tool == "studio_get_help_resource":
        if root is not None:
            doc = ui_help.installed_pdf(root, action)
            if doc["status"] != "available":
                return {**action, "execution": {"status": "unavailable_on_installation", "reason": doc["reason"]}}
        defaults = {**defaults, "profile": profile}
    return {**action, "execution": {"status": "available",
            "tool": tool, "preset_arguments": defaults, "scope": scope}}


def action_page(root, enabled, *, query="", offset=0, limit=50, profile="studio", executable_only=False):
    def eligible(action):
        return describe(action, enabled, profile, root)["execution"]["status"] == "available"
    page = ui_action_catalog.list_actions(root, query=query, offset=offset, limit=limit,
                                          profile=profile, eligible=eligible if executable_only else None)
    return {**page, "actions": [describe(row, enabled, profile, root) for row in page["actions"]]}


def export_catalog(root, work_dir, enabled, profile):
    actions, coverage = ui_action_catalog.action_inventory(root, profile=profile)
    controls, dialog_info = ui_dialog_catalog.dialog_inventory(root)
    actions = [describe(row, enabled, profile, root) for row in actions]
    summaries = Counter(row["execution"]["status"] for row in actions)
    summary = {"declarations": len(actions), "distinct_static_bindings": len({
        row["operation_key"] for row in actions if row["operation_key"]}),
        "menu_and_toolbar": coverage["by_surface"], "declarations_by_execution": dict(summaries),
        "declarations_by_tool": dict(Counter(row["execution"]["tool"] for row in actions
                                             if row["execution"]["status"] == "available")),
        "dialog_control_declarations": len(controls), "dialog_kind_counts": dialog_info["kind_counts"]}
    payload = {"schema_version": 1, "install_root": str(root), "profile": profile, "summary": summary,
               "menu_coverage": coverage, "dialog_coverage": dialog_info, "actions": actions, "controls": controls}
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / ("studio-ui-catalog-" + uuid.uuid4().hex + ".json")
    temp = path.with_suffix(".tmp")
    try:
        with temp.open("x", encoding="utf-8") as out:
            json.dump(payload, out, ensure_ascii=False, separators=(",", ":"))
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return {"status": "succeeded", "profile": profile, "catalog_path": str(path),
            "bytes": path.stat().st_size, "summary": summary}
