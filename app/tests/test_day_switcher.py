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
