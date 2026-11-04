"""The person's files: files.list/read/write tools, PDF/Word extraction, traversal rejects, and the four HTTP routes.

  python3 -m unittest discover -s app/tests -p 'test_files_tools.py'
"""
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import connectors, files_api, files_store, routes, web  # noqa: E402
from afterglow.files_store import MAX_TEXT_CHARS, FileStore  # noqa: E402
from afterglow.notes import LocalJsonNotes  # noqa: E402
from afterglow.tools import ToolBox, WorldState  # noqa: E402


def make_pdf_bytes(text):
    """A tiny valid one-font PDF holding `text`, with a computed xref table."""
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT /F1 24 Tf 72 720 Td ({safe}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = [b"%PDF-1.4\n"]
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(sum(map(len, out)))
        out.append(b"%d 0 obj\n" % n + body + b"\nendobj\n")
    xref_at = sum(map(len, out))
    out.append(b"xref\n0 %d\n" % (len(objs) + 1))
    out.append(b"0000000000 65535 f \n")
    for o in offsets:
        out.append(b"%010d 00000 n \n" % o)
    out.append(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref_at))
    return b"".join(out)


def make_docx_bytes(paragraphs):
    """A tiny valid .docx holding one paragraph per string."""
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' +
           "".join(f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in paragraphs) +
           "</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml",
                    '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                    'wordprocessingml.document.main+xml"/></Types>')
        zf.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships '
                    'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
        zf.writestr("word/document.xml", doc)
    return buf.getvalue()


class RunsMixin:
    world = "w-files"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_runs = os.environ.get("AFTERGLOW_RUNS")
        os.environ["AFTERGLOW_RUNS"] = self.tmp.name
        self.runs = Path(self.tmp.name)

    def tearDown(self):
        if self._old_runs is None:
            os.environ.pop("AFTERGLOW_RUNS", None)
        else:
            os.environ["AFTERGLOW_RUNS"] = self._old_runs
        self.tmp.cleanup()


class FilesToolsTest(RunsMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        state = Path(tempfile.mkdtemp()) / "world_state.json"
        self.box = ToolBox(LocalJsonNotes(str(self.runs / "notes.json")), WorldState(str(state), seed={}), day=1,
                           files=FileStore(self.world))
        self.store = self.box.files

    def test_write_list_read_md(self):
        out, written = self.box.run("files.write", {"name": "plan.md", "text": "# Plan\nShip it.\n"}, turn=1)
        self.assertEqual((out["id"], out["name"], sorted(out)), ("F001", "plan.md", ["id", "name"]))
        self.assertEqual(written, [])
        out, _ = self.box.run("files.list", {})
        self.assertIsInstance(out, list)
        self.assertEqual(len(out), 1)
        e = out[0]
        self.assertEqual((e["id"], e["name"], e["kind"]), ("F001", "plan.md", "md"))
        self.assertEqual(sorted(e), ["bytes", "created", "id", "kind", "name"])
        self.assertEqual(e["bytes"], len("# Plan\nShip it.\n".encode()))
        self.assertRegex(e["created"], r"^\d{4}-\d{2}-\d{2}T")
        out, _ = self.box.run("files.read", {"id": "F001"})
        self.assertEqual((out["id"], out["name"], out["kind"], out["text"]), ("F001", "plan.md", "md", "# Plan\nShip it.\n"))
        self.assertEqual(sorted(out), ["bytes", "id", "kind", "name", "text", "truncated"])
        self.assertFalse(out["truncated"])

    def test_write_and_read_txt(self):
        text = "héllo — world\n"
        out, _ = self.box.run("files.write", {"name": "note.txt", "text": text})
        self.assertEqual(out["name"], "note.txt")
        out, _ = self.box.run("files.read", {"id": out["id"]})
        self.assertEqual((out["text"], out["kind"], out["bytes"]), (text, "txt", len(text.encode("utf-8"))))

    def test_write_rejects_non_md_txt(self):
        for name in ("report.pdf", "word.docx", "data.json", "noext", "photo.png"):
            out, _ = self.box.run("files.write", {"name": name, "text": "x"})
            self.assertIn("only .md and .txt", out["error"], name)
        self.assertFalse((self.runs / self.world / "files" / "report.pdf").exists())

    def test_write_traversal_rejects(self):
        bad = ["../evil.md", "..\\evil.md", "/tmp/x.md", "sub/dir.md", "..", ".", ".hidden.md", "-flag.md",
               "a:b.md", "", "   ", "x" * 121 + ".md", "nul\x00.md"]
        for name in bad:
            out, _ = self.box.run("files.write", {"name": name, "text": "x"})
            self.assertIn("error", out, name)
        root = self.runs / self.world / "files"
        left = sorted(p.name for p in root.iterdir()) if root.is_dir() else []
        self.assertEqual([n for n in left if n != "index.json"], [])

    def test_read_missing_and_bad_id(self):
        out, _ = self.box.run("files.read", {"id": "F999"})
        self.assertIn("no file F999", out["error"])
        for fid in ("../x", "a/b", "", "F", "F1", "f001", "F001/preview", 123, None):
            out, _ = self.box.run("files.read", {"id": fid})
            self.assertIn("bad file id", out["error"], repr(fid))
        out, _ = self.box.run("files.read", {})
        self.assertIn("bad arguments", out["error"])

    def test_pdf_fixture_extracts(self):
        root = self.runs / self.world / "files"
        root.mkdir(parents=True)
        (root / "report.pdf").write_bytes(make_pdf_bytes("Hello Afterglow PDF"))
        (root / "other.txt").write_text("plain", encoding="utf-8")
        out, _ = self.box.run("files.list", {})
        self.assertEqual([(e["name"], e["kind"]) for e in out], [("other.txt", "txt"), ("report.pdf", "pdf")])
        pdf = next(e for e in out if e["kind"] == "pdf")
        out, _ = self.box.run("files.read", {"id": pdf["id"]})
        self.assertEqual(out["text"], "Hello Afterglow PDF")
        self.assertEqual(out["bytes"], (root / "report.pdf").stat().st_size)
        self.assertFalse(out["truncated"])

    def test_docx_fixture_extracts(self):
        root = self.runs / self.world / "files"
        root.mkdir(parents=True)
        (root / "letter.docx").write_bytes(make_docx_bytes(["Dear Mira,", "The review is & on <Friday>."]))
        out, _ = self.box.run("files.list", {})
        self.assertEqual([(e["name"], e["kind"]) for e in out], [("letter.docx", "docx")])
        out, _ = self.box.run("files.read", {"id": out[0]["id"]})
        self.assertEqual(out["text"], "Dear Mira,\nThe review is & on <Friday>.")

    def test_truncation_indicator(self):
        long, short = "ab" * 12500, "tiny"
        out, _ = self.box.run("files.write", {"name": "long.txt", "text": long})
        got, _ = self.box.run("files.read", {"id": out["id"]})
        self.assertTrue(got["truncated"])
        self.assertEqual((len(got["text"]), got["text"]), (MAX_TEXT_CHARS, long[:MAX_TEXT_CHARS]))
        self.assertEqual(got["bytes"], len(long))
        out, _ = self.box.run("files.write", {"name": "short.txt", "text": short})
        got, _ = self.box.run("files.read", {"id": out["id"]})
        self.assertFalse(got["truncated"])
        self.assertEqual(got["text"], short)

    def test_broken_files_report_errors(self):
        root = self.runs / self.world / "files"
        root.mkdir(parents=True)
        (root / "broken.pdf").write_bytes(b"not a pdf at all")
        (root / "broken.docx").write_bytes(b"PK not really a zip")
        out, _ = self.box.run("files.list", {})
        self.assertEqual(len(out), 2)
        for e in out:
            got, _ = self.box.run("files.read", {"id": e["id"]})
            self.assertIn("error", got, e["name"])
        self.assertIn("PDF", self.box.run("files.read", {"id": out[0]["id"] if out[0]["kind"] == "pdf" else out[1]["id"]})[0]["error"])

    def test_index_json_is_never_listed(self):
        self.box.run("files.write", {"name": "a.md", "text": "x"})
        self.assertTrue((self.runs / self.world / "files" / "index.json").exists())
        out, _ = self.box.run("files.list", {})
        self.assertEqual([e["name"] for e in out], ["a.md"])

    def test_overwrite_same_name_keeps_id(self):
        first, _ = self.box.run("files.write", {"name": "log.md", "text": "one"})
        second, _ = self.box.run("files.write", {"name": "log.md", "text": "two!"})
        self.assertEqual(first["id"], second["id"])
        got, _ = self.box.run("files.read", {"id": first["id"]})
        self.assertEqual((got["text"], got["bytes"]), ("two!", 4))
        out, _ = self.box.run("files.list", {})
        self.assertEqual(len(out), 1)

    def test_symlinks_are_skipped_and_never_overwritten(self):
        root = self.runs / self.world / "files"
        root.mkdir(parents=True)
        (root / "real.md").write_text("real", encoding="utf-8")
        (root / "link.md").symlink_to(root / "real.md")
        out, _ = self.box.run("files.list", {})
        self.assertEqual([e["name"] for e in out], ["real.md"])
        out, _ = self.box.run("files.write", {"name": "link.md", "text": "x"})
        self.assertIn("cannot be written", out["error"])
        self.assertEqual((root / "real.md").read_text(), "real")

    def test_files_not_wired_reports_error(self):
        state = Path(tempfile.mkdtemp()) / "w.json"
        box = ToolBox(LocalJsonNotes(str(self.runs / "n2.json")), WorldState(str(state), seed={}))
        for name, args in (("files.list", {}), ("files.read", {"id": "F001"}),
                           ("files.write", {"name": "a.md", "text": "x"})):
            out, _ = box.run(name, args)
            self.assertIn("not set up", out["error"])


class ConfiguredFolderTest(RunsMixin, unittest.TestCase):
    def user_dir(self, *names):
        d = Path(tempfile.mkdtemp()) / "docs"
        d.mkdir()
        for n in names:
            (d / n).write_text("user text", encoding="utf-8")
        return d

    def test_lists_user_documents_not_only_tool_written(self):
        d = Path(tempfile.mkdtemp()) / "docs"
        d.mkdir()
        (d / "user.md").write_text("# mine", encoding="utf-8")
        (d / "notes.txt").write_text("hi", encoding="utf-8")
        (d / "scan.pdf").write_bytes(make_pdf_bytes("Scanned words"))
        (d / "cv.docx").write_bytes(make_docx_bytes(["CV line"]))
        (d / "photo.png").write_bytes(b"\x89PNG nope")
        (d / "index.json").write_text("{}", encoding="utf-8")
        (d / ".hidden.md").write_text("shh", encoding="utf-8")
        (d / "sub").mkdir()
        (d / "sub" / "nested.md").write_text("deep", encoding="utf-8")
        with mock.patch.object(connectors, "files_folder", return_value=d):
            store = FileStore(self.world)
            self.assertTrue(store.is_configured)
            found = store.list_files()
            self.assertEqual([(e["name"], e["kind"]) for e in found],
                             [("cv.docx", "docx"), ("notes.txt", "txt"), ("scan.pdf", "pdf"), ("user.md", "md")])
            pdf = next(e for e in found if e["kind"] == "pdf")
            self.assertEqual(store.read_file(pdf["id"])["text"], "Scanned words")

    def test_missing_configured_folder_lists_empty_and_never_made(self):
        missing = Path(tempfile.mkdtemp()) / "gone"
        with mock.patch.object(connectors, "files_folder", return_value=missing):
            store = FileStore(self.world)
            self.assertEqual(store.list_files(), [])
            self.assertIsNone(store.get_entry("F001"))
            self.assertIn("missing", store.read_file("F001")["error"])
            self.assertIn("missing", store.write_file("a.md", "x")["error"])
            self.assertFalse(missing.exists())

    def test_write_goes_to_configured_folder(self):
        d = self.user_dir()
        with mock.patch.object(connectors, "files_folder", return_value=d):
            store = FileStore(self.world)
            out = store.write_file("hello.md", "# hi")
            self.assertEqual(out["name"], "hello.md")
            self.assertEqual((d / "hello.md").read_text(encoding="utf-8"), "# hi")
            self.assertEqual([e["name"] for e in store.list_files()], ["hello.md"])
            self.assertFalse((d / "index.json").exists())
            self.assertFalse((self.runs / self.world / "files").exists())
            self.assertTrue((self.runs / self.world / "files-index.json").exists())

    def test_list_and_read_create_nothing_in_user_folder(self):
        d = self.user_dir("a.md", "b.txt")
        before = sorted(p.name for p in d.iterdir())
        with mock.patch.object(connectors, "files_folder", return_value=d):
            store = FileStore(self.world)
            found = store.list_files()
            store.read_file(found[0]["id"])
            self.assertEqual(sorted(p.name for p in d.iterdir()), before)

    def test_fallback_when_no_folder_is_set(self):
        self.assertIsNone(connectors.files_folder(self.world))  # nothing saved on a fresh runs root
        store = FileStore(self.world)
        self.assertFalse(store.is_configured)
        self.assertEqual(store.root, self.runs / self.world / "files")
        out = store.write_file("a.md", "x")
        self.assertEqual((out["id"], (store.root / "a.md").read_text(encoding="utf-8")), ("F001", "x"))


class FakeSession:
    def __init__(self, world):
        self.world = world


class FakeApp:
    def __init__(self, world="w-files"):
        self.session = FakeSession(world)


def json_post(path, doc):
    return routes.Request("POST", path, "", {"Content-Type": "application/json"}, json.dumps(doc).encode())


def json_get(path, rest=""):
    req = routes.Request("GET", path, "", {}, None)
    req.rest = rest
    return req


class FilesHttpTest(RunsMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.app = FakeApp()
        self.store = FileStore(self.world)

    def test_routes_are_registered_and_wildcards_split(self):
        routes._table = None
        try:
            t = routes.table()
            self.assertIn(("GET", "/api/files"), t)
            self.assertIn(("GET", "/api/files/*"), t)
            self.assertIn(("POST", "/api/files"), t)
            fn, rest = routes.find("GET", "/api/files/F001/preview")
            self.assertEqual(rest, "F001/preview")
            self.assertIs(fn, t[("GET", "/api/files/*")])
        finally:
            routes._table = None

    def test_post_list_get_roundtrip(self):
        out = files_api.upload(self.app, json_post("/api/files", {"name": "demo.md", "text": "# demo"}))
        self.assertEqual((out["id"], out["name"]), ("F001", "demo.md"))
        listed = files_api.list_files(self.app, json_get("/api/files"))
        self.assertEqual([(e["id"], e["name"]) for e in listed], [("F001", "demo.md")])
        got = files_api.file_or_preview(self.app, json_get("/api/files/F001", "F001"))
        self.assertEqual((got["id"], got["text"], got["truncated"]), ("F001", "# demo", False))

    def test_upload_rejects(self):
        with self.assertRaises(ValueError):
            files_api.upload(self.app, json_post("/api/files", {"name": "../x.md", "text": "x"}))
        with self.assertRaises(ValueError):
            files_api.upload(self.app, json_post("/api/files", {"name": "a.pdf", "text": "x"}))
        with self.assertRaises(ValueError):
            files_api.upload(self.app, json_post("/api/files", {"name": "a.md"}))
        raw = routes.Request("POST", "/api/files", "", {"Content-Type": "multipart/form-data; boundary=x"}, b"--x")
        with self.assertRaises(ValueError):
            files_api.upload(self.app, raw)

    def test_missing_id_is_404_and_bad_id_is_400(self):
        body, status = files_api.file_or_preview(self.app, json_get("/api/files/F999", "F999"))
        self.assertEqual((status, body["error"]), (404, "no file F999"))
        body, status = files_api.file_or_preview(self.app, json_get("/api/files/F999/preview", "F999/preview"))
        self.assertEqual(status, 404)
        with self.assertRaises(ValueError):
            files_api.file_or_preview(self.app, json_get("/api/files/../x", "../x"))

    def test_preview_is_raw_bytes_with_content_type(self):
        pdf = make_pdf_bytes("Preview me")
        docx = make_docx_bytes(["Docx preview"])
        fid = {n: self.store.write_file(n, t)["id"] for n, t in (("a.md", "# A"), ("b.txt", "B"))}
        root = self.runs / self.world / "files"
        (root / "c.pdf").write_bytes(pdf)
        (root / "d.docx").write_bytes(docx)
        ids = {e["name"]: e["id"] for e in self.store.list_files()}
        ids.update(fid)
        expect = {"a.md": ("text/markdown; charset=utf-8", b"# A"), "b.txt": ("text/plain; charset=utf-8", b"B"),
                  "c.pdf": ("application/pdf", pdf), "d.docx": (
                      "application/vnd.openxmlformats-officedocument.wordprocessingml.document", docx)}
        for name, (ctype, raw) in expect.items():
            raw_out = files_api.file_or_preview(self.app, json_get(f"/api/files/{ids[name]}/preview", f"{ids[name]}/preview"))
            self.assertIsInstance(raw_out, routes.Raw)
            self.assertEqual((raw_out.content_type, raw_out.body), (ctype, raw))

    def test_broken_pdf_read_is_500(self):
        root = self.runs / self.world / "files"
        root.mkdir(parents=True)
        (root / "bad.pdf").write_bytes(b"junk")
        fid = next(e["id"] for e in self.store.list_files())
        body, status = files_api.file_or_preview(self.app, json_get(f"/api/files/{fid}", fid))
        self.assertEqual(status, 500)
        self.assertIn("PDF", body["error"])

    def test_a_new_file_from_the_files_screen_never_replaces_one(self):
        first = self.store.write_file("note.md", "First irreplaceable note", exclusive=True)
        self.assertNotIn("error", first)
        second = self.store.write_file("note.md", "Second separate note", exclusive=True)
        self.assertTrue(second.get("exists"))
        self.assertEqual((self.runs / self.world / "files" / "note.md").read_text(), "First irreplaceable note")
        with self.assertRaises(RuntimeError):  # the route answers 409
            files_api.upload(self.app, json_post("/api/files", {"name": "note.md", "text": "again", "exclusive": True}))
        self.assertEqual((self.runs / self.world / "files" / "note.md").read_text(), "First irreplaceable note")
        # the agent's files.write keeps its documented replace
        self.assertNotIn("error", self.store.write_file("note.md", "Agent rewrite"))
        self.assertEqual((self.runs / self.world / "files" / "note.md").read_text(), "Agent rewrite")

    def test_live_server_end_to_end(self):
        pdf = make_pdf_bytes("Live bytes")
        fid = self.store.write_file("live.md", "# live")["id"]
        (self.runs / self.world / "files" / "live.pdf").write_bytes(pdf)
        pdf_id = next(e["id"] for e in self.store.list_files() if e["name"] == "live.pdf")
        old_app = web.Handler.app
        web.Handler.app = self.app
        srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_port}"
            req = urllib.request.Request(base + "/api/files", data=json.dumps({"name": "up.md", "text": "up"}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual((r.status, json.loads(r.read())["name"]), (200, "up.md"))
            with urllib.request.urlopen(base + "/api/files", timeout=5) as r:
                names = sorted(e["name"] for e in json.loads(r.read()))
                self.assertEqual(names, ["live.md", "live.pdf", "up.md"])
            with urllib.request.urlopen(base + f"/api/files/{fid}", timeout=5) as r:
                self.assertEqual(json.loads(r.read())["text"], "# live")
            with urllib.request.urlopen(base + f"/api/files/{pdf_id}/preview", timeout=5) as r:
                self.assertEqual(r.headers.get_content_type(), "application/pdf")
                self.assertEqual(r.read(), pdf)
            for path, code in ((f"/api/files/F404", 404), ("/api/files/../x", 400)):
                with self.assertRaises(urllib.error.HTTPError) as e:
                    urllib.request.urlopen(base + path, timeout=5)
                self.assertEqual(e.exception.code, code, path)
                e.exception.close()
            bad = urllib.request.Request(base + "/api/files", data=json.dumps({"name": "../x.md", "text": "x"}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(bad, timeout=5)
            self.assertEqual(e.exception.code, 400)
            e.exception.close()
        finally:
            srv.shutdown()
            srv.server_close()
            web.Handler.app = old_app


if __name__ == "__main__":
    unittest.main()
