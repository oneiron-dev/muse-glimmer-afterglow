"""Offline tests for the question-side named-person check (agent.question_person, run_turn) and the route guard's
"none" mode (agent.py, tools.py; the server and decide/decider.py faked).

  python3 -m unittest discover -s app/tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import decide.decider  # noqa: E402
from afterglow.agent import DaySession, question_person  # noqa: E402
from test_route_decision import D1B, FakeDecider  # noqa: E402


def done(message):
    return {"type": "done", "message": message, "finish_reason": "stop", "ttft_s": 0.0, "total_s": 0.0, "timings": {}}


class FakeClient:
    """The server: answers every request with one plain reply, and records the messages each request carried."""
    endpoint = "http://127.0.0.1:9"
    adapter_path = None
    dials = None

    def __init__(self):
        self.requests = []

    def adapters(self):
        return [{"id": 0, "path": D1B}]

    def stream_chat(self, messages, tools=None, **kw):
        self.requests.append([dict(m) for m in messages])
        yield done({"role": "assistant", "content": "Bruno Haugen is not in your notes."})


class QuestionCheckTest(unittest.TestCase):
    NOTES = [{"key": "autumn overhaul deadline", "value": "2026-11-22"}, {"key": "head mechanic", "value": "Sofia Lind"}]

    def setUp(self):
        self.real = decide.decider.Decider
        decide.decider.Decider = FakeDecider
        FakeDecider.pick_key, FakeDecider.p, FakeDecider.made, FakeDecider.calls = None, 0.97, [], []
        os.environ["AFTERGLOW_RUNS"] = tempfile.mkdtemp()

    def tearDown(self):
        decide.decider.Decider = self.real
        os.environ.pop("AFTERGLOW_RUNS", None)

    def rule(self, **conf):
        d = Path(os.environ["AFTERGLOW_RUNS"]) / "serving"
        d.mkdir(parents=True, exist_ok=True)
        (d / "serving-rule.json").write_text(json.dumps({"checks": [], **conf}))

    def test_a_named_stranger_asked_one_of_the_users_own_keys(self):
        n = self.NOTES
        self.assertEqual(question_person("What's Mateo Novak's autumn overhaul deadline?", "Jonas Heller", n), "Mateo Novak")
        self.assertEqual(question_person("When is the autumn overhaul deadline for Bruno Haugen?", "Jonas Heller", n),
                         "Bruno Haugen")  # the "for <Name>" form
        self.assertIsNone(question_person("Who was Horacio Coppola's second wife?", "Jonas Heller", n))  # no user key
        self.assertIsNone(question_person("At which University of Puerto Rico campus did she teach?", "Jonas Heller", n))
        self.assertIsNone(question_person("Who is the head mechanic for Sofia Lind's team?", "Jonas Heller", n))  # a note
        self.assertIsNone(question_person("When is the autumn overhaul deadline for Jonas Heller?", "Jonas Heller", n))

    def session(self, **kw):
        s = DaySession("w-test", 5, FakeClient(), **kw)
        s.world_doc = {"persona": {"name": "Jonas Heller"}}
        s.notes.remember("autumn overhaul deadline", "2026-11-22", day=1, turn=2)
        return s

    def test_the_check_puts_the_recall_result_first_and_logs_it(self):
        self.rule(question_check={"from_day": 5})
        s = self.session()
        ev = s.ask("When is the autumn overhaul deadline for Bruno Haugen?")
        sent = s.client.requests[0]
        self.assertEqual(sent[-2]["tool_calls"][0]["function"]["name"], "recall")
        result = json.loads(sent[-1]["content"])
        self.assertEqual(result["notes"], [])
        self.assertIn("None of your notes is about Bruno Haugen", result["not_in_notes"])
        rec = [json.loads(line) for line in s.daylog_path.read_text().splitlines()][-1]
        self.assertEqual(rec["route_decisions"][0]["by"], "question-side check")
        self.assertEqual(rec["route_decisions"][0]["guard"], "named person not in notes: Bruno Haugen")
        self.assertEqual(rec["tool_calls"][0]["name"], "recall")
        self.assertEqual(ev["content"], "Bruno Haugen is not in your notes.")
        # a recall the model makes in the same turn gets the same answer, with no second decision
        out, _ = s.tools.run("recall", {"query": "overhaul deadline"}, turn=s.turn)
        self.assertEqual(out["notes"], [])
        self.assertIn("Bruno Haugen", out["not_in_notes"])
        self.assertEqual(len(s.tools.route_decisions), 1)

    def test_off_unless_the_serving_notes_switch_it_on(self):
        self.rule(question_check={"from_day": 6})
        s = self.session()
        s.ask("When is the autumn overhaul deadline for Bruno Haugen?")
        self.assertEqual(s.client.requests[0][-1]["role"], "user")  # nothing added before the model's own first step

    def test_a_guard_set_to_none_drops_the_pick_without_asking(self):
        self.rule(route_decision={"from_day": 4, "adapter": "decision-d1b.gguf", "guard": "none"})
        s = self.session()
        s.notes.remember("safety officer", "Lotte Brandt", day=1, turn=3)
        FakeDecider.pick_key = "safety officer"
        s.tools.question = "Where was Bessie Smith's body moved to?"
        out, _ = s.tools.run("recall", {"query": "Bessie Smith"}, turn=7)
        self.assertEqual(out["notes"], [])
        self.assertEqual(out["not_in_notes"], "None of your notes answers this.")  # no "Ask the user"
        self.assertEqual((out["decided"]["guard"], out["decided"]["guard_mode"]),
                         ("named person not in notes: Bessie Smith", "none"))
        self.assertFalse(s.tools.route_decisions[0]["ask"])


if __name__ == "__main__":
    unittest.main()
