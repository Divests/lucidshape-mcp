from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from lucidshape_mcp.catia_worker import describe, export_step


class Document:
    def __init__(self, path, saved=True, export_error=False):
        self.FullName = str(path)
        self.Name = path.name
        self.Saved = saved
        self.ReadOnly = False
        self.Part = SimpleNamespace(Bodies=SimpleNamespace(Count=0), HybridBodies=SimpleNamespace(Count=0))
        self.closed = False
        self.exports = []
        self.export_error = export_error

    def Close(self):
        self.closed = True

    def ExportData(self, path, kind):
        self.exports.append((path, kind))
        if self.export_error:
            raise RuntimeError("mock export failure")
        Path(path).write_bytes(b"mock STEP, not real CAD")


class Documents:
    def __init__(self, existing, return_existing=None):
        self.items = list(existing)
        self.opens = []
        self.return_existing = return_existing

    @property
    def Count(self):
        return len(self.items)

    def Item(self, index):
        return self.items[index - 1]

    def Open(self, path):
        self.opens.append(path)
        if self.return_existing is not None:
            return self.return_existing
        document = Document(Path(path))
        self.items.append(document)
        return document


class DocumentTests(unittest.TestCase):
    def setup_files(self, root):
        source = root / "source.CATPart"
        source.write_bytes(b"opaque native CAD bytes")
        job = root / "job"
        job.mkdir()
        (job / "input.CATPart").write_bytes(source.read_bytes())
        return source, job

    def test_read_state_distinguishes_disk_path_from_unsaved_edits(self):
        document = Document(Path("source.CATPart"), saved=False)
        self.assertIs(describe(document)["saved"], False)
        self.assertIs(describe(document)["read_only"], False)

    def test_saved_source_is_reused_without_opening_or_closing(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, job = self.setup_files(Path(temporary))
            document = Document(source)
            documents = Documents([document])
            result = export_step(documents, {"catia_path": str(source)}, job)
            self.assertEqual(documents.opens, [])
            self.assertFalse(document.closed)
            self.assertEqual(result["document_mode"], "reused_saved_document")
            self.assertFalse(result["private_document_closed"])
            self.assertEqual(source.read_bytes(), (job / "input.CATPart").read_bytes())

    def test_dirty_source_refuses_before_any_open_export_or_close(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, job = self.setup_files(Path(temporary))
            document = Document(source, saved=False)
            documents = Documents([document])
            with self.assertRaisesRegex(RuntimeError, "unsaved changes"):
                export_step(documents, {"catia_path": str(source)}, job)
            self.assertEqual(documents.opens, [])
            self.assertEqual(document.exports, [])
            self.assertFalse(document.closed)
            self.assertFalse((job / "export.stp").exists())

    def test_explicit_current_export_keeps_unsaved_document_and_disk_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, job = self.setup_files(Path(temporary))
            document = Document(source, saved=False)
            documents = Documents([document])
            result = export_step(documents, {"catia_path": str(source), "include_unsaved": True}, job)
            self.assertEqual(result["document_mode"], "reused_unsaved_document")
            self.assertEqual(result["geometry_source"], "current_in_memory_document")
            self.assertFalse(result["document_after_export"]["saved"])
            self.assertEqual(documents.opens, [])
            self.assertFalse(document.closed)
            self.assertEqual(source.read_bytes(), (job / "input.CATPart").read_bytes())

    def test_known_identical_copy_refuses_without_opening(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, job = self.setup_files(Path(temporary))
            alias = source.with_name("renamed-copy.CATPart")
            alias.write_bytes(source.read_bytes())
            document = Document(alias)
            documents = Documents([document])
            with self.assertRaisesRegex(RuntimeError, "byte-identical"):
                export_step(documents, {"catia_path": str(source)}, job)
            self.assertEqual(documents.opens, [])
            self.assertFalse(document.closed)

    def test_unexpected_native_alias_is_never_exported_or_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, job = self.setup_files(Path(temporary))
            alias = source.with_name("alias.CATPart")
            alias.write_bytes(b"same internal ID with different disk edits")
            document = Document(alias)
            documents = Documents([document], return_existing=document)
            with self.assertRaisesRegex(RuntimeError, "returned an existing document"):
                export_step(documents, {"catia_path": str(source)}, job)
            self.assertEqual(document.exports, [])
            self.assertFalse(document.closed)

    def test_only_proven_private_document_is_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, job = self.setup_files(Path(temporary))
            existing = Document(source.with_name("unrelated.CATPart"), saved=False)
            documents = Documents([existing])
            result = export_step(documents, {"catia_path": str(source)}, job)
            self.assertEqual(documents.opens, [str(job / "input.CATPart")])
            self.assertFalse(existing.closed)
            self.assertTrue(documents.items[-1].closed)
            self.assertEqual(result["document_mode"], "private_snapshot")
            self.assertTrue(result["private_document_closed"])

    def test_failed_export_does_not_close_reused_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, job = self.setup_files(Path(temporary))
            document = Document(source, export_error=True)
            documents = Documents([document])
            with self.assertRaisesRegex(RuntimeError, "mock export failure"):
                export_step(documents, {"catia_path": str(source)}, job)
            self.assertEqual(documents.opens, [])
            self.assertFalse(document.closed)


if __name__ == "__main__":
    unittest.main()
