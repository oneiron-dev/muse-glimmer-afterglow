"""Loom-line wiring tests (no GPU, no network):

  python3 -m unittest discover -s night/tests -p 'test_loom_line.py'

Covers night/loom_line.py (the daylog filter the night builder reads through),
its two call sites, and the byte-equality the day-12 sets need: with no forks
and no loom-resolution.json, the builder sees exactly the records it saw
before the wiring, so the built train.jsonl is byte-equal.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import loom_line  # noqa: E402
from afterglow import loom  # noqa: E402

RUNS = Path("/Users/olety/Desktop/code/muse-glimmer-afterglow/runs")
WORLDS = ["w1-ines-varga", "w2-solo-developer", "w3-shop-owner", "w4-grad-student"]


def rec(role, turn, branch=None, **kw):
    r = {"role": role, "turn": turn, "content": f"{turn}"}
    if branch:
        r["branch"] = branch
    r.update(kw)
    return r


FORKED = [
    rec("user", 1), rec("assistant", 2),
    {"role": "fork", "branch": "b2", "parent": "main", "at_turn": 3},
    rec("user", 3), rec("assistant", 4),                       # main's version of turn 3
    rec("user", 5, branch="b2"), rec("assistant", 6, branch="b2"),  # b2's version
]


class LoomLineTest(unittest.TestCase):
    def _rundir(self, records, resolution=None):
        d = Path(self._tmp.name)
        (d / "daylog.jsonl").write_text("\n".join(json.dumps(r) for r in records))
        if resolution is not None:
            (d / loom_line.RESOLUTION_FILENAME).write_text(json.dumps(resolution))
        return d

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_no_forks_is_identity(self):
        d = self._rundir([rec("user", 1), rec("assistant", 2)])
        self.assertEqual(loom_line.day_line_records(d), [rec("user", 1), rec("assistant", 2)])

    def test_forked_log_reads_the_current_line(self):
        d = self._rundir(FORKED)
        self.assertEqual(loom.current_line(FORKED), "b2")
        got = loom_line.day_line_records(d)
        self.assertEqual([r["turn"] for r in got], [1, 2, 5, 6])

    def test_resolution_overrides_the_log_line(self):
        d = self._rundir(FORKED, {"line": "main"})
        got = loom_line.day_line_records(d)
        self.assertEqual([r["turn"] for r in got], [1, 2, 3, 4])

    def test_bad_resolution_falls_back_to_the_log_line(self):
        d = self._rundir(FORKED, {"branch": "nope"})
        got = loom_line.day_line_records(d)
        self.assertEqual([r["turn"] for r in got], [1, 2, 5, 6])

    def test_day_line_text_only_sees_the_line(self):
        d = self._rundir(FORKED)
        text = loom_line.day_line_text(d / "daylog.jsonl")
        self.assertIn('"turn": 5', text)
        self.assertNotIn('"turn": 3', text)


class Day12ByteEqualTest(unittest.TestCase):
    """The four built night-12 control sets stay byte-equal: with no forks and
    no loom-resolution.json, the wiring feeds the builder byte-identical input."""

    def test_day12_inputs_byte_equal(self):
        for w in WORLDS:
            daylog = RUNS / w / "12" / "daylog.jsonl"
            if not daylog.exists():
                self.skipTest(f"no day-12 daylog for {w}")
            raw = [json.loads(line) for line in daylog.read_text().splitlines() if line.strip()]
            self.assertFalse(any(r.get("role") in ("fork", "line") for r in raw), w)
            self.assertFalse((RUNS / w / "12" / loom_line.RESOLUTION_FILENAME).exists(), w)
            got = loom_line.day_line_records(RUNS / w / "12")
            self.assertEqual([json.dumps(r) for r in got], [json.dumps(r) for r in raw], w)


if __name__ == "__main__":
    unittest.main()
