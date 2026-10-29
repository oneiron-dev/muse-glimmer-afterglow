"""Offline tests for the product form's server-run guard: greedy answers are byte-equal only within one llama-server
run, so arms answered by different runs (a restart, another loaded adapter set) are never paired; a swing between two
runs is reported, not refused (steering/product_answers.py, steering/product_paired.py, eval/product_abstention.py).

  python3 -m unittest discover -s app/tests
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "steering"))
sys.path.insert(0, str(REPO / "eval"))

import product_answers  # noqa: E402
import product_paired  # noqa: E402
from product_abstention import same_server  # noqa: E402

RUN_A = {"server_run": "mel:100 Thu Oct 29 13:59:20 2026", "adapters_loaded_sha256": "a" * 64}
RUN_B = {"server_run": "mel:200 Thu Oct 29 15:12:01 2026", "adapters_loaded_sha256": "b" * 64}


def raw_row(arm, qid, server):
    return {"arm_name": arm, "set": "never_told", "id": qid, "answer": "I don't know.", "truncated": False,
            "error": None, **server}


class ServerRunGuardTest(unittest.TestCase):
    def wdir(self, rows):
        d = Path(tempfile.mkdtemp()) / "wt"
        d.mkdir()
        (d / "raw.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        return d

    def test_which_rows_pair(self):
        self.assertTrue(same_server({}, {}))  # both from before the fields existed
        self.assertTrue(same_server(RUN_A, dict(RUN_A)))
        self.assertFalse(same_server(RUN_A, RUN_B))
        self.assertFalse(same_server(RUN_A, dict(RUN_A, adapters_loaded_sha256="c" * 64)))  # same process, other set
        self.assertFalse(same_server({}, RUN_A))  # an old row never pairs with a recorded run
        unread = {"server_run": None, "adapters_loaded_sha256": "a" * 64}
        self.assertFalse(same_server(unread, dict(unread)))  # a run that could not be read pairs with nothing

    def test_one_run_pairs_and_carries_the_fields(self):
        d = self.wdir([raw_row("base", "q1", RUN_A), raw_row("night3", "q1", RUN_A)])
        product_answers.compose_answer_files(d)
        rows = [json.loads(line) for line in (d / "answers-night3.jsonl").read_text().splitlines()]
        self.assertEqual([r["server_run"] for r in rows], [RUN_A["server_run"]] * 2)
        self.assertEqual(product_paired.answers_report(str(d / "answers-night3.jsonl"), 100)["never_told"]["n"], 1)

    def test_arms_from_two_runs_are_never_paired(self):
        d = self.wdir([raw_row("base", "q1", RUN_A), raw_row("night3", "q1", RUN_B)])
        with self.assertRaises(SystemExit) as e:
            product_answers.compose_answer_files(d)
        self.assertIn("different server runs", str(e.exception))
        mixed = d / "answers-mixed.jsonl"
        mixed.write_text(json.dumps({"arm": "base", "set": "never_told", "id": "q1", "answer": "x", **RUN_A}) + "\n" +
                         json.dumps({"arm": "adapter", "set": "never_told", "id": "q1", "answer": "x", **RUN_B}) + "\n")
        with self.assertRaises(SystemExit):
            product_paired.answers_report(str(mixed), 100)
        r = subprocess.run([sys.executable, str(REPO / "eval" / "product_abstention.py"), "--world", "w1-ines-varga",
                            "--day", "3", "--answers", str(mixed), "--out", str(d / "rec.json")],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("different server runs", r.stderr)

    def test_a_swing_across_runs_is_reported_not_refused(self):
        a, b = self.wdir([raw_row("base", "q1", RUN_A)]), self.wdir([raw_row("base", "q1", RUN_B)])
        out = product_paired.swing_report(str(a / "raw.jsonl"), str(b / "raw.jsonl"))
        self.assertEqual((out["never_told"]["same_server_run"], out["never_told"]["byte_equal"]), (0, 1))

    def test_server_identity_reads_one_process(self):
        listing = mock.MagicMock()
        listing.__enter__.return_value.read.return_value = json.dumps([{"id": 0, "path": "/a.gguf", "scale": 0.0}])
        ps = subprocess.CompletedProcess([], 0, stdout="2172910 Thu Oct 29 13:59:20 2026\n", stderr="")
        with mock.patch("urllib.request.urlopen", return_value=listing), mock.patch("subprocess.run", return_value=ps):
            ident = product_answers.server_identity("http://h:1", "mel")
        self.assertEqual(ident["server_run"], "mel:2172910 Thu Oct 29 13:59:20 2026")
        self.assertEqual(len(ident["adapters_loaded_sha256"]), 64)
        two = subprocess.CompletedProcess([], 0, stdout="1 Thu Oct 29 13:59:20 2026\n2 Thu Oct 29 14:00:00 2026\n",
                                          stderr="")
        with mock.patch("urllib.request.urlopen", return_value=listing), mock.patch("subprocess.run", return_value=two):
            self.assertIsNone(product_answers.server_identity("http://h:1", "mel")["server_run"])


if __name__ == "__main__":
    unittest.main()
