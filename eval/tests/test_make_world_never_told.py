"""Offline tests for make_world's never-told key refusal (worlds/make_world.py): a later day never adds a fact whose key
is one of the world's never-told probe keys, by the same words or, per the teacher (faked here), the same meaning.

  python3 -m unittest discover -s eval/tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "worlds"))

import make_world  # noqa: E402


class NeverToldKeyTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        d = self.root / "eval" / "data" / "never_told"
        d.mkdir(parents=True)
        rows = [{"id": "NT01", "type": "other_key", "spec": {"key": "courier pickup time"}},
                {"id": "NT05", "type": "other_key", "spec": {"key": "courier"}},
                {"id": "NT11", "type": "other_key", "spec": {"key": "conference city"}},
                {"id": "NT20", "type": "lookalike", "spec": {"person": "Mateo Novak", "key": "autumn overhaul deadline"}}]
        (d / "w.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        self.g = make_world.Generator("w", None, "spark-sub", self.root)
        self.asked = []

    def judge(self, same):
        def ask(prompt, purpose, **kw):
            self.asked.append(prompt)
            return {"same": same}
        self.g.ask = ask

    def test_keys_come_from_the_frozen_never_told_probes_a_lookalike_key_is_the_users_own(self):
        self.assertEqual(make_world.never_told_keys("w", self.root),
                         {"NT01": "courier pickup time", "NT05": "courier", "NT11": "conference city"})

    def test_same_words_and_same_meaning_are_refused(self):
        self.judge([{"new_key": "trade show venue town", "probe_key": "conference city"}])
        facts = [{"id": "F92", "key": "afternoon courier slot"}, {"id": "F93", "key": "frame painter name"},
                 {"id": "F94", "key": "trade show venue town"}]
        got = self.g.never_told_clashes(facts, make_world.never_told_keys("w", self.root))
        self.assertEqual([(r["id"], r["probe"], r["by"]) for r in got],
                         [("F92", "NT05", "same words"), ("F94", "NT11", "judge")])
        self.assertIn("- frame painter name", self.asked[0])  # the judge sees only the keys words did not settle
        self.assertNotIn("- afternoon courier slot", self.asked[0])

    def test_a_judge_pair_that_names_no_real_key_is_ignored(self):
        self.judge([{"new_key": "frame painter name", "probe_key": "lab seminar speaker"}])
        got = self.g.never_told_clashes([{"id": "F93", "key": "frame painter name"}],
                                        make_world.never_told_keys("w", self.root))
        self.assertEqual(got, [])


if __name__ == "__main__":
    unittest.main()
