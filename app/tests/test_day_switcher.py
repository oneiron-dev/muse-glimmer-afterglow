"""Offline tests for the day switcher (web.App.recorded_days, web.App.open_day): one app opens another recorded day.

  python3 -m unittest discover -s app/tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow.web import App  # noqa: E402


def recorded(runs, world, day, date):
    d = runs / world / str(day)
    d.mkdir(parents=True)
    (d / "session.json").write_text(json.dumps({"world": world, "day": day, "date": date, "notes_in_context": True,
                                                "context_message": {"role": "user", "content": f"day {day}"}}))
    (d / "daylog.jsonl").write_text(json.dumps({"ts": f"{date}T09:00:00+09:00", "turn": 1, "role": "user",
                                                "content": f"hello from day {day}"}) + "\n")


class DaySwitcherTest(unittest.TestCase):
    def setUp(self):
        self.runs = Path(tempfile.mkdtemp())
        os.environ["AFTERGLOW_RUNS"] = str(self.runs)
        os.environ["AFTERGLOW_WORLDS"] = str(self.runs / "_worlds")
        (self.runs / "serving").mkdir()
        recorded(self.runs, "w", 1, "2026-10-27")
        recorded(self.runs, "w", 2, "2026-10-28")
        recorded(self.runs, "v", 4, "2026-10-30")
        (self.runs / "w" / "3").mkdir()  # a folder with no session.json is not a recorded day
        self.app = App("w", 1, "http://127.0.0.1:9", restore=False)

    def tearDown(self):
        os.environ.pop("AFTERGLOW_RUNS", None)
        os.environ.pop("AFTERGLOW_WORLDS", None)

    def test_lists_recorded_days_per_person(self):
        self.assertEqual([(p["world"], p["days"]) for p in self.app.recorded_days()], [("v", [4]), ("w", [1, 2])])

    def test_opens_another_recorded_day_with_its_date_and_chat(self):
        r = self.app.open_day("w", 2)
        self.assertEqual(r, {"world": "w", "day": 2, "date": "2026-10-28"})
        s = self.app.session
        self.assertEqual((s.world, s.day), ("w", 2))
        self.assertEqual([m["content"] for m in s.history() if m.get("role") == "user"], ["hello from day 2"])
        self.assertIs(s.client, self.app.client)  # the same server client and slot

    def test_opens_another_person(self):
        self.app.open_day("v", 4)
        self.assertEqual((self.app.session.world, self.app.session.day), ("v", 4))

    def test_a_profile_made_in_the_app_opens_its_first_day(self):
        r = self.app.create_profile("  Mira  Holt ", "Runs a small bakery")
        self.assertEqual((r["world"], r["day"], r["name"]), ("p-mira-holt", 1, "Mira Holt"))
        self.assertEqual((self.app.session.world, self.app.session.day), ("p-mira-holt", 1))
        self.assertIn("Mira Holt's personal assistant", self.app.session.system_message["content"])
        self.assertIn("p-mira-holt", [p["world"] for p in self.app.recorded_days()])
        self.assertNotIn("_profiles", [p["world"] for p in self.app.recorded_days()])
        self.assertEqual(self.app.create_profile("Mira Holt")["world"], "p-mira-holt-2")
        with self.assertRaises(ValueError):
            self.app.create_profile("   ")

    def test_attachments_go_into_the_message(self):
        a = self.app.attach("../notes.md", "# Larkspur\nShip date 28 April.")
        self.assertEqual((a["id"], a["name"], a["truncated"]), ("A001", "notes.md", False))
        text, extra = self.app.message_with_attachments("Read this", [a["id"]])
        self.assertTrue(text.startswith("Read this\n\n[Attached file: notes.md]\n# Larkspur"))
        self.assertEqual((extra["typed"], extra["attachments"][0]["name"]), ("Read this", "notes.md"))
        self.assertEqual([x["id"] for x in self.app.attachments()], ["A001"])
        with self.assertRaises(ValueError):
            self.app.message_with_attachments("x", ["A999"])
        with self.assertRaises(ValueError):
            self.app.attach("empty.txt", "   ")

    def test_files_attached_together_get_their_own_ids(self):
        # The composer uploads several files at once; each request runs on its own server thread.
        import threading
        start, out = threading.Barrier(8), []

        def upload(i):
            start.wait()
            out.append(self.app.attach(f"f{i}.txt", f"body {i}"))

        threads = [threading.Thread(target=upload, args=(i,)) for i in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        ids = sorted(a["id"] for a in out)
        self.assertEqual(ids, [f"A{i:03d}" for i in range(1, 9)])
        self.assertEqual(sorted(x["id"] for x in self.app.attachments()), ids)
        bodies = {a["id"]: a["name"] for a in out}
        text, _ = self.app.message_with_attachments("all", ids)
        for i in range(8):
            self.assertIn(f"body {i}", text)
        self.assertEqual(len(set(bodies.values())), 8)

    def test_day_end_schedule(self):
        self.assertEqual(self.app.schedule(), {"ends_at": "23:00", "auto": False})  # off until the user turns it on
        self.assertEqual(self.app.schedule("7:5", True)["ends_at"], "07:05")
        self.assertEqual(self.app.schedule(), {"ends_at": "07:05", "auto": True, "set_at": self.app.schedule()["set_at"]})
        with self.assertRaises(ValueError):
            self.app.schedule("25:00")

    def test_refuses_a_day_that_was_never_opened(self):
        with self.assertRaises(ValueError):
            self.app.open_day("w", 3)
        with self.assertRaises(ValueError):
            self.app.open_day("w", 9)
        self.assertFalse((self.runs / "w" / "9").exists())  # no stray day folder
        outside = self.runs.parent / f"{self.runs.name}-outside" / "1"
        outside.mkdir(parents=True)
        (outside / "session.json").write_text("{}")
        with self.assertRaises(ValueError):  # a path is not a person
            self.app.open_day(f"../{self.runs.name}-outside", 1)
        self.assertEqual(self.app.session.day, 1)


if __name__ == "__main__":
    unittest.main()
