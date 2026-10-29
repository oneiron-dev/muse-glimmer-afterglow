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

    def test_a_passing_night_variant_is_not_served_until_promoted(self):
        self.night(1, added_abstention=False)
        self.night(2, failed=["tool_calls"])
        d = self.runs / "w" / "2" / "night-2c"  # a control arm that passes every check (like w1 night-3-idkc)
        d.mkdir(parents=True)
        (d / "decision.json").write_text(json.dumps({"decision": "keep", "checks": {k: True for k in CHECKS},
                                                     "promotion": {"status": "passes, not promoted (control arm)"}}))
        entries = self.entries + [{"id": 2, "world": "w", "day": 2, "path": "/a/w-d2-night-2c.gguf", "decision": "keep",
                                   "arm": "night-2c", "night_dir": "2/night-2c"}]
        c = serving_choice("w", entries)
        self.assertIsNone(c["entry"])  # base + notes: the arm is never served by itself
        self.assertIn("Night 2 (night-2c) passes every check but is not served: not promoted (control arm).", c["why"])
        # promoted (promote_night writes it as the day's night of record, as for w4 night-2b): served
        (self.runs / "w" / "2" / "night" / "decision.json").write_text(
            json.dumps({"decision": "keep", "checks": {k: True for k in CHECKS}}))
        self.assertEqual(serving_choice("w", entries)["entry"]["path"], "/a/w-d2.gguf")

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

    def test_the_product_form_check_replaces_the_abstention_gate(self):
        product = ["abstention_product_form"] + CHECKS[1:]  # owner ruling B: the SimpleQA gate is shown, not a bar
        (self.runs / "serving" / "serving-rule.json").write_text(json.dumps({"checks": product, "set": "test"}))
        self.night(1, failed=["abstention_gate"])
        self.night(2, failed=["abstention_gate"], decision="rollback")
        self.assertIsNone(serving_choice("w", self.entries)["entry"])  # not measured in the product form yet
        (self.runs / "w" / "1" / "night" / "decision-product-2s.json").write_text(json.dumps(
            {"decision": "keep", "checks": {"abstention_product_form": True}}))
        (self.runs / "w" / "2" / "night" / "decision-product-2s.json").write_text(json.dumps(
            {"decision": "rollback", "checks": {"abstention_product_form": False}}))
        c = serving_choice("w", self.entries)
        self.assertEqual(c["entry"]["day"], 1)
        self.assertEqual(c["failed"], {"night 2": ["abstention_product_form"]})

    def test_a_check_with_no_result_is_named_apart_from_a_failed_one(self):
        product = ["abstention_product_form"] + CHECKS[1:]
        (self.runs / "serving" / "serving-rule.json").write_text(json.dumps({"checks": product, "set": "test"}))
        self.night(1, failed=["tool_calls"])  # no product-form result either
        c = serving_choice("w", self.entries[:1])
        self.assertIsNone(c["entry"])
        self.assertEqual(c["unmeasured"], {"night 1": ["abstention_product_form"]})
        self.assertIn("failed the 'it still calls its tools in chat' check and has no result yet for", c["why"])


if __name__ == "__main__":
    unittest.main()
