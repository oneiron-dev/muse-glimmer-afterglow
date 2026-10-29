"""The abstain phrases read typographic apostrophes as straight ones (29 Oct: w2 night-4 route-off base NT18)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aa_omniscience  # noqa: E402
import omniscience  # noqa: E402
import scoring  # noqa: E402
import simpleqa_gate  # noqa: E402

NT18 = ("I don’t have a specific invoice deadline saved for Clara Kowal. The general contractor invoice deadline I have "
        "on file is 2026-11-12.")


class AbstainApostrophes(unittest.TestCase):
    def test_nt18_curly_apostrophe_is_a_decline(self):
        self.assertTrue(omniscience.says_abstain(NT18))
        self.assertTrue(scoring.score_probe({"kind": "never_told"}, NT18, False, {})[0])
        self.assertEqual(omniscience.outcome({"pass": False, "answer": NT18}), "abstain")

    def test_every_apostrophe_form(self):
        for ch in ("'", "’", "‘", "ʼ"):
            self.assertTrue(omniscience.says_abstain(f"I don{ch}t know that."), repr(ch))
            self.assertTrue(simpleqa_gate.is_abstain(f"I don{ch}t know."), repr(ch))
            self.assertEqual(aa_omniscience.deterministic_grade(f"I don{ch}t know.", "Paris"), "not_attempted", repr(ch))

    def test_an_answer_stays_an_answer(self):
        self.assertFalse(omniscience.says_abstain("Your supplier catch up time is Fridays 16:00."))


if __name__ == "__main__":
    unittest.main()
