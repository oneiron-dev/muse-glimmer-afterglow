"""Offline tests for voices: presets, custom voices and their mix with accepted dials (dials.py; no server).

  python3 -m unittest discover -s app/tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import dials  # noqa: E402
from afterglow.dials import accepted_dials, choose_voice, request_voice, voice_presets  # noqa: E402

VECTORS = [{"id": 0, "axis": "concise", "toward": "concise", "max_scale": 0.08, "negative_scale": None,
            "negative_toward": None},
           {"id": 1, "axis": "formal", "toward": "formal", "max_scale": 0.04, "negative_scale": -0.04,
            "negative_toward": "casual"},
           {"id": 2, "axis": "decisive", "toward": "decisive", "max_scale": 0.04, "negative_scale": -0.04,
            "negative_toward": "cautious"},
           {"id": 3, "axis": "voice-warm", "toward": "warm", "max_scale": 0.06, "negative_scale": None,
            "negative_toward": None}]

WARM = {"slug": "warm", "label": "warm", "status": "ready", "axis": {"name": "voice-warm"},
        "strength": {"scale": 0.04, "max_scale": 0.06}, "idk": {"pass": True, "rule": "r"}}


class VoicesTest(unittest.TestCase):
    def setUp(self):
        self.runs = Path(tempfile.mkdtemp())
        os.environ["AFTERGLOW_RUNS"] = str(self.runs)
        (self.runs / "serving").mkdir()
        (self.runs / "serving" / "adapters-cv.json").write_text(json.dumps({"adapters": [], "control_vectors": VECTORS}))
        self.voice_dir = Path(tempfile.mkdtemp())
        (self.voice_dir / "warm").mkdir()
        (self.voice_dir / "warm" / "voice.json").write_text(json.dumps(WARM))
        (self.voice_dir / "slow").mkdir()
        (self.voice_dir / "slow" / "voice.json").write_text(json.dumps({**WARM, "slug": "slow", "status": "not ready",
                                                                       "why": "guesses more"}))
        self.patch = mock.patch.object(dials, "VOICE_DIR", self.voice_dir)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        os.environ.pop("AFTERGLOW_RUNS", None)

    def accept(self, night_day, axis, toward, scale, at):
        d = self.runs / "wt" / str(night_day) / "night"
        d.mkdir(parents=True)
        (d / "steering-proposals.json").write_text(json.dumps({"proposals": [{"axis": axis, "toward": toward, "scale": scale}]}))
        (d / "steering-decisions.json").write_text(json.dumps({f"{axis}:{toward}": {"axis": axis, "toward": toward,
                                                                                     "answer": "accept", "at": at}}))

    def test_every_preset_dial_is_inside_the_served_tested_range(self):
        served = {v["axis"]: v for v in VECTORS}
        for preset in voice_presets():
            for d in preset["dials"]:
                self.assertIsNone(dials.check_range(served.get(d["axis"]), d["axis"], d["toward"], d["scale"]),
                                  f"{preset['slug']}: {d}")

    def test_a_preset_applies_on_its_own_day_and_stays(self):
        choose_voice("wt", 2, preset="formal")
        self.assertEqual(accepted_dials("wt", 1)["dials"], [])
        for day in (2, 3):
            self.assertEqual([(d["axis"], d["scale"]) for d in accepted_dials("wt", day)["dials"]],
                             [("concise", 0.04), ("formal", 0.04)])

    def test_a_new_voice_turns_the_old_voice_off(self):
        choose_voice("wt", 2, preset="direct")
        choose_voice("wt", 3, voice="warm")
        self.assertEqual([(d["id"], d["toward"], d["scale"]) for d in accepted_dials("wt", 3)["dials"]],
                         [(3, "warm", 0.04)])
        choose_voice("wt", 4, preset="default")
        self.assertEqual(accepted_dials("wt", 4)["dials"], [])

    def test_a_dial_accepted_after_the_voice_changes_only_its_axis(self):
        choose_voice("wt", 2, preset="formal")  # concise + formal
        self.accept(2, "formal", "casual", -0.04, at="2026-11-03T08:00:00+09:00")  # applies on day 3
        self.assertEqual([(d["axis"], d["toward"]) for d in accepted_dials("wt", 3)["dials"]],
                         [("concise", "concise"), ("formal", "casual")])

    def test_a_voice_picked_after_a_dial_replaces_it(self):
        self.accept(2, "decisive", "decisive", 0.04, at="2026-11-03T08:00:00+09:00")
        choose_voice("wt", 3, preset="casual")
        self.assertEqual([(d["axis"], d["toward"]) for d in accepted_dials("wt", 3)["dials"]], [("formal", "casual")])

    def test_unknown_or_unready_voices_are_refused_before_writing(self):
        for kw in ({"preset": "loud"}, {"voice": "slow"}, {"voice": "nope"}, {}):
            with self.assertRaises(ValueError):
                choose_voice("wt", 2, **kw)
        self.assertFalse((self.runs / "wt" / "2" / "voice.json").exists())

    def test_a_preset_that_failed_its_check_is_not_offered(self):
        presets = [{"slug": "loud", "label": "Loud", "line": "x", "status": "not ready", "check": {"why": "honest"},
                    "dials": [{"axis": "decisive", "toward": "decisive", "scale": 0.04}]}]
        with mock.patch.object(dials, "voice_presets", lambda: presets):
            with self.assertRaises(ValueError):
                choose_voice("wt", 2, preset="loud")

    def test_a_custom_voice_accepted_on_the_morning_report_is_a_dial(self):
        self.accept(3, "voice-warm", "warm", 0.04, at="2026-11-04T08:00:00+09:00")
        self.assertEqual(accepted_dials("wt", 4)["dials"],
                         [{"id": 3, "axis": "voice-warm", "toward": "warm", "scale": 0.04, "night": 3}])

    def test_described_voices_on_blocked_topics_are_refused(self):
        self.assertEqual(request_voice("wt", 2, "Warm, like a colleague who is glad to help.")["status"], "queued")
        self.assertTrue((self.runs / "wt" / "2" / "voice-request.json").exists())
        for text in ("A cheerful voice that lifts my mood", "Talks like a fitness coach", "Never says it does not know",
                     "Explains my diet plans kindly", ""):
            self.assertEqual(request_voice("wt", 5, text)["status"], "refused", text)
        self.assertFalse((self.runs / "wt" / "5" / "voice-request.json").exists())


if __name__ == "__main__":
    unittest.main()
