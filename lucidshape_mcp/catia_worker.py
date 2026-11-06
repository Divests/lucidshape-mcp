"""STA worker for fixed CATIA tasks; invoked only by the local MCP job runner."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sys

from .catia import parameters


def same_path(left, right):
    return str(left).replace("/", "\\").casefold() == str(right).replace("/", "\\").casefold()


def open_documents(documents):
    return [documents.Item(i) for i in range(1, documents.Count + 1)]


def find_open_document(documents, path):
    matches = [document for document in documents if same_path(describe(document)["full_name"], path)]
    if len(matches) > 1:
        raise RuntimeError("More than one open CATIA document reports the requested path")
    return matches[0] if matches else None


def document_identity(document):
    # Compare IUnknown identities, not transient Python dispatch wrappers or names.
    ole = getattr(document, "_oleobj_", None)
    if ole is None:
        return document  # In-process test doubles only.
    import pythoncom
    return ole.QueryInterface(pythoncom.IID_IUnknown)


def selection_count(selection):
    try:
        return int(selection.Count2)
    except Exception:
        return int(selection.Count)


def hidden_selection(selection):
    errors=[]
    for query in ("visibility:hidden,all","Visibility=Hidden,all","CATPrtSearch.Visibility=Hidden,all"):
        selection.Clear()
        try:
            selection.Search(query)
            return selection_count(selection),query
        except Exception as error:
            errors.append(str(error))
    raise RuntimeError("CATIA does not accept a hidden-object search; refusing an unverifiable visibility edit: "+"; ".join(errors))


def show_all(application,documents,arguments,directory):
    source=Path(arguments["catia_path"])
    backup=directory/"input.CATPart"
    original_hash=hashlib.sha256(backup.read_bytes()).hexdigest()
    if hashlib.sha256(source.read_bytes()).hexdigest()!=original_hash:
        raise RuntimeError("CATPart changed after its backup was made; refusing to edit a different file")
    existing=open_documents(documents)
    identities=[document_identity(item) for item in existing]
    document=find_open_document(existing,source)
    already_open=document is not None
    if document is None:
        document=documents.Open(str(source))
        already_open=document_identity(document) in identities
    document.Activate()
    saved_before=bool(document.Saved)
    selection=document.Selection
    previous=[]
    for i in range(1,min(selection_count(selection),100)+1):
        try:
            previous.append(selection.Item2(i).Value)
        except Exception:
            pass
    try:
        before,query=hidden_selection(selection)
        names=[]
        for i in range(1,min(before,50)+1):
            try:
                names.append(str(selection.Item2(i).Value.Name))
            except Exception:
                pass
        selection.Clear()
        selection.Search("Name=*,all")
        total=selection_count(selection)
        if total==0:
            raise RuntimeError("CATIA selected no objects for the show-all operation")
        selection.VisProperties.SetShow(0)  # catVisPropertyShowAttr, not a visibility toggle.
        rounds=1
        for _ in range(5):
            remaining,_=hidden_selection(selection)
            if remaining==0:
                break
            selection.VisProperties.SetShow(0)
            rounds+=1
        remaining,_=hidden_selection(selection)
        if remaining:
            raise RuntimeError(f"{remaining} objects are still hidden; source is left unsaved")
        saved_to_source=False
        save_as_requested=arguments.get("save_as_source",False)
        if save_as_requested:
            alerts=application.DisplayFileAlerts
            try:
                application.DisplayFileAlerts=False
                document.SaveAs(str(source))
            finally:
                application.DisplayFileAlerts=alerts
            saved_to_source=bool(document.Saved)
            if not saved_to_source or describe(document)["full_name"].replace("/","\\").casefold()!=str(source).replace("/","\\").casefold():
                raise RuntimeError("CATIA did not confirm SaveAs to the requested source file")
        elif saved_before and describe(document)["full_name"].replace("/","\\").casefold()==str(source).replace("/","\\").casefold():
            document.Save()
            saved_to_source=bool(document.Saved)
            if not saved_to_source:
                raise RuntimeError("CATIA did not confirm saving the visibility change")
        # Existing unsaved geometry must not be silently committed by a visibility-only operation.
        result={"backend":"CATIA COM Automation","source_path":str(source),"backup_file":str(backup),
                "source_sha256_before":original_hash,"source_sha256_after":hashlib.sha256(source.read_bytes()).hexdigest(),
                "document_already_open":already_open,"document_was_saved":saved_before,"saved_to_source":saved_to_source,
                "save_as_source_explicitly_requested":save_as_requested,
                "save_deferred_for_preexisting_changes":not saved_to_source,"selected_object_count":total,
                "hidden_before":before,"hidden_after":remaining,"show_rounds":rounds,"hidden_search":query,
                "previously_hidden_sample":names,"native_document":describe(document),"computer_use_calls":0}
        return result
    finally:
        selection.Clear()
        for item in previous:
            try:
                selection.Add(item)
            except Exception:
                pass


def describe(document) -> dict:
    name = document.Name
    try:
        full_name = document.FullName
    except Exception:
        full_name = name
    result = {"name": name, "full_name": full_name, "saved_to_disk": Path(full_name).is_absolute()}
    for attribute, key in (("Saved", "saved"), ("ReadOnly", "read_only")):
        try:
            result[key] = bool(getattr(document, attribute))
        except Exception:
            result[key] = None
    return result


def export_step(documents, arguments, directory):
    snapshot = directory / "input.CATPart"
    output = directory / "export.stp"
    source = Path(arguments["catia_path"])
    if not snapshot.is_file() or output.exists():
        raise ValueError("Require a private CATPart snapshot and a fresh STEP output path")
    source_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    existing = open_documents(documents)
    identities = [document_identity(item) for item in existing]
    document = find_open_document(existing, source)
    reused = document is not None
    owned = False
    result = None
    saved_before = describe(document)["saved"] if reused else None
    allow_unsaved = arguments.get("include_unsaved", False)
    if reused:
        if saved_before is not True and not allow_unsaved:
            raise RuntimeError("Requested CATPart is already open with unsaved changes; use that document and explicitly save the intended edits before exporting. No document was reopened, saved or closed.")
        if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
            raise RuntimeError("CATPart changed after its backup was made; refusing to export a different file")
    else:
        # Byte copies retain CATIA's internal identifier. Do not reopen a known
        # copy under another path, even though it has a different filesystem name.
        for candidate in existing:
            path = Path(describe(candidate)["full_name"])
            if path.is_absolute() and path.suffix.lower() == ".catpart" and path.is_file():
                if hashlib.sha256(path.read_bytes()).hexdigest() == source_hash:
                    raise RuntimeError(f"A byte-identical CATPart is already open at {path}; use its current path instead of opening another copy")
    try:
        if document is None:
            document = documents.Open(str(snapshot))
            if document_identity(document) in identities:
                raise RuntimeError("CATIA returned an existing document for the snapshot; it was left open and was not exported")
            if not same_path(describe(document)["full_name"], snapshot) or documents.Count != len(existing) + 1:
                raise RuntimeError("CATIA did not confirm a new private snapshot document; no document will be closed")
            owned = True
        part = document.Part
        result = {"backend": "CATIA COM Automation", "source_path": str(source),
                  "source_sha256": source_hash, "step_file": str(output),
                  "source_sha256_scope": "On-disk CATPart backup; not an identity hash of unsaved in-memory geometry",
                  "document_mode": ("reused_saved_document" if saved_before else "reused_unsaved_document") if reused else "private_snapshot",
                  "include_unsaved_requested": allow_unsaved, "document_was_saved": saved_before,
                  "geometry_source": "current_in_memory_document" if reused else "on_disk_snapshot",
                  "native_document": describe(document), "private_document_closed": False,
                  "body_count": part.Bodies.Count, "hybrid_body_count": part.HybridBodies.Count,
                  "bodies": [{"name": part.Bodies.Item(i).Name, "feature_count": part.Bodies.Item(i).Shapes.Count}
                             for i in range(1, min(part.Bodies.Count, 100) + 1)]}
        document.ExportData(str(output), "stp")
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError("CATIA did not export STEP")
        if reused and (describe(document)["saved"] is not saved_before or not same_path(describe(document)["full_name"], source)
                       or hashlib.sha256(source.read_bytes()).hexdigest() != source_hash):
            raise RuntimeError("The reused CATPart changed during export; the STEP was not accepted")
        result["step_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
        result["document_after_export"] = describe(document)
        return result
    finally:
        # Closing is permitted only for a new, clean document proven to belong
        # to this job. An aliased/reused user document must never be closed here.
        if owned and describe(document)["saved"] is True and same_path(describe(document)["full_name"], snapshot):
            document.Close()
            if result is not None:
                result["private_document_closed"] = True


def execute(application, action: str, arguments: dict, directory: Path) -> dict:
    """Business API only: no input injection, menus, dialogs, or screenshots."""
    documents = application.Documents
    if action == "catia_get_state":
        count = documents.Count
        return {"connected": True, "backend": "CATIA COM Automation",
                "document_count": count, "documents": [describe(documents.Item(i)) for i in range(1, min(count, 100) + 1)],
                "documents_truncated": count > 100,
                "active_document": describe(application.ActiveDocument) if count else None}

    if action=='catia_convert_step_to_stl':
        from .catia_conversion import convert_step_to_stl
        return convert_step_to_stl(application,arguments,directory)
    if action=='catia_export_body_stl':
        from .catia_conversion import export_open_body_to_stl
        return export_open_body_to_stl(application,arguments,directory)

    if action == "catia_show_all":
        return show_all(application,documents,arguments,directory)

    if action == "catia_inspect_part":
        from .catia_inspection import inspect_part
        return inspect_part(application,documents,arguments,describe)

    if action == "catia_export_step":
        return export_step(documents, arguments, directory)

    native = directory / "mirror.CATPart"
    step = directory / "mirror.stp"
    if native.exists() or step.exists():
        raise FileExistsError("Refusing to overwrite test artifacts")
    document = documents.Add("Part")
    print("Created new CATIA Part document for this MCP job", flush=True)
    part = document.Part
    document.Product.PartNumber = f"MCP_Mirror_{directory.name[:8]}"
    body = part.HybridBodies.Add()
    body.Name = "MCP mirror geometry"
    factory = part.HybridShapeFactory
    width, height, tilt = arguments["width_mm"], arguments["height_mm"], arguments["tilt_deg"]
    radians = math.radians(tilt)
    x, z = width / 2 * math.cos(radians), -width / 2 * math.sin(radians)
    first = factory.AddNewPointCoord(-x, height / 2, -z)
    second = factory.AddNewPointCoord(x, height / 2, z)
    body.AppendHybridShape(first)
    body.AppendHybridShape(second)
    part.Update()
    line = factory.AddNewLinePtPt(part.CreateReferenceFromObject(first), part.CreateReferenceFromObject(second))
    body.AppendHybridShape(line)
    part.Update()
    direction = factory.AddNewDirectionByCoord(0.0, 1.0, 0.0)
    surface = factory.AddNewExtrude(part.CreateReferenceFromObject(line), 0.0, float(height), direction)
    surface.Name = "MCP rectangular mirror"
    body.AppendHybridShape(surface)
    part.InWorkObject = surface
    part.Update()
    document.SaveAs(str(native))
    document.ExportData(str(step), "stp")
    if not native.is_file() or not step.is_file() or min(native.stat().st_size, step.stat().st_size) == 0:
        raise RuntimeError("CATIA did not save both expected artifacts")
    return {"backend": "CATIA COM Automation", "new_document": describe(document),
            "native_part": str(native), "step_file": str(step),
            "step_sha256": hashlib.sha256(step.read_bytes()).hexdigest(),
            "width_mm": width, "height_mm": height, "tilt_deg": tilt,
            "normal_xyz": [math.sin(radians), 0, math.cos(radians)],
            "expected_bounding_box_mm": [-x, x, -height/2, height/2, -abs(z), abs(z)]}


def main() -> int:
    request = Path(sys.argv[1]).resolve(strict=True)
    directory = request.parent
    try:
        payload = json.loads(request.read_text(encoding="utf-8"))
        action = payload["action"]
        args = parameters(action, **payload["parameters"])
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        application = None
        try:
            application = win32com.client.GetActiveObject("CATIA.Application")
            result = execute(application, action, args, directory)
        finally:
            application = None
            pythoncom.CoUninitialize()
        temporary = directory / "engine-result.tmp"
        temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
        temporary.replace(directory / "engine-result.json")
        print("LUCIDSHAPE_MCP_OK", flush=True)
        return 0
    except Exception as error:
        print("LUCIDSHAPE_MCP_FAILED: " + json.dumps(f"{type(error).__name__}: {error}", ensure_ascii=True), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
