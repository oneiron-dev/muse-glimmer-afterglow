"""Offline tests for the supersede decision at remember time (agent.py, decide/decider.py faked; no server).

  python3 -m unittest discover -s app/tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import decide.decider  # noqa: E402
from afterglow.agent import DaySession  # noqa: E402


class FakeDecider:
    """supersede_link: one candidate (the earlier note) with a set "replaces" p; pair: a set reasoned answer."""
    one_pass_p, reasoned = 0.0, None
    calls = []

    def __init__(self, endpoint, adapter_path=None, slot=None, thresholds=None, trace=None):
        self.trace = trace

    def supersede_link(self, earlier, new_note, accept_p=0.9):
        p = FakeDecider.one_pass_p
        d = {"candidate": earlier[0]["id"], "pick": "1", "p": p, "yes": True, "ms": 150.0}
        return {"link": earlier[0]["id"] if p >= accept_p else None, "decisions": [d], "ms": 150.0}

    def pair(self, kind, a, b):
        FakeDecider.calls.append((kind, a, b))
        return dict(FakeDecider.reasoned)


class SupersedeDecisionTest(unittest.TestCase):
    def setUp(self):
        self.real = decide.decider.Decider
        decide.decider.Decider = FakeDecider
        FakeDecider.calls = []
        os.environ["AFTERGLOW_RUNS"] = tempfile.mkdtemp()
        self.s = DaySession("w-test", 2, decide_supersede=True, decide_reasoned=True)
        self.s.notes.remember("morning_location", "Room 312 desk", day=1, turn=4)

    def tearDown(self):
        decide.decider.Decider = self.real
        os.environ.pop("AFTERGLOW_RUNS", None)

    def save(self):
        out, _ = self.s.tools.run("remember", {"key": "office desk room", "value": "Room 318"}, turn=9)
        return out

    def test_sure_in_one_pass_links_without_reasoning(self):
        FakeDecider.one_pass_p = 0.95
        out = self.save()
        self.assertEqual(out["replaced"]["decided"]["by"], "one-pass decision")
        self.assertEqual(FakeDecider.calls, [])

    def test_unsure_reasons_once_and_links_on_replaces(self):
        FakeDecider.one_pass_p = 0.7
        FakeDecider.reasoned = {"pick": "1", "p": 0.93, "yes": True, "ms": 2400.0, "reasoning_tokens": 300}
        out = self.save()
        self.assertEqual(out["replaced"]["decided"]["by"], "thought it through")
        self.assertEqual(len(FakeDecider.calls), 1)

    def test_reasoning_cut_at_its_budget_keeps_both(self):
        FakeDecider.one_pass_p = 0.7
        FakeDecider.reasoned = {"pick": "1", "p": 0.99, "yes": True, "ms": 16000.0, "reasoning_tokens": 1024}
        out = self.save()
        self.assertNotIn("replaced", out)
        self.assertEqual((out["decision"]["reason"], out["decision"]["outcome"]), ("reasoning_cap", "undecided: keep both"))
        self.assertIsNone(out["decision"]["reasoned"]["pick"])
        self.assertEqual(len(self.s.notes.all()), 2)

    def test_by_default_unsure_keeps_both_without_reasoning(self):
        s = DaySession("w-test", 2, decide_supersede=True)  # the reasoning escalation is off unless the notes say on
        s.notes.remember("morning_location", "Room 312 desk", day=1, turn=4)
        FakeDecider.one_pass_p = 0.7
        out, _ = s.tools.run("remember", {"key": "office desk room", "value": "Room 318"}, turn=9)
        self.assertEqual((out["decision"]["by"], FakeDecider.calls), ("one-pass decision", []))
        kept = next(n for n in s.notes.all() if n["key"] == "office desk room")
        self.assertFalse(kept["decided"]["linked"])

    def test_the_serving_notes_switch_it_on_from_their_day(self):
        serving = Path(os.environ["AFTERGLOW_RUNS"]) / "serving"
        serving.mkdir(exist_ok=True)
        (serving / "serving-rule.json").write_text(json.dumps(
            {"checks": [], "supersede_decision": {"from_day": 4, "link_p": 0.9, "reasoned": False}}))
        self.assertIsNone(DaySession("w-on", 3).tools.decide_supersede)
        self.assertIsNotNone(DaySession("w-on", 4).tools.decide_supersede)

    def test_unlikely_keeps_both_without_reasoning(self):
        FakeDecider.one_pass_p = 0.3
        out = self.save()
        self.assertNotIn("replaced", out)
        self.assertEqual(FakeDecider.calls, [])


if __name__ == "__main__":
    unittest.main()
