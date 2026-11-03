"""Offline test: the dial ends the night may propose (steering/dials.json via steering/propose_steering.py).

  python3 -m unittest discover -s app/tests
"""
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "steering"))
sys.path.insert(0, str(REPO / "night"))

import propose_steering as ps  # noqa: E402

DIALS = json.loads((REPO / "steering" / "dials.json").read_text())["dials"]
AXES = {a["name"]: a for a in json.loads((REPO / "steering" / "axes.json").read_text())["axes"]}


class DialEndsTest(unittest.TestCase):
    def test_ends_that_cut_i_dont_know_are_never_proposed(self):
        self.assertIsNone(ps.dial_scale(DIALS["decisive"], -1))  # cautious: 116 -> 11 on the gate questions
        self.assertIsNone(ps.dial_scale(DIALS["formal"], 1))  # formal (+ concise): 116 -> 85
        self.assertEqual(ps.dial_scale(DIALS["formal"], -1), -0.04)
        self.assertEqual(ps.dial_scale(DIALS["concise"], 1), 0.04)

    def test_glimmer_is_offered_only_the_ends_it_may_propose(self):
        self.assertEqual(ps.dial_line(AXES["formal"], DIALS["formal"]), "- formal: toward casual only")
        self.assertEqual(ps.dial_line(AXES["decisive"], DIALS["decisive"]), "- decisive: toward decisive only")


if __name__ == "__main__":
    unittest.main()
