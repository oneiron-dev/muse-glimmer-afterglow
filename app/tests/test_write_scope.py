"""P1 fixes from the Astra review of main e185ff8 (Thu 5 Nov): who may write, and where a write lands.

  python3 -m unittest tests.test_write_scope
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import dictation, notes, routes, web  # noqa: E402
from afterglow.web import App  # noqa: E402


def recorded(runs, world, day, date):
    d = runs / world / str(day)
    d.mkdir(parents=True)
    (d / "session.json").write_text(json.dumps({"world": world, "day": day, "date": date, "notes_in_context": True,
                                                "context_message": {"role": "user", "content": f"day {day}"}}))
    (d / "daylog.jsonl").write_text(json.dumps({"ts": f"{date}T09:00:00+09:00", "turn": 1, "role": "user",
                                                "content": f"hello {world} {day}"}) + "\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.runs = Path(tempfile.mkdtemp()) / "runs"  # its own parent, so an escape is visible and never shared
        self.runs.mkdir()
        self.env = {k: os.environ.get(k) for k in ("AFTERGLOW_RUNS", "AFTERGLOW_WORLDS")}
        os.environ["AFTERGLOW_RUNS"] = str(self.runs)
        os.environ["AFTERGLOW_WORLDS"] = str(self.runs / "_worlds")
        (self.runs / "serving").mkdir()
        recorded(self.runs, "alice", 1, "2026-10-27")
        recorded(self.runs, "bob", 1, "2026-10-27")
        self.app = App("alice", 1, "http://127.0.0.1:9", restore=False)

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class OriginTest(Base):
    """A web page elsewhere cannot write; the app's own page, the Electron window and native clients can."""

    def setUp(self):
        super().setUp()
        self.old_app = web.Handler.app
        web.Handler.app = self.app
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_port
        self.note = self.app.session.notes.remember("office", "Room 2.14", day=1, turn=1)["note"]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        web.Handler.app = self.old_app
        super().tearDown()

    def post(self, path, body, headers=None, host=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "text/plain", **(headers or {})}, method="POST")
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def status_of_note(self):
        return next(n["status"] for n in self.app.session.notes.all(include_superseded=True) if n["id"] == self.note["id"])

    def test_a_page_on_another_site_cannot_remove_a_note(self):
        code, body = self.post("/api/notes/forget", {"id": self.note["id"]}, {"Origin": "http://untrusted.example"})
        self.assertEqual(code, 403)
        self.assertEqual(self.status_of_note(), "active")

    def test_dns_rebinding_is_refused(self):
        # a page on evil.example that rebinds its name to 127.0.0.1: Origin matches Host, but not a loopback name
        code, _ = self.post("/api/notes/forget", {"id": self.note["id"]}, {"Origin": f"http://evil.example:{self.port}"},
                            host=f"evil.example:{self.port}")
        self.assertEqual(code, 403)
        self.assertEqual(self.status_of_note(), "active")

    def test_a_file_page_cannot_write(self):
        code, _ = self.post("/api/notes/forget", {"id": self.note["id"]}, {"Origin": "null"})
        self.assertEqual(code, 403)

    def test_the_apps_own_page_on_127_0_0_1_and_localhost(self):
        # The Electron window loads http://127.0.0.1:8650 (desktop/electron/server.js appURL), so it sends this Origin.
        for origin, host in ((f"http://127.0.0.1:{self.port}", f"127.0.0.1:{self.port}"),
                             (f"http://localhost:{self.port}", f"localhost:{self.port}")):
            code, body = self.post("/api/schedule", {"ends_at": "23:00", "auto": False}, {"Origin": origin}, host=host)
            self.assertEqual(code, 200, (origin, body))

    def test_native_clients_send_no_origin(self):
        # curl, the fn helper (URLSession), smoke_explainer_glimmer.py and the restart scripts send no Origin header
        code, body = self.post("/api/schedule", {"ends_at": "23:00", "auto": False})
        self.assertEqual(code, 200, body)
        code, _ = self.post("/api/notes/forget", {"id": self.note["id"], "world": "alice", "day": 1})
        self.assertEqual(code, 200)
        self.assertEqual(self.status_of_note(), "forgotten")

    def test_feature_routes_are_guarded_too(self):
        code, _ = self.post("/api/models/keep-loaded", {"slot": "vision", "on": False}, {"Origin": "http://untrusted.example"})
        self.assertEqual(code, 403)


class ProposalPathTest(Base):
    def test_a_path_shaped_profile_never_writes_outside_runs(self):
        for world in ("../escaped", "/tmp/escaped", "alice/../bob", "nobody"):
            with self.subTest(world=world), self.assertRaises(ValueError):
                self.app.answer_proposal(world, 1, "concise", "concise", "accept")
        self.assertFalse((self.runs.parent / "escaped").exists())
        self.assertFalse((self.runs / "nobody").exists())

    def test_the_day_must_be_a_day_number(self):
        for day in ("1/../../x", -1, 0, True, None):
            with self.subTest(day=day), self.assertRaises(ValueError):
                self.app.answer_proposal("alice", day, "concise", "concise", "accept")

    def test_a_real_profile_still_records(self):
        doc = self.app.answer_proposal("alice", 1, "concise", "concise", "dismiss")
        self.assertEqual(doc["concise:concise"]["answer"], "dismiss")
        self.assertTrue((self.runs / "alice" / "1" / "night" / "steering-decisions.json").exists())


class NoteScopeTest(Base):
    def test_a_stale_screen_cannot_change_another_profiles_note(self):
        a = self.app.session.notes.remember("room", "Alice room", day=1, turn=1)["note"]
        self.app.open_day("bob", 1)
        b = self.app.session.notes.remember("room", "Bob room", day=1, turn=1)["note"]
        self.assertEqual(a["id"], b["id"])  # ids restart per profile
        with self.assertRaises(RuntimeError):
            self.app.change_note(a["id"], "Alice intended edit", world="alice", day=1)
        with self.assertRaises(RuntimeError):
            self.app.change_note(a["id"], forget=True, world="alice", day=1)
        self.assertEqual([(n["value"], n["status"]) for n in self.app.session.notes.all(include_superseded=True)],
                         [("Bob room", "active")])
        self.app.change_note(b["id"], "Bob new room", world="bob", day=1)
        self.assertEqual([n["value"] for n in self.app.session.notes.all()], ["Bob new room"])


class AmbientScopeTest(Base):
    def req(self, body):
        return routes.Request("POST", "/api/ambient", "", {"Content-Type": "application/json"}, json.dumps(body).encode())

    def test_a_switch_while_transcribing_saves_nothing_under_the_new_profile(self):
        def transcribe_then_switch(app, raw, mime):
            app.open_day("bob", 1)
            return {"raw": "my private draft", "clean": "My private draft.", "engine": "test"}

        with mock.patch.object(dictation, "transcribe_audio", side_effect=transcribe_then_switch), \
                mock.patch.object(dictation, "wants_explanation", return_value=False):
            with self.assertRaises(RuntimeError):
                dictation.ambient(self.app, self.req({"audio_b64": "AAAA"}))
        self.assertFalse((self.runs / "bob" / "1" / "threads").exists() and any((self.runs / "bob" / "1" / "threads").iterdir()))
        self.assertFalse((self.runs / "alice" / "1" / "threads").exists() and any((self.runs / "alice" / "1" / "threads").iterdir()))

    def test_a_switch_before_an_explanation_refuses_it(self):
        def transcribe_then_switch(app, raw, mime):
            app.open_day("bob", 1)
            return {"raw": "explain this", "clean": "Explain this.", "engine": "test"}

        with mock.patch.object(dictation, "transcribe_audio", side_effect=transcribe_then_switch), \
                mock.patch.object(dictation, "wants_explanation", return_value=True):
            with self.assertRaises(RuntimeError):
                dictation.ambient(self.app, self.req({"audio_b64": "AAAA", "crop_png_b64": "AAAA"}))

    def test_no_switch_still_records(self):
        with mock.patch.object(dictation, "transcribe_audio",
                               return_value={"raw": "hi", "clean": "Hi.", "engine": "test"}), \
                mock.patch.object(dictation, "wants_explanation", return_value=False):
            out = dictation.ambient(self.app, self.req({"audio_b64": "AAAA"}))
        self.assertEqual(out["mode"], "paste")
        self.assertTrue(any((self.runs / "alice" / "1" / "threads").iterdir()))


if __name__ == "__main__":
    unittest.main()
