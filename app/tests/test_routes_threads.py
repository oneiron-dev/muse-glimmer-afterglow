"""The route registry (feature modules add routes without editing web.py) and subthreads (kept apart from the day log)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import history_api, routes, threads  # noqa: E402


class RoutesTest(unittest.TestCase):
    def test_threads_routes_are_registered_and_wildcards_match(self):
        t = routes.table()
        self.assertIn(("GET", "/api/threads"), t)
        routes._table = {("GET", "/api/x/*"): "wild", ("GET", "/api/x"): "exact"}
        try:
            self.assertEqual(routes.find("GET", "/api/x"), ("exact", ""))
            self.assertEqual(routes.find("GET", "/api/x/abc/def"), ("wild", "abc/def"))
            self.assertEqual(routes.find("POST", "/api/x"), (None, ""))
        finally:
            routes._table = None

    def test_request_parses_json_and_keeps_raw_bytes(self):
        r = routes.Request("POST", "/api/a", "id=t1&x=2", {"Content-Type": "application/json"}, b'{"k": 1}')
        self.assertEqual((r.body, r.query), ({"k": 1}, {"id": "t1", "x": "2"}))
        r2 = routes.Request("POST", "/api/a", "", {"Content-Type": "audio/webm"}, b"\x00\x01")
        self.assertIsNone(r2.body)
        self.assertEqual(r2.raw, b"\x00\x01")


class ThreadsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AFTERGLOW_RUNS"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("AFTERGLOW_RUNS", None)
        self.tmp.cleanup()

    def test_a_subthread_lives_beside_the_day_log_never_in_it(self):
        e = threads.create("w1", 3, "explain", "The Settings screen", at_turn=4, meta={"app": "Afterglow"})
        self.assertEqual(e["id"], "t1")
        threads.append("w1", 3, "t1", {"role": "user", "content": "what is this?"})
        threads.append("w1", 3, "t1", {"role": "assistant", "content": "Your model settings."})
        got = threads.get("w1", 3, "t1")
        self.assertEqual([r["role"] for r in got["records"]], ["user", "assistant"])
        self.assertEqual(threads.create("w1", 3, "dictation", "A note")["id"], "t2")
        self.assertTrue(threads.update("w1", 3, "t1", kept=True)["kept"])
        self.assertFalse((Path(self.tmp.name) / "w1" / "3" / "daylog.jsonl").exists())
        self.assertEqual(len(json.loads((Path(self.tmp.name) / "w1" / "3" / "threads" / "index.json").read_text())), 2)

    def test_bad_ids_and_kinds_are_refused(self):
        with self.assertRaises(ValueError):
            threads.create("w1", 1, "chat", "x")
        with self.assertRaises(ValueError):
            threads.get("w1", 1, "../daylog")
        with self.assertRaises(ValueError):
            threads.append("w1", 1, "t9", {"role": "user", "content": "x"})


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AFTERGLOW_RUNS"] = self.tmp.name
        for day, date, lines in ((1, "2026-10-27", [("user", "Hello", 1), ("assistant", "Hi.", 1)]), (2, "2026-10-28", [])):
            d = Path(self.tmp.name) / "w1" / str(day)
            d.mkdir(parents=True)
            (d / "session.json").write_text(json.dumps({"world": "w1", "day": day, "date": date}))
            (d / "daylog.jsonl").write_text("".join(json.dumps({"role": r, "content": c, "turn": t, "ts": "x",
                                                                  "messages": [{"big": True}]}) + "\n" for r, c, t in lines))

    def tearDown(self):
        os.environ.pop("AFTERGLOW_RUNS", None)
        self.tmp.cleanup()

    def test_an_earlier_day_reads_back_trimmed_and_the_summary_lists_every_day(self):
        app = type("A", (), {"session": type("S", (), {"world": "w1", "day": 2})()})()
        h = history_api.day_history(app, routes.Request("GET", "/api/day/history", "day=1", {}, None))
        self.assertEqual((h["date"], [r["content"] for r in h["history"]]), ("2026-10-27", ["Hello", "Hi."]))
        self.assertNotIn("messages", h["history"][0])
        s = history_api.days_summary(app, routes.Request("GET", "/api/days/summary", "", {}, None))
        self.assertEqual([(d["day"], d["turns"], d["first"]) for d in s["days"]], [(2, 0, ""), (1, 1, "Hello")])
        with self.assertRaises(ValueError):
            history_api.day_history(app, routes.Request("GET", "/api/day/history", "day=9", {}, None))


if __name__ == "__main__":
    unittest.main()
