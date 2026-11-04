"""The pages the recording can fall back on are served as before (/classic = the day page from before the app, /report,
/night), and nothing starts on its own: the night's auto-start is off by default, and no model download or model
server starts unless the user asks in Settings."""
import os
import sys
import tempfile
import threading
import types
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import modelsetup, web  # noqa: E402


class PagesTest(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, r.read().decode()

    def test_the_fallback_pages_are_served(self):
        status, classic = self.get("/classic")
        self.assertEqual(status, 200)
        self.assertIn('href="/app.css"', classic)
        self.assertIn("app.js", classic)
        self.assertEqual(classic, (web.WEB / "classic.html").read_text())
        for path, name in (("/report", "report.html"), ("/night", "night.html")):
            status, body = self.get(path)
            self.assertEqual((status, body), (200, (web.WEB / name).read_text()), path)
        status, app = self.get("/memory")
        self.assertIn('id="root"', app)


class NothingStartsByItselfTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AFTERGLOW_RUNS"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("AFTERGLOW_RUNS", None)
        self.tmp.cleanup()

    def test_the_night_does_not_start_by_itself_by_default(self):
        fake = types.SimpleNamespace(session=types.SimpleNamespace(world="w1"), DEFAULT_SCHEDULE=web.App.DEFAULT_SCHEDULE)
        self.assertFalse(web.App.schedule(fake)["auto"])
        self.assertFalse((Path(self.tmp.name) / "w1" / "schedule.json").exists())  # reading writes nothing

    def test_no_download_and_no_saved_model_until_the_user_asks(self):
        self.assertEqual(modelsetup.Download().state, {"state": "idle"})
        self.assertIsNone(modelsetup.saved())
        self.assertFalse(modelsetup.LocalServer().running())


if __name__ == "__main__":
    unittest.main()
