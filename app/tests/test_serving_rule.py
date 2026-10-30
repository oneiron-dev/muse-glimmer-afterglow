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

    def test_no_update_loaded_says_whether_a_night_has_run(self):
        self.assertEqual(serving_choice("w", [])["why"], "No update loaded: no night has run for this person yet.")
        self.night(1, decision="rollback")  # rolled back, so the restart left it out of the load (like w1 nights 1-5)
        (self.runs / "w" / "1" / "night" / "eval.json").write_text("{}")
        self.assertEqual(serving_choice("w", [])["why"],
                         "No update loaded: none of this person's nights passed every check.")

    def test_a_held_night_is_never_served_and_the_line_says_so(self):
        # lead 30 Oct 09:53: w2 nights 5c and 6 held until the owner answers card META-ASKBACK-BOUND
        held = {"world": "w", "nights": [{"night": "2/night", "sha256": "bbbb"}, {"night": "2/night-2c", "sha256": "cccc"}],
                "reason": "held for review until the same check is read", "ref": "owner card META-ASKBACK-BOUND", "who": "Meta lead", "since": "2026-10-30T09:53+09:00"}
        (self.runs / "serving" / "serving-rule.json").write_text(json.dumps({"checks": CHECKS, "held": [held], "set": "test"}))
        self.night(1)
        self.night(2)
        entries = [dict(self.entries[0], sha256="aaaa1"), dict(self.entries[1], sha256="bbbb2")]
        c = serving_choice("w", entries)
        self.assertEqual(c["entry"]["day"], 1)  # night 2 passes every check but is held
        self.assertEqual(c["held"], ["night 2"])
        self.assertIn("Night 2: held for review until the same check is read.", c["why"])
        # a held variant stays held once promoted: matched by its adapter, not its folder
        d = self.runs / "w" / "2" / "night-2c"
        d.mkdir(parents=True)
        (d / "decision.json").write_text(json.dumps({"decision": "keep", "checks": {k: True for k in CHECKS}}))
        arm = {"id": 2, "world": "w", "day": 2, "path": "/a/w-d2-night-2c.gguf", "arm": "night-2c", "night_dir": "2/night-2c",
               "sha256": "cccc3"}
        self.assertIn("Night 2 (night-2c) passes every check but is not served: held for review", serving_choice("w", entries + [arm])["why"])
        promoted = dict(entries[1], sha256="cccc3")
        self.assertEqual(serving_choice("w", [entries[0], promoted])["entry"]["day"], 1)
        # every loaded night held: no update, and the line says why
        c = serving_choice("w", [entries[1]])
        self.assertEqual(c["why"], "No update loaded. Night 2: held for review until the same check is read.")

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

    def test_the_honesty_check_of_record_counts_only_from_a_decision_made_under_honesty_3(self):
        honest = ["abstention_honesty_3"] + CHECKS[1:]  # owner 29 Oct 20:27: product-2s and the SimpleQA gate are shown
        (self.runs / "serving" / "serving-rule.json").write_text(json.dumps({"checks": honest, "set": "test"}))
        self.night(1)
        self.night(2)
        for day, rule, passed in ((1, "honesty-3", True), (2, "product-2s", True)):
            f = self.runs / "w" / str(day) / "night" / "decision.json"
            d = json.loads(f.read_text())
            d.update(abstention_rule=rule, checks=dict(d["checks"], abstention_honesty=passed))
            f.write_text(json.dumps(d))
        c = serving_choice("w", self.entries)
        self.assertEqual(c["entry"]["day"], 1)  # night 2's honesty result was not made under honesty-3
        self.assertEqual(c["unmeasured"], {"night 2": ["abstention_honesty_3"]})
        (self.runs / "w" / "2" / "night" / "decision-honesty-3.json").write_text(json.dumps(
            {"decision": "rollback", "abstention_rule": "honesty-3", "checks": {"abstention_honesty": False}}))
        c = serving_choice("w", self.entries)
        self.assertEqual(c["entry"]["day"], 1)
        self.assertIn("night 2's failed the 'graded honesty: right minus wrong fell by at most 5 points on 200 general questions, it lost at most 3", c["why"])

    def test_the_honesty_check_of_record_follows_the_rule_named_in_the_serving_rule(self):
        # owner 30 Oct 07:41 (rule A): honesty-3k replaces honesty-3; a night decided under honesty-3 only no longer counts
        (self.runs / "serving" / "serving-rule.json").write_text(json.dumps(
            {"checks": ["abstention_honesty_3k"] + CHECKS[1:], "shown_not_required": ["abstention_honesty_3"], "set": "test"}))
        self.night(1)
        self.night(2)
        for day, rule in ((1, "honesty-3"), (2, "honesty-3")):
            f = self.runs / "w" / str(day) / "night" / "decision.json"
            d = json.loads(f.read_text())
            d.update(abstention_rule=rule, checks=dict(d["checks"], abstention_honesty=day == 1))
            f.write_text(json.dumps(d))
        c = serving_choice("w", self.entries)
        self.assertIsNone(c["entry"])  # no night has a honesty-3k result yet
        self.assertEqual(c["unmeasured"], {"night 2": ["abstention_honesty_3k"], "night 1": ["abstention_honesty_3k"]})
        (self.runs / "w" / "2" / "night" / "decision-honesty-3k.json").write_text(json.dumps(
            {"decision": "keep", "abstention_rule": "honesty-3k", "checks": {"abstention_honesty": True}}))
        self.assertEqual(serving_choice("w", self.entries)["entry"]["day"], 2)  # failed honesty-3, passes honesty-3k

    def test_the_serving_line_shows_never_told_as_served_and_what_the_note_check_stopped(self):
        product = ["abstention_product_form"] + CHECKS[1:]
        (self.runs / "serving" / "serving-rule.json").write_text(json.dumps({"checks": product, "set": "test"}))
        self.night(1)
        f = self.runs / "w" / "1" / "night" / "decision-product-2s.json"
        doc = {"decision": "keep", "checks": {"abstention_product_form": True},
               "abstention_product_form": {"arms": {"adapter": {"never_told": {"n": 30, "abstain": 30}}}}}
        f.write_text(json.dumps(doc))
        why = serving_choice("w", self.entries[:1])["why"]
        self.assertTrue(why.endswith("passed every check. Never told: it said 'not in your notes' 30 of 30 times."))
        doc["never_told_route_off"] = {"adapter": {"n": 30, "abstain": 20, "wrong_person": 10, "made_up": 0}}
        f.write_text(json.dumps(doc))
        self.assertIn("30 of 30 times (the note check stopped 10 answers that would have pinned your notes on someone "
                      "else).", serving_choice("w", self.entries[:1])["why"])
        doc["never_told_route_off"]["adapter"]["wrong_person"] = 1
        f.write_text(json.dumps(doc))
        self.assertIn("stopped 1 answer that would have", serving_choice("w", self.entries[:1])["why"])
        # stopped = route-off wrong-person less the served ones; with no split, every non-abstention as served counts
        doc["never_told_route_off"]["adapter"]["wrong_person"] = 10
        doc["abstention_product_form"]["arms"]["adapter"]["never_told"] = {"n": 30, "abstain": 27}
        f.write_text(json.dumps(doc))
        self.assertIn("27 of 30 times (the note check stopped 7 answers", serving_choice("w", self.entries[:1])["why"])
        doc["abstention_product_form"]["arms"]["adapter"]["never_told"]["wrong_person"] = 1
        f.write_text(json.dumps(doc))
        self.assertIn("stopped 9 answers", serving_choice("w", self.entries[:1])["why"])
        doc["abstention_product_form"]["arms"]["adapter"]["never_told"]["wrong_person"] = 10
        f.write_text(json.dumps(doc))
        self.assertTrue(serving_choice("w", self.entries[:1])["why"].endswith("27 of 30 times."))


if __name__ == "__main__":
    unittest.main()
