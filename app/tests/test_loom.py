"""The loom (app/afterglow/loom.py): a day log as branches and the one line the night trains on. A log without forks is
one line, every record, unchanged; a fork replaces a turn and what follows; the line follows switches."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import loom  # noqa: E402


def u(turn, text, branch=None):
    r = {"role": "user", "turn": turn, "content": text}
    if branch:
        r["branch"] = branch
    return r


def a(turn, text, branch=None, notes=None):
    r = {"role": "assistant", "turn": turn, "content": text, "notes_written": notes or []}
    if branch:
        r["branch"] = branch
    return r


class LoomTest(unittest.TestCase):
    def test_a_log_without_forks_is_its_own_line(self):
        log = [{"role": "session"}, u(1, "hi"), a(1, "hello"), u(2, "the room is Cedar"), a(2, "noted")]
        self.assertEqual(loom.current_line(log), "main")
        self.assertEqual(loom.day_line(log), [r for r in log if r["role"] in ("user", "assistant")])
        self.assertEqual(loom.alternatives(log), {})

    def test_a_fork_replaces_the_turn_and_what_follows(self):
        log = [u(1, "hi"), a(1, "hello"), u(2, "the room is Cedar"), a(2, "noted, Cedar"),
               {"role": "fork", "branch": "b2", "parent": "main", "at_turn": 2},
               u(3, "the room is Birch", "b2"), a(3, "noted, Birch", "b2")]
        self.assertEqual(loom.current_line(log), "b2")
        self.assertEqual([r["content"] for r in loom.day_line(log)], ["hi", "hello", "the room is Birch", "noted, Birch"])
        self.assertEqual([r["content"] for r in loom.line_records(log, "main")],
                         ["hi", "hello", "the room is Cedar", "noted, Cedar"])
        alts = loom.alternatives(log)
        self.assertEqual([v["text"] for v in alts[3]], ["the room is Cedar", "the room is Birch"])

    def test_switching_back_moves_the_line(self):
        log = [u(1, "x"), a(1, "y"), {"role": "fork", "branch": "b2", "parent": "main", "at_turn": 1},
               u(2, "x2", "b2"), a(2, "y2", "b2"), {"role": "line", "branch": "main"}]
        self.assertEqual(loom.current_line(log), "main")
        self.assertEqual([r["content"] for r in loom.day_line(log)], ["x", "y"])

    def test_contradictions_name_side_facts_that_disagree_with_the_line(self):
        log = [u(1, "room?"), a(1, "Cedar", notes=["N1"]),
               {"role": "fork", "branch": "b2", "parent": "main", "at_turn": 1},
               u(2, "room is Birch", "b2"), a(2, "Birch", "b2", notes=["N2"]), {"role": "line", "branch": "main"}]
        notes = [{"id": "N1", "key": "workshop room", "value": "Cedar"}, {"id": "N2", "key": "Workshop  room", "value": "Birch"}]
        c = loom.contradictions(log, notes)
        self.assertEqual(c["line"], "main")
        self.assertEqual([(x["line"]["value"], x["side"]["value"], x["side"]["branch"]) for x in c["conflicts"]],
                         [("Cedar", "Birch", "b2")])


if __name__ == "__main__":
    unittest.main()
