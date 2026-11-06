import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from lucidshape_mcp.catia import parameters
from lucidshape_mcp.catia_worker import show_all


class Selection:
    def __init__(self):
        self.objects=[SimpleNamespace(Name="hidden body",hidden=True),SimpleNamespace(Name="visible body",hidden=False)]
        self.items=[]
        self.VisProperties=self

    @property
    def Count2(self):
        return len(self.items)

    def Clear(self):
        self.items=[]

    def Search(self,query):
        self.items=[item for item in self.objects if item.hidden] if query!="Name=*,all" else list(self.objects)

    def Item2(self,index):
        return SimpleNamespace(Value=self.items[index-1])

    def Add(self,item):
        self.items.append(item)

    def SetShow(self,value):
        if value!=0:
            raise AssertionError("Show-all must set show, not toggle visibility")
        for item in self.items:
            item.hidden=False


class Document:
    def __init__(self,path,saved=True):
        self.FullName=str(path)
        self.Name=path.name
        self.Saved=saved
        self.Selection=Selection()
        self.saves=[]

    def Activate(self):
        pass

    def Save(self):
        self.SaveAs(self.FullName)

    def SaveAs(self,path):
        self.saves.append(str(path))
        Path(path).write_bytes(b"mock saved CATPart with all items visible")
        self.FullName=str(path)
        self.Saved=True


class Documents:
    Count=1
    def __init__(self,document):
        self.document=document
    def Item(self,index):
        return self.document
    def Open(self,path):
        return self.document


class VisibilityTests(unittest.TestCase):
    def setup_file(self,root):
        source=root/"source.CATPart"
        source.write_bytes(b"mock original native CAD bytes")
        job=root/"job"
        job.mkdir()
        (job/"input.CATPart").write_bytes(source.read_bytes())
        return source,job

    def test_show_all_backed_up_clean_source_and_restores_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            source,job=self.setup_file(Path(temporary))
            document=Document(source)
            document.Selection.Add(document.Selection.objects[1])
            original=(job/"input.CATPart").read_bytes()
            result=show_all(SimpleNamespace(DisplayFileAlerts=True),Documents(document),{"catia_path":str(source)},job)
            self.assertEqual(result["hidden_before"],1)
            self.assertEqual(result["hidden_after"],0)
            self.assertTrue(result["saved_to_source"])
            self.assertEqual((job/"input.CATPart").read_bytes(),original)
            self.assertEqual(document.Selection.Count2,1)

    def test_dirty_alias_not_saved_without_explicit_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            source,job=self.setup_file(Path(temporary))
            document=Document(Path(temporary)/"other.CATPart",saved=False)
            result=show_all(SimpleNamespace(DisplayFileAlerts=True),Documents(document),{"catia_path":str(source)},job)
            self.assertEqual(document.saves,[])
            self.assertTrue(result["save_deferred_for_preexisting_changes"])
            self.assertEqual(result["source_sha256_before"],result["source_sha256_after"])
            self.assertEqual(result["hidden_after"],0)

    def test_explicit_save_as_targets_requested_path_and_restores_alerts(self):
        with tempfile.TemporaryDirectory() as temporary:
            source,job=self.setup_file(Path(temporary))
            document=Document(Path(temporary)/"other.CATPart",saved=False)
            app=SimpleNamespace(DisplayFileAlerts=True)
            result=show_all(app,Documents(document),{"catia_path":str(source),"save_as_source":True},job)
            self.assertEqual(document.saves,[str(source)])
            self.assertTrue(app.DisplayFileAlerts)
            self.assertTrue(result["saved_to_source"])
            self.assertEqual(result["native_document"]["full_name"],str(source))
            self.assertEqual(result["source_sha256_before"],hashlib.sha256((job/"input.CATPart").read_bytes()).hexdigest())

    def test_source_race_rejected_before_edit(self):
        with tempfile.TemporaryDirectory() as temporary:
            source,job=self.setup_file(Path(temporary))
            source.write_bytes(b"different source")
            document=Document(source)
            with self.assertRaisesRegex(RuntimeError,"changed after its backup"):
                show_all(SimpleNamespace(),Documents(document),{"catia_path":str(source)},job)
            self.assertTrue(document.Selection.objects[0].hidden)
            self.assertEqual(document.saves,[])

    def test_nonboolean_save_scope_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            source,job=self.setup_file(Path(temporary))
            with self.assertRaises(ValueError):
                parameters("catia_show_all",catia_path=str(source),save_as_source="yes")


if __name__=="__main__":
    unittest.main()
