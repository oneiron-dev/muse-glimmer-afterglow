"""Offline tests for the notes store and the world tools (no server needed).

  python3 -m unittest discover -s app/tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow.notes import LocalJsonNotes, conversation_ref  # noqa: E402
from afterglow.tools import ToolBox, WorldState  # noqa: E402


class NotesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.notes = LocalJsonNotes(os.path.join(self.tmp, "notes.json"))

    def test_correction_supersedes(self):
        self.notes.remember("supplier call time", "Tuesdays at 10:30", day=1, turn=1)
        r = self.notes.remember("Supplier  call time", "Wednesdays at 09:00", day=1, turn=5)
        self.assertEqual(r["superseded"]["value"], "Tuesdays at 10:30")
        active = self.notes.all()
        self.assertEqual([n["value"] for n in active], ["Wednesdays at 09:00"])
        old = self.notes.all(include_superseded=True)[0]
        self.assertEqual((old["status"], old["superseded_by"]), ("superseded", active[0]["id"]))

    def test_same_value_is_unchanged(self):
        self.notes.remember("test field", "Tolvik Flats")
        r = self.notes.remember("test field", "tolvik flats")
        self.assertTrue(r["unchanged"])
        self.assertEqual(len(self.notes.all(include_superseded=True)), 1)

    def test_recall_ranks_key_matches(self):
        self.notes.remember("CNC nickname", "Old Gus")
        self.notes.remember("laser cutter location", "the mezzanine")
        self.notes.remember("test pilot", "Sunniva Brekke")
        self.assertEqual(self.notes.recall("where is the laser cutter?")[0]["value"], "the mezzanine")
        self.assertEqual(self.notes.recall("zebra"), [])

    def test_export_and_seed(self):
        self.notes.remember("employer", "Kestrel Fabrication", day=1)
        out = os.path.join(self.tmp, "day1-notes.json")
        self.notes.export_day(out, world="w", day=1)
        doc = json.loads(Path(out).read_text())
        self.assertEqual((doc["world"], doc["day"], len(doc["notes"])), ("w", 1, 1))
        day2 = LocalJsonNotes(os.path.join(self.tmp, "d2.json"), seed_from=out)
        self.assertEqual(day2.all()[0]["value"], "Kestrel Fabrication")

    def test_vault_conversation_per_world_day(self):
        a, b = conversation_ref("w1-ines-varga", 1), conversation_ref("w1-ines-varga", 2)
        self.assertNotEqual(a, conversation_ref("w1-ines-varga", 1, version=1))
        self.assertEqual(a, conversation_ref("w1-ines-varga", 1))
        self.assertNotEqual(a, b)
        self.assertRegex(a, r"^[0-9a-f]{32}$")
        self.assertGreaterEqual(int(a[:16], 16), 1 << 62)


class ToolsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.notes = LocalJsonNotes(os.path.join(tmp, "notes.json"))
        seed = {"calendar": [{"id": "E001", "date": "2026-10-27", "time": "10:30", "title": "supplier call"}],
                "tasks": [{"id": "T001", "title": "send status note", "done": False}]}
        self.box = ToolBox(self.notes, WorldState(os.path.join(tmp, "world_state.json"), seed=seed), day=1)

    def test_remember_and_recall(self):
        out, written = self.box.run("remember", {"key": "QA engineer", "value": "Yusuf Demir"}, turn=3)
        self.assertEqual(len(written), 1)
        out, _ = self.box.run("recall", {"query": "QA engineer"})
        self.assertEqual(out["notes"][0]["value"], "Yusuf Demir")

    def test_calendar_and_tasks(self):
        out, _ = self.box.run("calendar.add_event", {"date": "2026-10-27", "time": "08:00", "title": "standup"})
        self.assertEqual(out["added"]["id"], "E002")
        out, _ = self.box.run("calendar.list_events", {"date": "2026-10-27"})
        self.assertEqual([e["time"] for e in out["events"]], ["08:00", "10:30"])
        self.box.run("tasks.complete", {"task_id": "T001"})
        out, _ = self.box.run("tasks.list", {})
        self.assertEqual(out["tasks"], [])

    def test_bad_arguments_and_unknown_tool(self):
        out, _ = self.box.run("calendar.add_event", {"date": "2026-10-27"})
        self.assertIn("bad arguments", out["error"])
        out, _ = self.box.run("email.send", {})
        self.assertIn("unknown tool", out["error"])


if __name__ == "__main__":
    unittest.main()
