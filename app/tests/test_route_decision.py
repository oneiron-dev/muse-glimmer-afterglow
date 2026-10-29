"""Offline tests for the route decision at recall time (agent.py, tools.py; decide/decider.py faked; no server).

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
from afterglow.agent import DaySession, route_setting  # noqa: E402

D1B = "/data/afterglow/adapters/decision-d1b.gguf"


class FakeClient:
    endpoint = "http://127.0.0.1:9"
    adapter_path = None

    def __init__(self, loaded):
        self.loaded = loaded

    def adapters(self):
        return self.loaded


class FakeDecider:
    """route: picks the note whose key is FakeDecider.pick_key (None = 0, ask), at p 0.97, below the threshold when
    FakeDecider.p is under it."""
    pick_key, p, made, calls = None, 0.97, [], []

    def __init__(self, endpoint, adapter_path=None, slot=None, thresholds=None, trace=None):
        FakeDecider.made.append({"adapter_path": adapter_path, "slot": slot, "trace": trace, "thresholds": thresholds})
        self.threshold = (thresholds or {}).get("route", 0.0)

    def route(self, first, notes, question):
        FakeDecider.calls.append((first, [n["key"] for n in notes], question))
        note = next((n for n in notes if n["key"] == FakeDecider.pick_key), None)
        path = "one_pass" if FakeDecider.p >= self.threshold else "look_up"
        pick = str(notes.index(note) + 1) if note else "0"
        return {"kind": "route", "prompt_id": "route-v1", "note": note, "ask": note is None and path == "one_pass",
                "path": path, "pick": pick, "p": FakeDecider.p, "label_mass": 0.99, "ms": 470.0}


class RouteDecisionTest(unittest.TestCase):
    def setUp(self):
        self.real = decide.decider.Decider
        decide.decider.Decider = FakeDecider
        FakeDecider.pick_key, FakeDecider.p, FakeDecider.made, FakeDecider.calls = None, 0.97, [], []
        os.environ["AFTERGLOW_RUNS"] = tempfile.mkdtemp()

    def tearDown(self):
        decide.decider.Decider = self.real
        os.environ.pop("AFTERGLOW_RUNS", None)

    def session(self, loaded=({"id": 0, "path": "/data/afterglow/adapters/w1-ines-varga-d1.gguf"},
                              {"id": 12, "path": D1B}), **kw):
        s = DaySession("w-test", 4, FakeClient(list(loaded)), decide_route=True, **kw)
        s.notes.remember("label printer name", "Zebra ZT411", day=1, turn=2)
        s.notes.remember("standup time", "10:30", day=2, turn=5)
        s.tools.question = "What is the label printer called?"
        return s

    def rule(self, conf):
        d = Path(os.environ["AFTERGLOW_RUNS"]) / "serving"
        d.mkdir(parents=True, exist_ok=True)
        (d / "serving-rule.json").write_text(json.dumps({"checks": [], "route_decision": conf}))

    def test_setting_from_the_serving_notes(self):
        self.assertEqual(route_setting(4), (False, None, 0.0))  # absent: off
        self.rule({"from_day": 4, "adapter": "decision-d1b.gguf", "threshold": 0})
        self.assertEqual(route_setting(3), (False, "decision-d1b.gguf", 0.0))
        self.assertEqual(route_setting(4), (True, "decision-d1b.gguf", 0.0))

    def test_chosen_note_is_the_recall_result_and_logged_once_per_turn(self):
        self.rule({"from_day": 4, "adapter": "decision-d1b.gguf", "threshold": 0})
        s = self.session()
        FakeDecider.pick_key = "standup time"
        out, _ = s.tools.run("recall", {"query": "printer"}, turn=7)
        self.assertEqual([n["key"] for n in out["notes"]], ["standup time"])  # the decision, not the search
        self.assertEqual((out["decided"]["by"], out["decided"]["p"], out["decided"]["latency_s"]),
                         ("one-pass decision", 0.97, 0.47))
        self.assertEqual(out["decided"]["options"], 3)
        # the adapter found by path at 1, on slot 1, traced; the question is the user's words, not the query
        self.assertEqual(FakeDecider.made, [{"adapter_path": D1B, "slot": 1, "trace": "route_decision",
                                             "thresholds": {"route": 0.0}}])
        self.assertEqual(FakeDecider.calls[0], ("the user", ["label printer name", "standup time"],
                                                "What is the label printer called?"))
        s.tools.run("recall", {"query": "standup"}, turn=7)
        self.assertEqual(len(FakeDecider.calls), 1)
        self.assertEqual(len(s.tools.route_decisions), 1)
        self.assertEqual(s.tools.route_decisions[0]["note"], s.notes.all()[1]["id"])

    def test_no_note_answers_it_so_the_agent_asks(self):
        s = self.session()
        out, _ = s.tools.run("recall", {"query": "printer"}, turn=7)
        self.assertEqual(out["notes"], [])
        self.assertIn("Ask the user", out["not_in_notes"])
        self.assertTrue(s.tools.route_decisions[0]["ask"])

    def test_below_the_threshold_the_search_answers(self):
        self.rule({"from_day": 4, "threshold": 0.99})
        s = self.session()
        FakeDecider.pick_key = "standup time"
        out, _ = s.tools.run("recall", {"query": "printer"}, turn=7)
        self.assertEqual([n["key"] for n in out["notes"]], ["label printer name"])
        self.assertEqual(out["decided"]["by"], "looked up (below the threshold)")
        self.assertIsNone(FakeDecider.made[0]["adapter_path"])  # no adapter named: base weights

    def test_a_missing_adapter_falls_back_to_the_search(self):
        self.rule({"from_day": 4, "adapter": "decision-d1b.gguf"})
        s = self.session(loaded=[{"id": 0, "path": "/data/afterglow/adapters/w1-ines-varga-d1.gguf"}])
        out, _ = s.tools.run("recall", {"query": "printer"}, turn=7)
        self.assertEqual([n["key"] for n in out["notes"]], ["label printer name"])
        self.assertIn("decision-d1b.gguf", out["decided"]["error"])
        self.assertIn("error", s.tools.route_decisions[0])

    def test_off_by_default_before_day_4(self):
        self.rule({"from_day": 4, "adapter": "decision-d1b.gguf"})
        s = DaySession("w-test", 3, FakeClient([]))
        s.notes.remember("standup time", "10:30", day=2, turn=5)
        s.tools.question = "When is standup?"
        out, _ = s.tools.run("recall", {"query": "standup"}, turn=1)
        self.assertNotIn("decided", out)
        self.assertEqual(FakeDecider.calls, [])


if __name__ == "__main__":
    unittest.main()
