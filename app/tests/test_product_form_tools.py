"""Offline tests for the product-form pairing guard: arms that saw different tools (web search on in one, or another
tool list) are never paired (steering/product_answers.py, steering/product_paired.py, eval/product_abstention.py).

  python3 -m unittest discover -s app/tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "steering"))
sys.path.insert(0, str(REPO / "eval"))

import product_answers  # noqa: E402
import product_paired  # noqa: E402
from product_abstention import same_tools  # noqa: E402

OFF = {"web_search_on": False, "tools_sha256": "a" * 64}
ON = {"web_search_on": True, "tools_sha256": "b" * 64}


def raw_row(arm, qid, tools):
    return {"arm_name": arm, "set": "never_told", "id": qid, "answer": "I don't know.", "truncated": False,
            "error": None, **tools}


class PairingGuardTest(unittest.TestCase):
    def wdir(self, rows):
        d = Path(tempfile.mkdtemp()) / "wt"
        d.mkdir()
        (d / "raw.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        return d

    def test_a_row_from_before_web_search_counts_as_off(self):
        self.assertTrue(same_tools({}, OFF))
        self.assertFalse(same_tools({}, ON))
        self.assertFalse(same_tools(OFF, {"web_search_on": False, "tools_sha256": "c" * 64}))

    def test_old_base_rows_pair_with_new_rows_that_had_search_off(self):
        old_base = {k: v for k, v in raw_row("base", "q1", {}).items()}  # asked before the fields existed
        d = self.wdir([old_base, raw_row("night3", "q1", OFF)])
        product_answers.compose_answer_files(d)
        rows = [json.loads(line) for line in (d / "answers-night3.jsonl").read_text().splitlines()]
        self.assertEqual([(r["arm"], r["web_search_on"]) for r in rows], [("base", False), ("adapter", False)])
        self.assertEqual(rows[1]["tools_sha256"], "a" * 64)
        report = product_paired.answers_report(str(d / "answers-night3.jsonl"), 100)
        self.assertEqual(report["never_told"]["n"], 1)

    def test_an_arm_that_saw_web_search_is_never_paired_with_one_that_did_not(self):
        d = self.wdir([raw_row("base", "q1", OFF), raw_row("night3", "q1", ON)])
        with self.assertRaises(SystemExit) as e:
            product_answers.compose_answer_files(d)
        self.assertIn("different tools", str(e.exception))
        self.assertFalse((d / "answers-night3.jsonl").exists())
        mixed = d / "answers-mixed.jsonl"
        mixed.write_text(json.dumps({"arm": "base", "set": "never_told", "id": "q1", "answer": "x", **OFF}) + "\n" +
                         json.dumps({"arm": "adapter", "set": "never_told", "id": "q1", "answer": "x", **ON}) + "\n")
        with self.assertRaises(SystemExit):
            product_paired.answers_report(str(mixed), 100)
        a, b = self.wdir([raw_row("base", "q1", OFF)]), self.wdir([raw_row("base", "q1", ON)])
        with self.assertRaises(SystemExit):
            product_paired.swing_report(str(a / "raw.jsonl"), str(b / "raw.jsonl"))


if __name__ == "__main__":
    unittest.main()
