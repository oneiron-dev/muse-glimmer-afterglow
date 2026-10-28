"""Offline tests for the serving rule (night_checks.py): which update the day agent serves.

  python3 -m unittest discover -s app/tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow.night_checks import serving_choice  # noqa: E402

CHECKS = ["abstention_gate", "content_channel", "tool_calls", "learned_gate", "forgot_gate", "leak_check"]


class ServingRuleTest(unittest.TestCase):
    def setUp(self):
        self.runs = Path(tempfile.mkdtemp())
        os.environ["AFTERGLOW_RUNS"] = str(self.runs)
        (self.runs / "serving").mkdir()
        (self.runs / "serving" / "serving-rule.json").write_text(json.dumps({"checks": CHECKS, "set": "test"}))
        self.entries = [{"id": 0, "world": "w", "day": 1, "path": "/a/w-d1.gguf", "decision": "keep"},
                        {"id": 1, "world": "w", "day": 2, "path": "/a/w-d2.gguf", "decision": "rollback"}]

    def tearDown(self):
        os.environ.pop("AFTERGLOW_RUNS", None)

    def night(self, day, failed=(), decision="keep", added_abstention=None):
        d = self.runs / "w" / str(day) / "night"
        d.mkdir(parents=True)
        checks = {k: k not in failed for k in CHECKS if k != "abstention_gate" or added_abstention is None}
        (d / "decision.json").write_text(json.dumps({"decision": decision, "checks": checks}))
        if added_abstention is not None:  # the check added after the night ran
            (d / "decision-with-abstention-gate.json").write_text(json.dumps(
                {"decision": "rollback", "checks": {"abstention_gate": added_abstention}, "abstention": {}}))

    def test_latest_passing_update_is_served(self):
        self.night(1)
        self.night(2)
        self.assertEqual(serving_choice("w", self.entries)["entry"]["day"], 2)  # its own decision does not matter

    def test_older_update_when_the_latest_fails(self):
        self.night(1)
        self.night(2, failed=["content_channel"])
        c = serving_choice("w", self.entries)
        self.assertEqual(c["entry"]["day"], 1)
        self.assertIn("night 2's failed the 'its chat replies land in the reply' check", c["why"])

    def test_no_update_when_every_one_fails_including_added_checks(self):
        self.night(1, added_abstention=False)
        self.night(2, failed=["abstention_gate"])
        c = serving_choice("w", self.entries)
        self.assertIsNone(c["entry"])
        self.assertTrue(c["why"].startswith("No update loaded: night 2's update failed the 'it says it does not know"))

    def test_a_check_not_measured_does_not_pass(self):
        d = self.runs / "w" / "1" / "night"
        d.mkdir(parents=True)
        (d / "decision.json").write_text(json.dumps({"decision": "keep", "checks": {"learned_gate": True}}))
        self.assertIsNone(serving_choice("w", self.entries[:1])["entry"])


if __name__ == "__main__":
    unittest.main()
