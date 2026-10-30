"""Offline tests for night-found corrections back into the served notes (notes.apply_night_links,
notes.undo_night_link, agent.apply_night_links at day start; contract: runs/<w>/<d>/night/night-links.json).

  python3 -m unittest discover -s app/tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow.agent import apply_night_links  # noqa: E402
from afterglow.notes import LocalJsonNotes  # noqa: E402

LINK = {"old_id": "N0001", "old_key": "front till cover", "old_value": "Lena Frost", "new_id": "N0002",
        "new_key": "weekend clerk", "new_value": "Mia Holt", "linked_by": "readout", "p": 0.93,
        "guard": {"rule": "margin", "passed": True}, "change_word": "now", "said": "Mia Holt is our weekend clerk now",
        "reason": 'You said "Mia Holt is our weekend clerk now", so "front till cover: Lena Frost" was replaced.'}


class NightLinksTest(unittest.TestCase):
    def setUp(self):
        self.runs = Path(tempfile.mkdtemp())
        os.environ["AFTERGLOW_RUNS"] = str(self.runs)
        (self.runs / "serving").mkdir()

    def tearDown(self):
        os.environ.pop("AFTERGLOW_RUNS", None)

    def notes(self, day_dir):
        n = LocalJsonNotes(day_dir / "notes.json")
        n.remember("front till cover", "Lena Frost", day=1, turn=1)
        n.remember("weekend clerk", "Mia Holt", day=5, turn=3)
        return n

    def test_a_link_hides_the_old_note_and_keeps_it_on_disk(self):
        n = self.notes(self.runs / "w" / "6")
        self.assertEqual(n.apply_night_links([LINK], 5), [LINK])
        self.assertEqual([x["id"] for x in n.all()], ["N0002"])  # out of recall and the notes block
        self.assertEqual(n.recall("front till cover"), [])
        old = json.loads((self.runs / "w" / "6" / "notes.json").read_text())["notes"][0]
        self.assertEqual((old["status"], old["superseded_by_night"]["by"], old["superseded_by_night"]["night_day"],
                          old["superseded_by_night"]["p"]), ("superseded", "N0002", 5, 0.93))
        self.assertIsNone(old["superseded_by"])  # the day's own link field is untouched
        self.assertEqual(n.apply_night_links([LINK], 5), [])  # already applied

    def test_undo_restores_the_note_and_is_never_reapplied(self):
        n = self.notes(self.runs / "w" / "6")
        n.apply_night_links([LINK], 5)
        undone = n.undo_night_link("N0001", day=6)
        self.assertEqual(undone["by"], "N0002")
        self.assertEqual(sorted(x["id"] for x in n.all()), ["N0001", "N0002"])
        old = n.notes[0]
        self.assertNotIn("superseded_by_night", old)
        self.assertEqual((old["night_links_undone"][0]["by"], old["night_links_undone"][0]["day"]), ("N0002", 6))
        self.assertEqual(n.apply_night_links([LINK], 5), [])  # the user's undo stands
        with self.assertRaises(KeyError):
            n.undo_night_link("N0001")

    def test_a_link_to_a_note_that_is_gone_or_already_replaced_is_skipped(self):
        n = self.notes(self.runs / "w" / "6")
        n.remember("weekend clerk", "Ola Berg", day=6, turn=1)  # the user replaced N0002 today
        self.assertEqual(n.apply_night_links([LINK, dict(LINK, old_id="N0099")], 5), [])

    def test_a_proposed_link_is_applied_only_on_the_users_link_and_keep_both_is_remembered(self):
        n = self.notes(self.runs / "w" / "6")
        prop = dict(LINK, state="proposed", reason="kept both: name -> name",
                    question='Did "weekend clerk: Mia Holt" replace "front till cover: Lena Frost"?',
                    guard={"rule": "shape", "passed": False, "why": "name -> name"})
        self.assertEqual(n.apply_night_links([], 5), [])  # proposals are never applied on their own
        rec = n.answer_night_proposal(prop, "keep_both", 5, day=6)
        self.assertEqual((rec["answer"], rec["by"]), ("keep_both", "N0002"))
        self.assertEqual(len(n.all()), 2)  # both notes stay
        self.assertEqual(n.notes[0]["night_links_kept_both"][0]["night_day"], 5)
        n.answer_night_proposal(prop, "link", 5, day=6)
        self.assertEqual([x["id"] for x in n.all()], ["N0002"])
        self.assertTrue(n.notes[0]["superseded_by_night"]["by_user"])
        with self.assertRaises(ValueError):
            n.answer_night_proposal(prop, "maybe", 5)

    def write_night(self, day, applies_from):
        d = self.runs / "w" / str(day) / "night"
        d.mkdir(parents=True)
        (d / "night-links.json").write_text(json.dumps({"world": "w", "day": day, "applies_from_day": applies_from,
                                                        "rule": "test", "links": [LINK]}))
        self.notes(self.runs / "w" / str(day))
        return self.runs / "w" / str(day)

    def rule(self, **conf):
        (self.runs / "serving" / "serving-rule.json").write_text(json.dumps({"checks": [], **conf}))

    def test_day_start_applies_the_last_nights_links_only_when_switched_on_from_that_night(self):
        prev = self.write_night(6, 7)
        n = LocalJsonNotes(self.runs / "w" / "7" / "notes.json", seed_from=prev / "notes.json")
        self.assertIsNone(apply_night_links(n, "w", 7, prev))  # no setting: off
        self.rule(night_links={"from_night": 7})
        self.assertIsNone(apply_night_links(n, "w", 7, prev))  # night 6 is before the first night that applies
        self.rule(night_links={"from_night": 6})
        self.assertEqual(apply_night_links(n, "w", 7, prev), {"night_day": 6, "applied": [{"old_id": "N0001", "new_id": "N0002"}],
                                                               "listed": 1, "proposed": []})
        self.assertEqual([x["id"] for x in n.all()], ["N0002"])
        # yesterday's own notes file is untouched: the night reads it as the day it trained on
        self.assertEqual(len(LocalJsonNotes(prev / "notes.json").all()), 2)

    def test_the_day_log_records_the_applied_links_once_and_the_ui_history_skips_it(self):
        from afterglow.agent import DaySession
        self.rule(night_links={"from_night": 6})
        self.write_night(6, 7)
        for _ in range(2):  # a second session on the same day (a restart) logs nothing new
            s = DaySession("w", 7, decide_supersede=False, decide_route=False, web_search=False)
        recs = [json.loads(x) for x in s.daylog_path.read_text().splitlines()]
        self.assertEqual([r["role"] for r in recs], ["night_links"])
        self.assertEqual((recs[0]["night_day"], recs[0]["applied"], recs[0]["answers"]),
                         (6, [{"old_id": "N0001", "new_id": "N0002"}], []))
        self.assertEqual(s.history(), [])

    def test_a_file_for_another_day_is_not_applied(self):
        self.rule(night_links={"from_night": 1})
        prev = self.write_night(6, 8)
        n = LocalJsonNotes(self.runs / "w" / "7" / "notes.json", seed_from=prev / "notes.json")
        self.assertIsNone(apply_night_links(n, "w", 7, prev))


if __name__ == "__main__":
    unittest.main()
