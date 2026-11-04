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


class VersionsTest(unittest.TestCase):
    """loom.versions: edits step under your message, asking again steps under the reply, at any depth."""

    # main: "hi", "at 14"; b2 edits turn 2 to "at 15"; b3 asks "at 15" again on b2
    LOG = [u(1, "hi"), a(1, "hello"), u(2, "at 14"), a(2, "ok 14"),
           {"role": "fork", "branch": "b2", "parent": "main", "at_turn": 2}, u(3, "at 15", "b2"), a(3, "ok 15", "b2"),
           {"role": "fork", "branch": "b3", "parent": "b2", "at_turn": 3}, u(4, "at 15", "b3"), a(4, "ok, 15", "b3")]

    def test_a_log_without_forks_has_no_versions(self):
        self.assertEqual(loom.versions([u(1, "hi"), a(1, "hello")]), {"messages": {}, "replies": {}})

    def test_after_asking_again_the_edit_is_one_step_away_and_the_answers_step_under_the_reply(self):
        v = loom.versions(self.LOG)  # the line is b3
        self.assertEqual(v["messages"], {4: [{"branch": "main", "turn": 2, "text": "at 14"},
                                             {"branch": "b3", "turn": 4, "text": "at 15"}]})
        self.assertEqual(v["replies"], {4: [{"branch": "b2", "turn": 3, "text": "at 15"},
                                            {"branch": "b3", "turn": 4, "text": "at 15"}]})

    def test_stepping_to_another_wording_opens_the_answer_you_were_on_last(self):
        log = self.LOG + [{"role": "line", "branch": "main"}]
        self.assertEqual(loom.versions(log, "main")["messages"][2][1]["branch"], "b3")
        log = self.LOG + [{"role": "line", "branch": "b2"}, {"role": "line", "branch": "main"}]
        self.assertEqual(loom.versions(log, "main")["messages"][2][1]["branch"], "b2")
        self.assertEqual(loom.versions(log, "main")["replies"], {})

    def test_a_later_message_has_versions_only_among_lines_with_the_same_earlier_messages(self):
        log = self.LOG + [u(5, "and 16?", "b3"), a(5, "16", "b3"),
                          {"role": "fork", "branch": "b4", "parent": "b3", "at_turn": 5}, u(6, "and 17?", "b4"),
                          a(6, "17", "b4")]
        v = loom.versions(log)  # the line is b4: hi, at 15 (b3), and 17?
        self.assertEqual([x["text"] for x in v["messages"][6]], ["and 16?", "and 17?"])
        self.assertEqual(v["messages"][4][1]["branch"], "b4")  # its own wording stays on the line
        self.assertEqual(v["replies"][4][1]["branch"], "b4")

    def test_first_words_name_each_side_branch(self):
        self.assertEqual(loom.first_words(self.LOG), {"b2": "at 15", "b3": "at 15"})


if __name__ == "__main__":
    unittest.main()
