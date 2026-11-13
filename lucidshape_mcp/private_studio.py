"""Own one export process; never attach to or close an existing Studio instance."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import time

from .dde_worker import studio_processes

_PROCESSES = {}


def _write_launch(directory, result):
    (directory / "launch.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


def _failure(result, code, message):
    result["error"] = message
    result["error_details"] = {"code": code, "message": message, "retryable": False}


def _progress(directory):
    """Read bounded task-generated files only, never inspect windows or send DDE."""
    for name in ("native-progress.json", "native-progress.txt"):
        path = directory / name
        try:
            if not path.is_file() or not 0 < path.stat().st_size <= 65536:
                continue
            text = path.read_text(encoding="utf-8-sig").strip()
            if not text:
                continue
            if path.suffix == ".json":
                json.loads(text)
            return {"native_progress_observed": True, "native_progress_file": str(path),
                    "native_progress_tail": text[-2000:]}
        except (OSError, UnicodeError, ValueError):
            continue
    return {"native_progress_observed": False}


async def run_private_studio(executable: Path, directory: Path, script_text: str,
                             receipt_name: str, timeout_seconds: int = 120) -> dict:
    executable = Path(executable).resolve()
    directory = Path(directory).resolve()
    if not executable.is_file():
        raise ValueError("LucidShape.exe not found")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 600:
        raise ValueError("timeout_seconds must be 1..600")
    if not receipt_name or receipt_name in (".", "..") or any(c in receipt_name for c in "/\\:"):
        raise ValueError("receipt_name must be a basename")
    directory.mkdir(parents=True, exist_ok=True)
    receipt = directory / receipt_name
    script = directory / "native-job.lus"
    # Refuse old progress as well as old receipts: it must describe this launch.
    if any((directory / name).exists() for name in
           (receipt_name, "native-job.lus", "native-progress.json", "native-progress.txt")):
        raise ValueError("Private launch artifacts already exist; refuse replay")
    result = {"status": "unverified", "phase": "preflight", "executable": str(executable),
              "script": str(script), "receipt_file": str(receipt),
              "private_process_started": False, "existing_processes_modified": False,
              "computer_use_calls": 0, "process_left_open": False,
              "automatic_resubmission_allowed": False}
    try:
        existing = studio_processes()
    except (OSError, RuntimeError, AttributeError) as exc:
        result["status"] = "unavailable"
        _failure(result, "STUDIO_PREFLIGHT_FAILED",
                 f"Cannot verify exclusive Studio startup: {exc}. No application was started or closed.")
        _write_launch(directory, result)
        return result
    result["running_studio_processes"] = existing
    if existing:
        result["status"] = "unavailable"
        _failure(result, "STUDIO_ALREADY_RUNNING",
                 "This export profile requires exclusive Studio startup; existing processes were left untouched. "
                 "Save and close those windows once, then call the export without manually reopening Studio. "
                 "This guard does not prove that an existing process is faulty.")
        _write_launch(directory, result)
        return result

    script.write_text(script_text, encoding="utf-8")
    startup = None
    if hasattr(subprocess, "STARTUPINFO"):
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
    try:
        with (directory / "launch.log").open("wb") as log:
            process = subprocess.Popen([str(executable), str(script)], cwd=executable.parent,
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       startupinfo=startup)
    except OSError as exc:
        _failure(result, "STUDIO_LAUNCH_FAILED", f"Studio could not be started: {exc}")
        _write_launch(directory, result)
        return result
    _PROCESSES[process.pid] = process
    result.update(launcher_pid=process.pid, private_process_started=True,
                  process_left_open=True, phase="starting")
    try:
        _write_launch(directory, result)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if receipt.is_file() and 0 < receipt.stat().st_size <= 1048576:
                try:
                    json.loads(receipt.read_text(encoding="utf-8-sig"))
                except (ValueError, UnicodeError):
                    pass
                else:
                    if process.poll() is not None:
                        _failure(result, "STUDIO_EXITED", "Launching Studio exited; native process ownership is unverified")
                    else:
                        result.update(status="receipt_ready", phase="verifying")
                    break
            if process.poll() is not None:
                result["exit_code"] = process.returncode
                _failure(result, "STUDIO_EXITED", "Studio exited before producing a native receipt")
                break
            await asyncio.sleep(.1)
        else:
            result.update(_progress(directory))
            if result["native_progress_observed"]:
                result["phase"] = "native_export"
                _failure(result, "NATIVE_EXPORT_INCOMPLETE",
                         "Native script progress was recorded, but no complete receipt arrived before the deadline.")
            else:
                result["phase"] = "startup_unconfirmed"
                _failure(result, "STUDIO_STARTUP_UNCONFIRMED",
                         "No native progress or complete receipt before the deadline. This does not identify a DDE, "
                         "license or network failure. A startup dialog, including an automatic update check, is one possible cause.")
    except asyncio.CancelledError:
        result.update(status="interrupted", phase="cancelled")
        _failure(result, "EXPORT_CANCELLED", "Caller cancelled this private export; no automatic resubmission")
        raise
    except (OSError, ValueError) as exc:
        _failure(result, "EXPORT_OBSERVATION_FAILED", f"Could not read native export evidence: {exc}")
    finally:
        result.update(_progress(directory))
        if result["status"] != "receipt_ready":
            result["private_process_cleanup"] = await close_private_studio(result)
            result["process_left_open"] = not result["private_process_cleanup"]["closed"]
        _write_launch(directory, result)
    return result


async def close_private_studio(launch: dict) -> dict:
    """Close only the retained Popen handle, including a failed derived-file export.

    This function must not be used for a live user document or PID-based recovery.
    A lost handle (for example after server restart) is never reconstructed.
    """
    pid = launch.get("launcher_pid")
    process = _PROCESSES.get(pid)
    if process is None or not launch.get("private_process_started"):
        return {"closed": False, "reason": "No owned Popen handle; left untouched"}
    if process.poll() is None:
        try:
            process.terminate()
            await asyncio.to_thread(process.wait, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"closed": False, "pid": pid, "reason": str(exc)}
    _PROCESSES.pop(pid, None)
    return {"closed": True, "pid": pid, "exit_code": process.returncode,
            "method": "owned Popen handle terminate after export attempt; logs and artifacts retained",
            "existing_processes_modified": False}
