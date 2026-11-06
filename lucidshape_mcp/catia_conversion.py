"""Native CATIA STEP-to-STL conversion with private-document ownership checks."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

def _clipboard_internal(document):
    """Recognize only CATIA's four native clipboard formats in the OS temp root."""
    from .catia_worker import describe,same_path
    path=Path(describe(document)['full_name'])
    return (same_path(path.parent,tempfile.gettempdir()) and
        re.fullmatch(r'ccp_dummy\d+_Format_(CATPrtCont|CATProdCont|CATPrtResult|CATPrtResultWithOutLink)',path.name) is not None)

def convert_step_to_stl(application,arguments,directory):
    from .catia_worker import describe,document_identity,open_documents,same_path
    documents=application.Documents
    existing=open_documents(documents)
    identities=[document_identity(d) for d in existing]
    before=[describe(d) for d in existing]
    previous=application.ActiveDocument if existing else None
    try:
        previous_window=application.ActiveWindow if existing else None
    except Exception:
        previous_window=None
    staged=directory/'input.stp'
    unique=directory/('mcp_'+directory.name+'.stp')
    native=directory/'imported.CATPart'
    output=directory/'converted.stl'
    if not staged.is_file() or any(p.exists() for p in (unique,native,output)):
        raise ValueError('Conversion requires a fresh private input and output directory')
    source_hash=hashlib.sha256(staged.read_bytes()).hexdigest()
    if hashlib.sha256(Path(arguments['cad_path']).read_bytes()).hexdigest()!=source_hash:
        raise ValueError('Source STEP changed after snapshot')
    shutil.copyfile(staged,unique)
    document=None
    owned=False
    result=None
    primary_error=None
    alerts=application.DisplayFileAlerts
    try:
        application.DisplayFileAlerts=False
        print('CATIA_PRIVATE_STEP_IMPORT',flush=True)
        document=documents.Open(str(unique))
        print('CATIA_IMPORTED_DOCUMENT',describe(document),flush=True)
        if document_identity(document) in identities or documents.Count!=len(existing)+1:
            raise RuntimeError('CATIA did not return exactly one new private document; no document will be closed')
        # STEP import may take its document name from the STEP product label.
        # New IUnknown identity, an exact +1 count and unchanged old identities
        # establish ownership; a translated display name is not a file identity.
        current_identities=[document_identity(d) for d in open_documents(documents)]
        if any(identity not in current_identities for identity in identities):
            raise RuntimeError('A preexisting document disappeared during import; no document will be closed')
        # Save only the new import, establishing a clean state for safe cleanup.
        document.SaveAs(str(native))
        if not same_path(describe(document)['full_name'],native) or document.Saved is not True:
            raise RuntimeError('Private CATIA import could not be confirmed saved')
        owned=True
        print('CATIA_PRIVATE_STL_EXPORT',flush=True)
        document.ExportData(str(output),'stl')
        if not output.is_file() or output.stat().st_size<84:
            raise RuntimeError('CATIA did not produce a nonempty STL file')
        if describe(document)['saved'] is not True or not same_path(describe(document)['full_name'],native):
            raise RuntimeError('Private document changed during STL export; output not accepted')
        if [describe(d) for d in existing]!=before or hashlib.sha256(Path(arguments['cad_path']).read_bytes()).hexdigest()!=source_hash:
            raise RuntimeError('Preexisting document state or source STEP changed during conversion')
        result={'backend':'CATIA COM Automation','source_step':arguments['cad_path'],
            'source_sha256':source_hash,'stl_file':str(output),'stl_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
            'private_native_file':str(native),'preexisting_documents_preserved':True,'source_geometry_modified':False,
            'private_document_closed':False,'tessellation_parameters':'existing CATIA export defaults; not certified',
            'mesh_convergence_verified':False,'optical_accuracy_verified':False,'computer_use_calls':0}
        return result
    except Exception as error:
        primary_error=error
        print('CATIA_CONVERSION_PRIMARY_ERROR',type(error).__name__,str(error),flush=True)
        raise
    finally:
        try:
            if owned and describe(document)['saved'] is True and same_path(describe(document)['full_name'],native):
                document.Close()
                if result is not None:
                    result['private_document_closed']=True
        finally:
            try:
                application.DisplayFileAlerts=alerts
            finally:
                if previous is not None and document_identity(application.ActiveDocument)==document_identity(previous):
                    pass
                elif previous_window is not None or previous is not None:
                    try:
                        (previous_window if previous_window is not None else previous).Activate()
                    except Exception as error:
                        print('CATIA_CONTEXT_RESTORE_ERROR',type(error).__name__,str(error),flush=True)
                        if primary_error is None:
                            raise

def export_open_body_to_stl(application,arguments,directory):
    """Copy one native solid body to a new owned Part; never reopen its CATPart."""
    from .catia_worker import describe,document_identity,open_documents,find_open_document,same_path,selection_count
    documents=application.Documents
    existing=open_documents(documents)
    identities=[document_identity(d) for d in existing]
    source=find_open_document(existing,arguments['catia_path'])
    if source is None:
        raise RuntimeError('Body export requires the exact source CATPart already open')
    # Copy replaces CATIA-owned clipboard documents and invalidates the old
    # wrappers. Preserve every ordinary document, including unsaved user Parts.
    protected=[d for d in existing if not _clipboard_internal(d)]
    before=[describe(d) for d in protected]
    protected_identities=[document_identity(d) for d in protected]
    clipboard_before=[describe(d) for d in existing if _clipboard_internal(d)]
    if describe(source)['saved'] is not True and arguments.get('include_unsaved') is not True:
        raise RuntimeError('Native body export requires explicit include_unsaved for this document')
    snapshot=directory/'input.CATPart'
    source_hash=hashlib.sha256(snapshot.read_bytes()).hexdigest()
    if hashlib.sha256(Path(arguments['catia_path']).read_bytes()).hexdigest()!=source_hash:
        raise RuntimeError('CATPart changed after snapshot')
    body=source.Part.Bodies.Item(arguments['body_name'])
    if str(body.Name)!=arguments['body_name']:
        raise RuntimeError('CATIA returned a different native body')
    # Return to the task source after a previous refused private import.
    source.Activate()
    selection=source.Selection
    if selection_count(selection)>100:
        raise RuntimeError('Refusing to replace a source selection larger than the restorable limit')
    selected=[selection.Item2(i).Value for i in range(1,selection_count(selection)+1)]
    previous=application.ActiveDocument
    try:
        window=application.ActiveWindow
    except Exception:
        window=None
    alerts=application.DisplayFileAlerts
    native=directory/'body.CATPart'
    stl=directory/'converted.stl'
    if native.exists() or stl.exists():
        raise ValueError('Native body export requires fresh private outputs')
    document=None
    owned=False
    result=None
    primary=None
    try:
        application.DisplayFileAlerts=False
        selection.Clear()
        selection.Add(body)
        selection.Copy()
        print('CATIA_NATIVE_BODY_COPIED',arguments['body_name'],flush=True)
        # CATIA adds hidden ccp_dummy documents for clipboard formats. They
        # belong to the clipboard and are never treated as our private Part.
        after_copy=open_documents(documents)
        after_copy_identities=[document_identity(d) for d in after_copy]
        print('CATIA_DOCUMENT_COUNTS',len(existing),len(after_copy),flush=True)
        document=documents.Add('Part')
        if document_identity(document) in after_copy_identities or documents.Count!=len(after_copy)+1:
            raise RuntimeError('New body document ownership is unconfirmed')
        owned=True
        target=document.Selection
        target.Clear()
        target.Add(document.Part)
        target.PasteSpecial('CATPrtResultWithOutLink')
        target.Clear()
        document.Part.Update()
        document.SaveAs(str(native))
        if describe(document)['saved'] is not True or not same_path(describe(document)['full_name'],native):
            raise RuntimeError('Native body snapshot was not confirmed saved')
        print('CATIA_NATIVE_BODY_STL_EXPORT',flush=True)
        document.ExportData(str(stl),'stl')
        if not stl.is_file() or stl.stat().st_size<84 or describe(document)['saved'] is not True:
            raise RuntimeError('Native body STL export was not confirmed')
        after=[describe(d) for d in protected]
        current_hash=hashlib.sha256(Path(arguments['catia_path']).read_bytes()).hexdigest()
        current_identities=[document_identity(d) for d in open_documents(documents)]
        if after!=before or current_hash!=source_hash or any(identity not in current_identities for identity in protected_identities):
            print('CATIA_BODY_EXPORT_STATE_DIFF',json.dumps({'before':before,'after':after,
                'source_sha256_before':source_hash,'source_sha256_after':current_hash},ensure_ascii=True),flush=True)
            raise RuntimeError('Preexisting document state or source CATPart changed')
        result={'backend':'CATIA COM Automation','source_path':arguments['catia_path'],
            'body_name':arguments['body_name'],'source_sha256':source_hash,
            'source_sha256_scope':'on-disk backup; body copied from current explicitly allowed in-memory document',
            'stl_file':str(stl),'stl_sha256':hashlib.sha256(stl.read_bytes()).hexdigest(),
            'private_native_file':str(native),'preexisting_documents_preserved':True,'source_geometry_modified':False,
            'private_document_closed':False,'tessellation_parameters':'existing native CATIA export defaults; not certified',
            'document_preservation_scope':'all preexisting non-clipboard documents; CATIA replaces its own temporary clipboard formats',
            'preexisting_document_states_before':before,'preexisting_document_states_after':after,
            'clipboard_documents_before':clipboard_before,
            'clipboard_documents_after_copy':[describe(d) for d in after_copy if _clipboard_internal(d)],
            'mesh_convergence_verified':False,'optical_accuracy_verified':False,'computer_use_calls':0}
        return result
    except Exception as error:
        primary=error
        print('CATIA_BODY_EXPORT_PRIMARY_ERROR',type(error).__name__,str(error),flush=True)
        raise
    finally:
        try:
            selection.Clear()
            for value in selected:
                selection.Add(value)
            if owned and describe(document)['saved'] is True and same_path(describe(document)['full_name'],native):
                document.Close()
                if result is not None:
                    result['private_document_closed']=True
        finally:
            try:
                application.DisplayFileAlerts=alerts
            finally:
                if document_identity(application.ActiveDocument)!=document_identity(previous):
                    try:
                        (window if window is not None else previous).Activate()
                    except Exception as error:
                        print('CATIA_CONTEXT_RESTORE_ERROR',type(error).__name__,str(error),flush=True)
                        if primary is None:
                            raise
