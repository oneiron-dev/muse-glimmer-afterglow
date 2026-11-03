"""Offline tests for what the morning report reads from the run files (afterglow/morning.py, web.App.report):
the night of record (the kept arm the serving manifest names), every night of a person, the fresh-200 line, speed.

  python3 -m unittest discover -s app/tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import morning  # noqa: E402
from afterglow.web import App  # noqa: E402

FRESH_NOTE = ("Fresh-200 847ef7a7 (nelly, base+arm one run): honesty +16.0 [+8,+24] passes -5 bound; wrong 83->47 "
              "(9 made up), declines 100->138; ask 18->10 pass. Keep CONFIRMED of record.")


def write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))


def decision(kind, honesty=None, failed=(), **extra):
    checks = {"learned_gate": True, "forgot_gate": True, "abstention_honesty": True}
    checks.update({k: False for k in failed})
    doc = {"decision": kind, "checks": checks, **extra}
    if honesty is not None:
        doc["abstention_honesty"] = {"honesty_change": honesty, "honesty_change_ci95": [honesty - 8, honesty + 8]}
    return doc


class MorningReportTest(unittest.TestCase):
    def setUp(self):
        self.runs = Path(tempfile.mkdtemp())
        os.environ["AFTERGLOW_RUNS"] = str(self.runs)
        os.environ["AFTERGLOW_WORLDS"] = str(self.runs / "_worlds")
        w = self.runs / "w"
        write(w / "1" / "night" / "decision.json", decision("keep"))
        write(w / "2" / "night" / "decision.json", decision("rollback", -12.0, ["abstention_honesty"]))
        # day 3: the night of record rolled back, the arm night-3b was kept and is served
        write(w / "3" / "night" / "decision.json", decision("rollback", -20.0, ["abstention_honesty"]))
        write(w / "3" / "night-3b" / "decision.json",
              decision("keep", 1.5, fresh_confirm="pass", fresh_confirm_note=FRESH_NOTE))
        # day 4: no night of record; its only arm rolled back
        write(w / "4" / "night-4a" / "decision.json", decision("rollback", 2.0, ["forgot_gate"]))
        (w / "5").mkdir()  # today: no night yet
        for night in ("1/night", "2/night", "3/night", "3/night-3b"):
            write(w / night / "eval.json", {"facts": [], "learned": {"facts_learned": {"A": 0, "B": 3}}})
        write(self.runs / "serving" / "adapters.json", {"adapters": [
            {"world": "w", "day": 1, "path": "/x/w-d1.gguf", "sha256": "a" * 64, "decision": "keep", "id": 0},
            {"world": "w", "day": 3, "path": "/x/w-d3.gguf", "sha256": "b" * 64, "decision": "keep",
             "arm": "night-3b", "night_dir": "3/night-3b", "id": 1},
            {"world": "w", "day": 4, "arm": "night-4a", "night_dir": "4/night-4a", "path": "/x/w-d4a.gguf",
             "sha256": "c" * 64, "id": 2}]})

    def tearDown(self):
        os.environ.pop("AFTERGLOW_RUNS", None)
        os.environ.pop("AFTERGLOW_WORLDS", None)

    def app(self, world="w", day=5):
        app = App.__new__(App)  # no model server: the report reads files only

        class Notes:
            notes = []

        class Session:
            pass

        app.session = Session()
        app.session.world, app.session.day, app.session.notes = world, day, Notes()
        return app

    def test_the_kept_arm_is_the_night_of_record_for_its_day_only(self):
        self.assertEqual(morning.served("w")["day"], 3)
        self.assertEqual(morning.night_dir("w", 3), "3/night-3b")
        self.assertEqual(morning.night_dir("w", 2), "2/night")
        self.assertIsNone(morning.served("nobody"))

    def test_nights_lists_kept_and_rolled_back_with_the_failed_check(self):
        n = morning.nights("w")
        self.assertEqual([(x["day"], x["decision"]) for x in n],
                         [(1, "keep"), (2, "rollback"), (3, "keep"), (4, "rollback")])  # day 5 has no night yet
        self.assertEqual(n[1]["failed"], ["abstention_honesty"])
        self.assertEqual(n[1]["honesty_change"], -12.0)
        self.assertTrue(n[2]["served"])
        self.assertEqual(n[2]["honesty_change"], 1.5)  # the kept arm's, not the rolled-back night of record's
        self.assertEqual(n[3]["failed"], ["forgot_gate"])  # every arm of day 4 rolled back
        self.assertEqual(n[3]["night_dir"], "4/night-4a")

    def test_fresh_reads_the_confirmation_note(self):
        f = morning.fresh({"fresh_confirm": "pass", "fresh_confirm_note": FRESH_NOTE})
        self.assertEqual(f["change"], 16.0)
        self.assertEqual(f["ci95"], [8.0, 24.0])
        self.assertEqual(f["ask_back"], [18, 10])
        self.assertTrue(f["passed"])
        self.assertIsNone(morning.fresh({"decision": "keep"}))
        self.assertEqual(morning.fresh({"fresh_confirm_note": "honesty -2.5 [-10.5,+5.5] passes"})["change"], -2.5)

    def test_report_defaults_to_the_night_before_today_and_reads_the_kept_arm(self):
        r = self.app(day=4).report()
        self.assertEqual(r["day"], 3)
        self.assertEqual(r["night_of_record"], "3/night-3b")
        self.assertEqual(r["decision"]["decision"], "keep")
        self.assertEqual(r["fresh"]["change"], 16.0)
        self.assertEqual([x["day"] for x in r["nights"]], [1, 2, 3, 4])

    def test_report_for_a_day_shows_a_rollback_as_it_was(self):
        r = self.app(day=5).report("w", 2)
        self.assertEqual(r["night_of_record"], "2/night")
        self.assertEqual(r["decision"]["decision"], "rollback")
        self.assertIsNone(r["fresh"])

    def test_report_for_another_person_defaults_to_their_served_night(self):
        write(self.runs / "v" / "7" / "night" / "eval.json", {"facts": []})
        write(self.runs / "v" / "7" / "night" / "decision.json", decision("keep", 0.5))
        write(self.runs / "v" / "8" / "night" / "eval.json", {"facts": []})
        write(self.runs / "v" / "8" / "night" / "decision.json", decision("rollback", -9.0, ["abstention_honesty"]))
        doc = json.loads((self.runs / "serving" / "adapters.json").read_text())
        doc["adapters"].append({"world": "v", "day": 7, "sha256": "d" * 64, "decision": "keep", "id": 3})
        write(self.runs / "serving" / "adapters.json", doc)
        r = self.app().report("v")
        self.assertEqual(r["day"], 7)
        self.assertEqual(r["decision"]["decision"], "keep")

    def test_speed_reads_the_drafter_results(self):
        s = morning.speed()
        if s is None:
            self.skipTest("drafter/results not in this checkout")
        self.assertGreater(s["ours_tok_s"], s["stock_tok_s"])
        self.assertEqual(s["gain_pct"], round(100 * (s["ours_tok_s"] / s["stock_tok_s"] - 1), 1))
        self.assertTrue(s["series"])


if __name__ == "__main__":
    unittest.main()
