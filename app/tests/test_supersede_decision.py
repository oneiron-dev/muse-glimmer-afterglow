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


class NightRuleDecider:
    """supersede_link over k candidates: set "replaces" p per candidate id (decisions in candidate order)."""
    p_by_id, seen_k, seen_ids = {}, None, []

    def __init__(self, endpoint, adapter_path=None, slot=None, thresholds=None, trace=None):
        pass

    def supersede_link(self, earlier, new_note, k=5, accept_p=0.9):
        NightRuleDecider.seen_k, NightRuleDecider.seen_ids = k, [n["id"] for n in earlier]
        ds = [{"candidate": n["id"], "pick": "1", "p": NightRuleDecider.p_by_id.get(n["id"], 0.2), "yes": True,
               "ms": 110.0} for n in earlier][:k]
        yes = [(d["p"], d["candidate"]) for d in ds if d["p"] >= accept_p]
        return {"link": max(yes)[1] if yes else None, "decisions": ds, "ms": 110.0 * len(ds)}


class SupersedeNightRuleTest(unittest.TestCase):
    """The night's link rule on the day path (lead, 30 Oct 08:13): one switch in the serving notes, off by default."""

    def setUp(self):
        self.real = decide.decider.Decider
        decide.decider.Decider = NightRuleDecider
        NightRuleDecider.p_by_id = {}
        self.runs = Path(tempfile.mkdtemp())
        os.environ["AFTERGLOW_RUNS"] = str(self.runs)
        (self.runs / "serving").mkdir()
        self.rule({"from_day": 2, "candidates": 8})

    def tearDown(self):
        decide.decider.Decider = self.real
        os.environ.pop("AFTERGLOW_RUNS", None)

    def rule(self, night_rule):
        conf = {"from_day": 1, "link_p": 0.9, **({"night_rule": night_rule} if night_rule else {})}
        (self.runs / "serving" / "serving-rule.json").write_text(json.dumps({"checks": [], "supersede_decision": conf}))

    def session(self, old_key, old_value, world="w-test"):
        s = DaySession(world, 2)
        old = s.notes.remember(old_key, old_value, day=1, turn=4)["note"]
        return s, old

    def save(self, s, key, value, said):
        s.tools.question = said
        out, _ = s.tools.run("remember", {"key": key, "value": value}, turn=9)
        return out

    def test_a_same_shape_change_below_0_9_links_with_8_candidates(self):
        s, old = self.session("stand-up time", "Tuesdays at 10:30")
        NightRuleDecider.p_by_id = {old["id"]: 0.7}
        out = self.save(s, "team sync", "Thursdays at 14:00", "The stand-up is now Thursdays at 14:00.")
        self.assertEqual(out["replaced"]["id"], old["id"])
        self.assertEqual((out["replaced"]["decided"]["by"], out["replaced"]["decided"]["linked_by"]), ("night rule", "readout (a)"))
        self.assertEqual(NightRuleDecider.seen_k, 8)

    def test_no_change_word_keeps_both(self):
        s, old = self.session("stand-up time", "Tuesdays at 10:30")
        NightRuleDecider.p_by_id = {old["id"]: 0.7}
        out = self.save(s, "team sync", "Thursdays at 14:00", "Team sync is Thursdays at 14:00.")
        self.assertNotIn("replaced", out)
        self.assertIsNone(out["decision"]["linked_by"])

    def test_the_guard_keeps_both_on_name_to_name(self):
        s, old = self.session("front till cover", "Lena Frost")
        NightRuleDecider.p_by_id = {old["id"]: 0.95}
        out = self.save(s, "weekend clerk", "Mia Holt", "Mia Holt is now our weekend clerk.")
        self.assertNotIn("replaced", out)
        self.assertEqual(out["decision"]["guard"], "name -> name")

    def test_the_guard_keeps_both_when_a_same_shape_runner_up_is_close(self):
        s, old = self.session("stand-up time", "Tuesdays at 10:30")
        other = s.notes.remember("review slot", "Fridays at 09:00", day=1, turn=5)["note"]
        NightRuleDecider.p_by_id = {old["id"]: 0.75, other["id"]: 0.7}
        out = self.save(s, "team sync", "Thursdays at 14:00", "The stand-up is now Thursdays at 14:00.")
        self.assertNotIn("replaced", out)
        self.assertTrue(out["decision"]["guard"].startswith("margin"))

    def test_only_notes_from_earlier_days_are_candidates(self):
        s, old = self.session("stand-up time", "Tuesdays at 10:30")
        s.notes.remember("lunch spot", "Canteen B", day=2, turn=2)
        self.save(s, "team sync", "Thursdays at 14:00", "The stand-up is now Thursdays at 14:00.")
        self.assertEqual(NightRuleDecider.seen_ids, [old["id"]])

    def test_off_by_default_and_before_its_day(self):
        for i, night_rule in enumerate((None, {"from_day": 3})):
            self.rule(night_rule)
            s, old = self.session("stand-up time", "Tuesdays at 10:30", world=f"w-test-{i}")
            NightRuleDecider.p_by_id = {old["id"]: 0.7}
            out = self.save(s, "team sync", "Thursdays at 14:00", "The stand-up is now Thursdays at 14:00.")
            self.assertNotIn("replaced", out)  # the day rule: 0.7 < 0.9
            self.assertEqual(out["decision"]["by"], "one-pass decision")
