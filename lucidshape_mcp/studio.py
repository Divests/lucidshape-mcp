"""Open a model in LucidStudio using a fixed startup script and native API receipt."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid

from .inputs import input_file, snapshot, decode_text
from .results import count, number, sequence


async def open_file(source_path: str, executable: Path, root: Path, timeout_seconds: int = 120) -> dict:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 600:
        raise ValueError("timeout_seconds must be an integer from 1 to 600")
    source = input_file(source_path, {".stp", ".step", ".lue", ".lug"})
    if not executable.is_file():
        raise ValueError("LucidShape.exe was not found next to the configured lush.exe")
    directory = root.resolve() / ("studio-" + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    # Fixed ASCII names keep generated script literals independent of user-supplied filenames.
    staged_name = "model" + source.suffix.lower()
    manifest = snapshot(source, directory, staged_name)
    model_path = (directory / staged_name).as_posix()
    receipt_path = (directory / "studio-receipt.json").as_posix()
    script = """#pragma NOPP
object mcpNull;
object mcpApp=getApplication();
double mcpOpened=mcpApp.openFile(@@MODEL@@);
if(mcpOpened) {
    object mcpModel=mcpApp.getModel($,1);
    object mcpSurfaces=mcpModel.getObjects(0,$,("Surface"),());
    double xmin=1e100,xmax=-1e100,ymin=1e100,ymax=-1e100,zmin=1e100,zmax=-1e100;
    double mcpBounds=mcpSurfaces.checkBoundingBox(xmin,xmax,ymin,ymax,zmin,zmax);
    object mcpReceipt=JSONStore(@@RECEIPT@@,0,1);
    mcpReceipt << (mcpOpened,mcpSurfaces.size(),mcpBounds,(xmin,xmax,ymin,ymax,zmin,zmax));
    mcpReceipt=mcpNull;
}
""".replace("@@MODEL@@", json.dumps(model_path)).replace("@@RECEIPT@@", json.dumps(receipt_path))
    launcher = directory / "open-model.lus"
    launcher.write_text(script, encoding="ascii")
    result = {"source_path": str(source), "loaded_file": str(directory / staged_name),
              "transport": "LucidStudio startup .lus -> native Application.openFile/getModel API",
              "computer_use_calls": 0, "inputs": manifest, "receipt_file": str(directory / "studio-receipt.json")}
    with (directory / "launch.log").open("wb") as log:
        process = subprocess.Popen([str(executable), str(launcher)], cwd=executable.parent,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    result["launcher_pid"] = process.pid
    deadline = time.monotonic() + timeout_seconds
    receipt = directory / "studio-receipt.json"
    try:
        while time.monotonic() < deadline:
            if receipt.is_file() and receipt.stat().st_size:
                try:
                    raw = json.loads(decode_text(receipt.read_bytes())[0])["List"]
                    opened, surfaces, bounds_ok, bounds = [raw[f"p{i}"] for i in range(4)]
                except (ValueError, KeyError):
                    await asyncio.sleep(0.2)
                    continue
                if opened != 1 or bounds_ok != 1 or count(surfaces) < 1:
                    raise ValueError("LucidStudio did not confirm loaded surface geometry")
                for value in sequence(bounds, 6):
                    number(value)
                if any(bounds[i] > bounds[i+1] for i in (0,2,4)):
                    raise ValueError("LucidStudio returned invalid geometry bounds")
                result.update(status="succeeded", studio_loaded_verified=True, surface_object_count=count(surfaces),
                              stability_verified=False, gui_responsiveness_verified=False,
                              verification_scope="Native openFile/getModel receipt only; not proof of later rendering stability or continued application responsiveness",
                              bounding_box_mm=bounds, dimensions_mm=[bounds[i+1]-bounds[i] for i in (0,2,4)],
                              source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
                break
            await asyncio.sleep(0.5)
        else:
            result.update(status="unverified", studio_loaded_verified=False,
                          error="No native LucidStudio API receipt before deadline; application launch alone is not proof of import. Any launched Studio instance is left open.")
    finally:
        (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
