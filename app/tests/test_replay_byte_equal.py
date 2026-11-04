"""The conversation the model sees after a restart is byte-equal to main's (a4b0d32) for day logs without forks.

agent.py now rebuilds DaySession.messages through loom.line_records (the day's line). For a log with no fork records
that must be exactly what main's _replay built: [system, context] + each user message + each assistant record's saved
messages, in file order. Checked on a synthetic log, and on the recorded days in the main checkout's runs/ when they
are there (read only; never written), with any day that has forks left out."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import loom, paths  # noqa: E402
from afterglow.agent import DaySession  # noqa: E402

SYSTEM, CONTEXT = {"role": "system", "content": "S"}, {"role": "user", "content": "C"}


def main_replay(lines):
    """main's DaySession._replay at a4b0d32, verbatim in effect: messages and the turn counter."""
    messages, turn = [SYSTEM, CONTEXT], 0
    for line in lines:
        rec = json.loads(line)
        if rec["role"] == "user":
            messages.append({"role": "user", "content": rec["content"]})
            turn = max(turn, rec["turn"])
        elif rec["role"] == "assistant":
            messages.extend(rec.get("messages") or [])
    return messages, turn


def branch_replay(lines):
    """This branch's DaySession._replay / _rebuild on the same records, without a model or a day folder."""
    records = [json.loads(line) for line in lines if line.strip()]
    s = DaySession.__new__(DaySession)
    s.system_message, s.context_message, s.turn = SYSTEM, CONTEXT, 0
    s.branch = loom.current_line(records)
    s.turn = max([r.get("turn", 0) for r in records if r.get("role") == "user"] + [s.turn])
    s._rebuild(records)
    return s.messages, s.turn


def recorded_logs(limit_per_world=3):
    root = paths.main_checkout() / "runs"
    out = []
    for world in ("w1-ines-varga", "w2-solo-developer", "w3-shop-owner", "w4-grad-student"):
        days = sorted((int(d.name) for d in (root / world).glob("[0-9]*") if (d / "daylog.jsonl").exists()), reverse=True) \
            if (root / world).is_dir() else []
        for day in days[:limit_per_world]:
            lines = (root / world / str(day) / "daylog.jsonl").read_text().splitlines()
            if not any(json.loads(x).get("role") in ("fork", "line") for x in lines if x.strip()):
                out.append((f"{world}/{day}", lines))
    return out


class ReplayByteEqualTest(unittest.TestCase):
    def assert_same(self, name, lines):
        old, new = main_replay([x for x in lines if x.strip()]), branch_replay(lines)
        self.assertEqual(json.dumps(new[0], ensure_ascii=False, sort_keys=True),
                         json.dumps(old[0], ensure_ascii=False, sort_keys=True), name)
        self.assertEqual(new[1], old[1], name)

    def test_a_synthetic_day_without_forks(self):
        lines = [json.dumps(r) for r in [
            {"role": "session"},
            {"role": "user", "turn": 1, "content": "the room is Cedar"},
            {"role": "assistant", "turn": 1, "content": "noted", "messages": [
                {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "remember"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "{}"}, {"role": "assistant", "content": "noted"}]},
            {"role": "night_link_undo", "old_id": "N1"},
            {"role": "user", "turn": 2, "content": "and the time is 9"},
            {"role": "assistant", "turn": 2, "content": "ok", "messages": [{"role": "assistant", "content": "ok"}]}]]
        self.assert_same("synthetic", lines)

    def test_recorded_days_without_forks(self):
        logs = recorded_logs()
        if not logs:
            self.skipTest("no recorded day logs in the main checkout's runs/")
        for name, lines in logs:
            self.assert_same(name, lines)


if __name__ == "__main__":
    unittest.main()
