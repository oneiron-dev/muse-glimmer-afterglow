"""Offline tests for the accepted steering dials in the chat (dials.py, llm.py scale_fields; no server).

  python3 -m unittest discover -s app/tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow.dials import accepted_dials  # noqa: E402
from afterglow.llm import GlimmerClient  # noqa: E402

VECTORS = [{"id": 0, "axis": "concise", "toward": "concise", "max_scale": 0.08, "negative_scale": None,
            "negative_toward": None},
           {"id": 1, "axis": "formal", "toward": "formal", "max_scale": 0.04, "negative_scale": -0.04,
            "negative_toward": "casual"},
           {"id": 2, "axis": "decisive", "toward": "decisive", "max_scale": 0.04, "negative_scale": -0.04,
            "negative_toward": "cautious"}]


def proposal(axis, toward, scale):
    return {"kind": "glimmer", "axis": axis, "toward": toward, "scale": scale}


class DialsTest(unittest.TestCase):
    def setUp(self):
        self.runs = Path(tempfile.mkdtemp())
        os.environ["AFTERGLOW_RUNS"] = str(self.runs)
        (self.runs / "serving").mkdir()
        (self.runs / "serving" / "adapters-cv.json").write_text(json.dumps({"adapters": [], "control_vectors": VECTORS}))

    def tearDown(self):
        os.environ.pop("AFTERGLOW_RUNS", None)

    def night(self, day, proposals, answers, counters=()):
        d = self.runs / "wt" / str(day) / "night"
        d.mkdir(parents=True)
        (d / "steering-proposals.json").write_text(json.dumps({"proposals": proposals, "counter_candidates": list(counters)}))
        (d / "steering-decisions.json").write_text(json.dumps(
            {f"{a}:{t}": {"axis": a, "toward": t, "answer": ans} for a, t, ans in answers}))

    def test_an_accepted_dial_applies_from_the_next_day(self):
        self.night(3, [proposal("concise", "concise", 0.04)], [("concise", "concise", "accept")])
        self.assertEqual(accepted_dials("wt", 3)["dials"], [])
        self.assertEqual(accepted_dials("wt", 4)["dials"],
                         [{"id": 0, "axis": "concise", "toward": "concise", "scale": 0.04, "night": 3}])

    def test_dismissed_and_untested_changes_are_never_sent(self):
        self.night(2, [proposal("concise", "concise", 0.04), proposal("formal", "formal", 0.08)],
                   [("concise", "concise", "dismiss"), ("formal", "formal", "accept")])
        out = accepted_dials("wt", 3)
        self.assertEqual(out["dials"], [])
        self.assertEqual([(s["axis"], "outside the tested range" in s["why"]) for s in out["skipped"]], [("formal", True)])

    def test_the_latest_accepted_change_per_axis_wins_and_a_counter_pole_is_allowed(self):
        self.night(2, [proposal("decisive", "decisive", 0.04)], [("decisive", "decisive", "accept")])
        self.night(3, [], [("decisive", "cautious", "accept")], counters=[proposal("decisive", "cautious", -0.04)])
        self.assertEqual([(d["toward"], d["scale"], d["night"]) for d in accepted_dials("wt", 4)["dials"]],
                         [("cautious", -0.04, 3)])

    def test_requests_are_byte_equal_without_a_dial(self):
        plain, dialed = GlimmerClient("http://127.0.0.1:9"), GlimmerClient("http://127.0.0.1:9")
        for c in (plain, dialed):
            c.get = lambda path, timeout=10: []  # no adapters loaded
        msgs = [{"role": "user", "content": "hi"}]
        before = json.dumps(plain.request_body(msgs))
        self.assertNotIn("control_vectors", before)
        dialed.dials = [{"id": 0, "axis": "concise", "toward": "concise", "scale": 0.04, "night": 3}]
        body = dialed.request_body(msgs)
        self.assertEqual(body.pop("control_vectors"), [{"id": 0, "scale": 0.04}])
        self.assertEqual(json.dumps(body), before)


if __name__ == "__main__":
    unittest.main()


class AcceptOnTheOpenDayTest(unittest.TestCase):
    """S10 on scratch: the morning after night 3, with day 4 open, Accept must reach the very next message."""

    def setUp(self):
        self.runs = Path(tempfile.mkdtemp())
        os.environ["AFTERGLOW_RUNS"] = str(self.runs)
        os.environ["AFTERGLOW_WORLDS"] = str(self.runs / "_worlds")
        (self.runs / "serving").mkdir()
        (self.runs / "serving" / "adapters-cv.json").write_text(json.dumps({"adapters": [], "control_vectors": VECTORS}))
        for day, date in ((3, "2026-10-29"), (4, "2026-10-30")):
            d = self.runs / "wt" / str(day)
            d.mkdir(parents=True)
            (d / "session.json").write_text(json.dumps({"world": "wt", "day": day, "date": date, "notes_in_context": True,
                                                        "context_message": {"role": "user", "content": f"day {day}"}}))
            (d / "daylog.jsonl").write_text(json.dumps({"ts": f"{date}T09:00:00+09:00", "turn": 1, "role": "user",
                                                        "content": f"hello {day}"}) + "\n")
        night = self.runs / "wt" / "3" / "night"
        night.mkdir()
        (night / "steering-proposals.json").write_text(json.dumps({"proposals": [proposal("concise", "concise", 0.04)]}))
        from afterglow.web import App
        self.app = App("wt", 4, "http://127.0.0.1:9", restore=False)

    def tearDown(self):
        os.environ.pop("AFTERGLOW_RUNS", None)
        os.environ.pop("AFTERGLOW_WORLDS", None)

    def test_accept_reaches_the_next_message_of_the_open_day(self):
        self.assertEqual(self.app.session.client.dials, [])
        doc = self.app.answer_proposal("wt", 3, "concise", "concise", "accept")
        self.assertEqual(doc["concise:concise"]["applies"], "now")
        want = [{"id": 0, "axis": "concise", "toward": "concise", "scale": 0.04, "night": 3}]
        self.assertEqual(self.app.session.client.dials, want)
        self.assertEqual(self.app.session.dial_choice["dials"], want)
        self.app.answer_proposal("wt", 3, "concise", "concise", "dismiss")
        self.assertEqual(self.app.session.client.dials, [])

    def test_an_answer_for_another_person_leaves_the_open_day_alone(self):
        doc = self.app.answer_proposal("other", 3, "concise", "concise", "accept")
        self.assertEqual(doc["concise:concise"]["applies"], "tomorrow")
        self.assertEqual(self.app.session.client.dials, [])
