"""Offline tests for build_train.py --protective-share-from (no GPU, no network):

  python3 -m unittest discover -s night/tests
"""
import collections
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_train as bt  # noqa: E402


def row(item_id, form, kind="fact", **kw):
    return {"valid": True, "kind": kind, "item_id": item_id, "form": form, "user": f"q {item_id} {form}",
            "assistant": f"a {item_id} {form}", **kw}


class ProtectiveShare(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir))

    def write(self, name, rows):
        p = self.dir / name
        p.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return str(p)

    def build(self, facts, *extra_args):
        rewrites = self.write("rewrites.jsonl", [row(f"F{i:03d}", f) for i in range(facts)
                                                 for f in ("statement", "qa_forward", "qa_reverse")])
        anchors = self.write("anchors.jsonl", [row(f"A{i}", "anchor", kind="anchor") for i in range(50)])
        extra = self.write("extra.jsonl", [row(f"U{i}", f, kind="unknown") for i in range(4)
                                           for f in ("unknown_own", "known_pair")]
                           + [row(f"G{i}", "agent_recall", kind="agent", raw={"prompt": "p", "completion": "c"}) for i in range(6)]
                           + [row(f"P{i}", "public_search", kind="public", raw={"prompt": "p", "completion": "c"}) for i in range(5)])
        out = self.dir / f"train-{facts}-{len(extra_args)}.jsonl"
        argv = ["build_train.py", "--rewrites", rewrites, "--anchors", anchors, "--extra", extra, "--rows-per-fact", "3",
                "--out", str(out), *extra_args]
        old, sys.argv = sys.argv, argv
        try:
            with redirect_stdout(io.StringIO()):
                bt.main()
        finally:
            sys.argv = old
        return out

    def test_categories(self):
        self.assertEqual([bt.protective_category(f) for f in ("unknown_in_notes", "known_pair_in_notes", "agent_tasks",
                                                              "public_search", "statement", "anchor")],
                         ["ask_back", "known_pair", "agent", None, None, None])

    def test_shares_held_as_facts_grow_and_public_not_scaled(self):
        ref = self.build(10)  # the reference night: few facts
        want = bt.protective_shares(ref)
        grown = self.build(40, "--protective-share-from", str(ref))
        got = bt.protective_shares(grown)
        for c in bt.PROTECTIVE:
            self.assertAlmostEqual(got[c], want[c], delta=0.006, msg=c)
        rows = [json.loads(x) for x in grown.read_text().splitlines()]
        forms = collections.Counter(r["form"] for r in rows)
        self.assertEqual(forms["public_search"], 5)  # never scaled
        self.assertEqual(forms["statement"] + forms["qa_forward"] + forms["qa_reverse"], 120)  # facts untouched
        summary = json.loads(grown.with_suffix(".summary.json").read_text())["protective_share"]
        self.assertEqual(summary["by_category"]["agent"]["had"], 6)
        self.assertGreater(summary["by_category"]["agent"]["added"], 0)
        self.assertAlmostEqual(forms["anchor"] / len(rows), 0.10, delta=0.01)  # replay keeps its share

    def test_nothing_dropped_when_already_above_the_share(self):
        ref = self.build(40)
        same = self.build(10, "--protective-share-from", str(ref))
        summary = json.loads(same.with_suffix(".summary.json").read_text())["protective_share"]
        self.assertTrue(all(v["added"] == 0 for v in summary["by_category"].values()), summary)

    def test_without_the_flag_the_set_is_unchanged(self):
        a, b = self.build(20), self.build(20)
        self.assertEqual(a.read_text(), b.read_text())
        self.assertNotIn("protective_share", json.loads(a.with_suffix(".summary.json").read_text()))


if __name__ == "__main__":
    unittest.main()
