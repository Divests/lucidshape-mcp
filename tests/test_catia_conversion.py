from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from lucidshape_mcp.catia_conversion import convert_step_to_stl,export_open_body_to_stl

class Selection:
    def __init__(self,doc):
        self.doc=doc
        self.values=[]
    @property
    def Count2(self):
        return len(self.values)
    def Item2(self,i):
        return SimpleNamespace(Value=self.values[i-1])
    def Clear(self):
        self.values=[]
    def Add(self,value):
        self.values.append(value)
    def Copy(self):
        self.doc.app.copied=list(self.values)
        for doc in getattr(self.doc.app,'clipboard_documents_to_invalidate',[]):
            doc.Saved=None
        if getattr(self.doc.app,'change_source_during_copy',False):
            self.doc.Saved=not self.doc.Saved
        for i in range(getattr(self.doc.app,'clipboard_documents_to_create',0)):
            self.doc.app.Documents.items.append(Doc(Path('ccp_dummy'+str(i)),self.doc.app))
    def PasteSpecial(self,format):
        if getattr(self.doc.app,'fail_paste',False):
            raise RuntimeError('fixture paste failure')
        self.doc.Saved=False

class Doc:
    def __init__(self,path,app):
        self.FullName=str(path)
        self.Name=path.name
        self.Saved=True
        self.ReadOnly=False
        self.app=app
        self.closed=False
        self.Selection=Selection(self)
        self.Part=SimpleNamespace(Bodies=SimpleNamespace(Item=lambda name:SimpleNamespace(Name=name)),Update=lambda:None)
    def SaveAs(self,path):
        self.FullName=path
        self.Name=Path(path).name
        self.Saved=True
        Path(path).write_bytes(b'private native test fixture')
    def ExportData(self,path,kind):
        if self.app.fail_export:
            raise RuntimeError('fixture export failure')
        Path(path).write_bytes(b'fixture STL'.ljust(84,b' '))
    def Close(self):
        self.closed=True
        if self.app.fail_close:
            raise RuntimeError('fixture close failure')
    def Activate(self):
        self.app.ActiveDocument=self

class Docs:
    def __init__(self,app):
        self.app=app
        self.items=[]
        self.alias=None
    @property
    def Count(self):
        return len(self.items)
    def Item(self,i):
        return self.items[i-1]
    def Open(self,path):
        if self.alias:
            return self.alias
        d=Doc(Path(path),self.app)
        self.items.append(d)
        self.app.ActiveDocument=d
        return d
    def Add(self,kind):
        if self.alias:
            return self.alias
        doc=Doc(Path('Part99.CATPart'),self.app)
        doc.Saved=False
        self.items.append(doc)
        self.app.ActiveDocument=doc
        return doc

class CatiaConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.job=self.root/'aabbccddee'
        self.job.mkdir()
        self.source=self.root/'source.stp'
        self.source.write_bytes(b'fixture STEP')
        (self.job/'input.stp').write_bytes(self.source.read_bytes())
        self.app=SimpleNamespace(DisplayFileAlerts=True,fail_export=False,fail_close=False)
        self.app.Documents=Docs(self.app)
        self.user=Doc(self.root/'user.CATPart',self.app)
        self.user.Saved=False
        self.app.Documents.items.append(self.user)
        self.app.ActiveDocument=self.user
    def run_conversion(self):
        return convert_step_to_stl(self.app,{'cad_path':str(self.source)},self.job)
    def test_only_private_clean_import_is_saved_exported_and_closed(self):
        result=self.run_conversion()
        self.assertTrue(result['private_document_closed'])
        self.assertFalse(self.user.closed)
        self.assertFalse(self.user.Saved)
        self.assertIs(self.app.ActiveDocument,self.user)
        self.assertTrue(self.app.DisplayFileAlerts)
        self.assertEqual(self.source.read_bytes(),b'fixture STEP')
    def test_existing_alias_is_never_saved_exported_or_closed(self):
        self.app.Documents.alias=self.user
        with self.assertRaisesRegex(RuntimeError,'exactly one new'):
            self.run_conversion()
        self.assertFalse(self.user.closed)
        self.assertFalse((self.job/'imported.CATPart').exists())
        self.assertTrue(self.app.DisplayFileAlerts)
    def test_export_failure_restores_original_state(self):
        self.app.fail_export=True
        with self.assertRaisesRegex(RuntimeError,'export failure'):
            self.run_conversion()
        self.assertFalse(self.user.closed)
        self.assertTrue(self.app.Documents.items[-1].closed)
        self.assertTrue(self.app.DisplayFileAlerts)
        self.assertIs(self.app.ActiveDocument,self.user)
    def test_cleanup_failure_still_restores_alerts_and_active_document(self):
        self.app.fail_close=True
        with self.assertRaisesRegex(RuntimeError,'close failure'):
            self.run_conversion()
        self.assertTrue(self.app.DisplayFileAlerts)
        self.assertIs(self.app.ActiveDocument,self.user)

    def prepare_body_source(self):
        path=Path(self.user.FullName)
        path.write_bytes(b'fixture current CATPart')
        (self.job/'input.CATPart').write_bytes(path.read_bytes())
        return {'catia_path':str(path),'body_name':'S9-LENS','include_unsaved':True}

    def test_body_copy_restores_selection_and_never_saves_source(self):
        args=self.prepare_body_source()
        prior=object()
        self.user.Selection.Add(prior)
        result=export_open_body_to_stl(self.app,args,self.job)
        self.assertTrue(result['private_document_closed'])
        self.assertFalse(self.user.closed)
        self.assertFalse(self.user.Saved)
        self.assertEqual(self.user.Selection.values,[prior])
        self.assertIs(self.app.ActiveDocument,self.user)
        self.assertTrue(self.app.DisplayFileAlerts)
        self.assertEqual(self.app.copied[0].Name,'S9-LENS')

    def test_dirty_body_source_requires_explicit_scope_before_copy(self):
        args=self.prepare_body_source()
        args['include_unsaved']=False
        with self.assertRaisesRegex(RuntimeError,'explicit'):
            export_open_body_to_stl(self.app,args,self.job)
        self.assertEqual(len(self.app.Documents.items),1)
        self.assertFalse(hasattr(self.app,'copied'))

    def test_native_clipboard_scratch_documents_are_not_mistaken_for_the_new_part(self):
        args=self.prepare_body_source()
        self.app.clipboard_documents_to_create=4
        result=export_open_body_to_stl(self.app,args,self.job)
        self.assertTrue(result['private_document_closed'])
        self.assertTrue(all(not d.closed for d in self.app.Documents.items[1:5]))
        self.assertFalse(self.user.closed)

    def test_replaced_native_clipboard_wrappers_do_not_mask_preserved_user_documents(self):
        args=self.prepare_body_source()
        scratch=Doc(Path(tempfile.gettempdir())/'ccp_dummy5_Format_CATPrtCont',self.app)
        self.app.Documents.items.append(scratch)
        self.app.clipboard_documents_to_invalidate=[scratch]
        result=export_open_body_to_stl(self.app,args,self.job)
        self.assertTrue(result['preexisting_documents_preserved'])
        self.assertEqual(len(result['preexisting_document_states_before']),1)
        self.assertFalse(scratch.closed)

    def test_real_source_state_change_is_still_rejected(self):
        args=self.prepare_body_source()
        self.app.change_source_during_copy=True
        with self.assertRaisesRegex(RuntimeError,'Preexisting document state'):
            export_open_body_to_stl(self.app,args,self.job)

    def test_failed_paste_leaves_dirty_private_document_and_source_intact(self):
        args=self.prepare_body_source()
        self.app.fail_paste=True
        with self.assertRaisesRegex(RuntimeError,'paste failure'):
            export_open_body_to_stl(self.app,args,self.job)
        self.assertFalse(self.app.Documents.items[-1].closed)
        self.assertFalse(self.user.closed)
        self.assertFalse(self.user.Saved)
        self.assertIs(self.app.ActiveDocument,self.user)
        self.assertTrue(self.app.DisplayFileAlerts)
