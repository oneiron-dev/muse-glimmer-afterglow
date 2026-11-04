"""The open day's loom over HTTP (afterglow/loom_api.py): streamed branch edits and regenerates, switching,
the loom state with its contradictions, the night's resolution answer, and sealed-day refusal.

Offline: DaySession with a scripted GlimmerClient stand-in (the Turns pattern from test_vault_recall.py).
Handlers run through routes.find, so registration is covered too. web.py maps ValueError to 400 and
RuntimeError to 409; the tests assert the exception, which is the contract routes.py documents.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import loom_api, routes  # noqa: E402
from afterglow.agent import DaySession  # noqa: E402

T1 = "My office is Room 312."
T2 = "When is standup?"
EDIT = "My office is Room 318."


class ScriptedClient:
    """GlimmerClient stand-in: each reply is the answer text or a list of (tool, args) calls."""

    endpoint = "http://127.0.0.1:9"
    adapter_path = None

    def __init__(self, *replies):
        self.replies = list(replies)

    def adapters(self):
        return []

    def stream_chat(self, messages, tools=None, **kw):
        r = self.replies.pop(0)
        msg = ({"role": "assistant", "content": r} if isinstance(r, str) else
               {"role": "assistant", "content": "", "tool_calls": [
                   {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
                   for i, (name, args) in enumerate(r)]})
        yield {"type": "done", "message": msg, "finish_reason": "stop",
               "ttft_s": 0.1, "total_s": 0.2, "timings": {}}


MISSING = object()  # no request body at all (routes.Request keeps body None)


class LoomApiCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AFTERGLOW_RUNS"] = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.environ.pop, "AFTERGLOW_RUNS", None)
        self._worlds = 0

    def session(self, *replies, world=None, day=1):
        if world is None:  # every session opens a fresh day; helpers never reopen each other's logs
            self._worlds += 1
            world = f"w-test-{self._worlds}"
        return DaySession(world, day, ScriptedClient(*replies), date="2026-10-27",
                          decide_supersede=False, decide_route=False, web_search=False)

    def app(self, session):
        return SimpleNamespace(session=session, lock=threading.Lock())

    def request(self, method, path, body):
        raw = None if body is MISSING else json.dumps(body).encode()
        return routes.Request(method, path, "", {"Content-Type": "application/json"}, raw)

    def post(self, path, body, app):
        fn, _ = routes.find("POST", path)
        self.assertIsNotNone(fn, f"{path} is not registered")
        return fn(app, self.request("POST", path, body))

    def get(self, path, app):
        fn, _ = routes.find("GET", path)
        self.assertIsNotNone(fn, f"{path} is not registered")
        return fn(app, self.request("GET", path, MISSING))

    def consume(self, out):
        self.assertIsInstance(out, routes.Stream)
        self.assertTrue(out.lock)  # a branch turn holds the chat lock, like /api/chat
        return list(out.events)

    def records(self, s):
        return [json.loads(x) for x in s.daylog_path.read_text().splitlines()]

    def seal(self, s, *names, night="night"):
        d = s.dir / night
        d.mkdir(parents=True, exist_ok=True)
        for n in names:
            (d / n).write_text("{}")

    def chat_two_turns(self):
        """main, turn 1 (office fact N0001) and turn 2 (a plain answer); the client holds no further replies."""
        s = self.session([("remember", {"key": "office", "value": "Room 312"})], "Noted. Room 312.",
                         "Standup is at 10:30.")
        s.ask(T1)
        s.ask(T2)
        return s, self.app(s)

    def three_branches(self):
        """main (T1 office=312, T2) with an edited branch b2 (office=318, standup=10:30) and a same-text
        regenerate b3 of T2 that re-saves office=312. Ends back on main. Notes: N0001..N0004."""
        s = self.session([("remember", {"key": "office", "value": "Room 312"})], "Noted. Room 312.",
                         "Standup is at 10:30.",
                         [("remember", {"key": "office", "value": "Room 318"}),
                          ("remember", {"key": "standup time", "value": "10:30"})], "Saved both.",
                         [("remember", {"key": "office", "value": "Room 312"})], "Saved again.")
        app = self.app(s)
        s.ask(T1)
        s.ask(T2)
        self.consume(self.post("/api/branch", {"at_turn": 1, "text": EDIT}, app))
        self.post("/api/branch/switch", {"branch": "main"}, app)
        self.consume(self.post("/api/branch/regenerate", {"turn": 2}, app))
        self.post("/api/branch/switch", {"branch": "main"}, app)
        return s, app


class LoomRoutesTest(unittest.TestCase):
    def test_every_loom_route_is_registered(self):
        self.assertEqual(set(loom_api.ROUTES),
                         {("POST", "/api/branch"), ("POST", "/api/branch/regenerate"),
                          ("POST", "/api/branch/switch"), ("GET", "/api/loom"),
                          ("POST", "/api/loom/resolve")})
        for (method, path), fn in loom_api.ROUTES.items():
            found, rest = routes.find(method, path)
            self.assertIs(found, fn)
            self.assertEqual(rest, "")


class BranchTest(LoomApiCase):
    def test_branch_streams_the_new_turn_and_forks_only_when_consumed(self):
        s, app = self.chat_two_turns()
        s.client.replies.extend([[("remember", {"key": "office", "value": "Room 318"})], "Saved. Room 318."])
        before = s.daylog_path.read_bytes()
        out = self.post("/api/branch", {"at_turn": 1, "text": EDIT}, app)
        self.assertIsInstance(out, routes.Stream)
        self.assertTrue(out.lock)
        self.assertEqual(s.daylog_path.read_bytes(), before)  # the handler only validates; nothing is forked
        self.assertEqual(s.branch, "main")
        events = list(out.events)
        last = events[-1]
        self.assertEqual(last["type"], "turn_done")
        self.assertEqual((last["turn"], last["content"], last["notes_written"]), (3, "Saved. Room 318.", ["N0002"]))
        self.assertIn("round_done", {e["type"] for e in events})  # the same events /api/chat sends
        self.assertEqual(s.branch, "b2")
        self.assertIn({"role": "fork", "branch": "b2", "parent": "main", "at_turn": 1},
                      [{k: r.get(k) for k in ("role", "branch", "parent", "at_turn")} for r in self.records(s)])
        self.assertEqual([r["content"] for r in s.history() if r["role"] == "user"], [EDIT])

    def test_branch_validates_before_streaming_and_writes_nothing(self):
        s, app = self.chat_two_turns()
        before = s.daylog_path.read_bytes()
        bad = [{}, {"text": EDIT}, {"at_turn": 1}, {"at_turn": 0, "text": EDIT}, {"at_turn": -1, "text": EDIT},
               {"at_turn": "1", "text": EDIT}, {"at_turn": True, "text": EDIT}, {"at_turn": 1.5, "text": EDIT},
               {"at_turn": 99, "text": EDIT}, {"at_turn": 1, "text": ""}, {"at_turn": 1, "text": "  "},
               {"at_turn": 1, "text": 5}, ["not", "an", "object"], MISSING]
        for body in bad:
            with self.assertRaises(ValueError, msg=body):
                self.post("/api/branch", body, app)
        self.assertEqual(s.daylog_path.read_bytes(), before)
        self.assertEqual(s.branch, "main")

    def test_branch_refuses_a_busy_day(self):
        _, app = self.chat_two_turns()
        with app.lock:
            with self.assertRaises(RuntimeError):
                self.post("/api/branch", {"at_turn": 1, "text": EDIT}, app)

    def test_a_branch_consumed_after_a_switch_reports_an_error_not_a_turn(self):
        s, app = self.chat_two_turns()
        s.client.replies.extend(["late reply"])
        out = self.post("/api/branch", {"at_turn": 1, "text": EDIT}, app)
        s.fork(1)  # another request forked first; the captured line is stale
        events = list(out.events)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "error")
        self.assertIn("changed", events[0]["message"])
        self.assertEqual([r for r in self.records(s) if r.get("role") == "user" and r.get("turn") == 3], [])

    def test_a_branch_consumed_after_the_night_seals_reports_an_error(self):
        s, app = self.chat_two_turns()
        s.client.replies.extend(["late reply"])
        out = self.post("/api/branch", {"at_turn": 1, "text": EDIT}, app)
        self.seal(s, "eval.json")
        events = list(out.events)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "error")
        self.assertIn("sealed", events[0]["message"])
        self.assertEqual([r for r in self.records(s) if r.get("role") == "fork"], [])


class RegenerateTest(LoomApiCase):
    def test_regenerate_replays_the_same_words_on_a_new_branch(self):
        s, app = self.chat_two_turns()
        s.client.replies.extend(["Standup is at 10:30, I believe."])
        before = s.daylog_path.read_bytes()
        out = self.post("/api/branch/regenerate", {"turn": 2}, app)
        self.assertEqual(s.daylog_path.read_bytes(), before)  # forked when consumed, like a branch
        last = self.consume(out)[-1]
        self.assertEqual((last["type"], last["turn"], last["content"]),
                         ("turn_done", 3, "Standup is at 10:30, I believe."))
        self.assertEqual(s.branch, "b2")
        self.assertEqual([r["content"] for r in s.history() if r["role"] == "user"], [T1, T2])

    def test_regenerate_validates_and_refuses_a_busy_day(self):
        s, app = self.chat_two_turns()
        before = s.daylog_path.read_bytes()
        for body in [{}, {"turn": 0}, {"turn": -2}, {"turn": "2"}, {"turn": True}, {"turn": 99}, MISSING]:
            with self.assertRaises(ValueError, msg=body):
                self.post("/api/branch/regenerate", body, app)
        self.assertEqual(s.daylog_path.read_bytes(), before)
        with app.lock:
            with self.assertRaises(RuntimeError):
                self.post("/api/branch/regenerate", {"turn": 2}, app)


class SwitchTest(LoomApiCase):
    def test_switch_moves_the_line_and_returns_the_loom_state(self):
        s, app = self.three_branches()
        back = self.post("/api/branch/switch", {"branch": "b2"}, app)
        self.assertEqual(back["line"], "b2")
        self.assertEqual(back["branches"], ["main", "b2", "b3"])
        self.assertEqual(back["forks"], {"b2": {"parent": "main", "at_turn": 1},
                                        "b3": {"parent": "main", "at_turn": 2}})
        self.assertEqual(back["alternatives"],
                         {3: [{"branch": "main", "turn": 1, "text": T1},
                              {"branch": "b2", "turn": 3, "text": EDIT}]})
        self.assertEqual(s.branch, "b2")
        self.assertEqual([r["content"] for r in s.history() if r["role"] == "user"], [EDIT])
        fwd = self.post("/api/branch/switch", {"branch": "main"}, app)
        self.assertEqual(fwd["line"], "main")
        self.assertEqual([r["content"] for r in s.history() if r["role"] == "user"], [T1, T2])

    def test_switch_validates(self):
        s, app = self.chat_two_turns()
        before = s.daylog_path.read_bytes()
        for body in [{}, {"branch": 2}, {"branch": None}, {"branch": "b9"}, MISSING]:
            with self.assertRaises(ValueError, msg=body):
                self.post("/api/branch/switch", body, app)
        self.assertEqual(s.daylog_path.read_bytes(), before)

    def test_switch_refuses_a_busy_day_and_a_sealed_one(self):
        s, app = self.three_branches()
        with app.lock:
            with self.assertRaises(RuntimeError):
                self.post("/api/branch/switch", {"branch": "b2"}, app)
        self.post("/api/loom/resolve", {}, app)
        answer = (s.dir / "loom-resolution.json").read_bytes()
        self.seal(s, "receipt.json")  # its night ran on main: the line and its answer stay as they were trained
        log = s.daylog_path.read_bytes()
        with self.assertRaises(RuntimeError) as e:
            self.post("/api/branch/switch", {"branch": "b2"}, app)
        self.assertIn("sealed", str(e.exception))
        self.assertEqual(s.branch, "main")
        self.assertEqual(s.daylog_path.read_bytes(), log)
        self.assertEqual((s.dir / "loom-resolution.json").read_bytes(), answer)


class LineNotesTest(LoomApiCase):
    """The notes follow the line (loom.line_notes): what was said on another version is set aside, not active."""

    def status(self, s):
        doc = json.loads((s.dir / "notes.json").read_text())["notes"]
        return {n["id"]: (n["value"], n["status"], n.get("superseded_by")) for n in doc}

    def test_switching_back_sets_the_edited_fact_aside_and_restores_the_first(self):
        s, app = self.chat_two_turns()
        s.client.replies += [[("remember", {"key": "office", "value": "Room 318"})], "Saved. Room 318."]
        self.consume(self.post("/api/branch", {"at_turn": 1, "text": EDIT}, app))
        self.assertEqual(self.status(s), {"N0001": ("Room 312", "other_version", None),
                                          "N0002": ("Room 318", "active", None)})
        self.post("/api/branch/switch", {"branch": "main"}, app)
        self.assertEqual(self.status(s), {"N0001": ("Room 312", "active", None),
                                          "N0002": ("Room 318", "other_version", None)})
        self.assertEqual([n["value"] for n in s.notes.recall("office")], ["Room 312"])
        self.post("/api/branch/switch", {"branch": "b2"}, app)
        self.assertEqual(self.status(s)["N0002"], ("Room 318", "active", None))
        self.assertEqual([n["value"] for n in s.notes.recall("office")], ["Room 318"])

    def test_a_correction_on_the_line_after_a_set_aside_one_supersedes_the_first(self):
        s, app = self.chat_two_turns()  # main: T1 saves 312 (N0001), T2 answers
        s.client.replies += [[("remember", {"key": "office", "value": "Room 318"})], "Saved. Room 318.",
                             [("remember", {"key": "office", "value": "Room 320"})], "Saved. Room 320."]
        self.consume(self.post("/api/branch", {"at_turn": 2, "text": "Actually it is Room 318."}, app))
        self.post("/api/branch/switch", {"branch": "main"}, app)
        s.ask("Correction: my office is Room 320.")  # on main, after b2's 318 was set aside
        st = self.status(s)
        self.assertEqual(st["N0001"], ("Room 312", "superseded", "N0003"))
        self.assertEqual(st["N0002"][1], "other_version")
        self.assertEqual(st["N0003"], ("Room 320", "active", None))
        self.post("/api/branch/switch", {"branch": "b2"}, app)  # b2 never heard 320: its own 318 is back
        st = self.status(s)
        self.assertEqual(st["N0001"], ("Room 312", "superseded", "N0002"))
        self.assertEqual(st["N0002"], ("Room 318", "active", None))
        self.assertEqual(st["N0003"][1], "other_version")

    def test_a_day_without_forks_never_rewrites_its_notes(self):
        s, app = self.chat_two_turns()
        before = (s.dir / "notes.json").read_bytes()
        s._line_notes()
        self.assertEqual((s.dir / "notes.json").read_bytes(), before)


class LoomStateTest(LoomApiCase):
    def test_state_reports_branches_alternatives_and_contradictions(self):
        s, app = self.three_branches()
        st = self.get("/api/loom", app)
        self.assertEqual(st["line"], "main")
        self.assertEqual(st["branches"], ["main", "b2", "b3"])
        self.assertEqual(st["forks"], {"b2": {"parent": "main", "at_turn": 1},
                                      "b3": {"parent": "main", "at_turn": 2}})
        self.assertEqual(st["alternatives"],
                         {1: [{"branch": "main", "turn": 1, "text": T1},
                              {"branch": "b2", "turn": 3, "text": EDIT}],
                          2: [{"branch": "main", "turn": 2, "text": T2},
                              {"branch": "b3", "turn": 4, "text": T2}]})
        # only the edited branch disagrees: its office fact conflicts, its standup fact is extra. The
        # same-text regenerate (N0004) and the shared prefix (N0001, the line side of the conflict) add nothing.
        self.assertEqual(st["contradictions"],
                         {"line": "main",
                          "conflicts": [{"key": "office", "line": {"id": "N0001", "value": "Room 312"},
                                         "side": {"id": "N0002", "value": "Room 318", "branch": "b2"}}],
                          "extra": [{"key": "standup time", "value": "10:30", "id": "N0003", "branch": "b2"}]})
        self.assertEqual(st["side_branches"], ["b2"])
        self.assertFalse(st["sealed"])
        self.assertIsNone(st["resolution"])
        self.assertEqual(st["left_out"], [])
        # the thread's steppers: the edit under your first message, the second answer under the reply to T2
        self.assertEqual(st["versions"],
                         {"messages": {1: [{"branch": "main", "turn": 1, "text": T1},
                                           {"branch": "b2", "turn": 3, "text": EDIT}]},
                          "replies": {2: [{"branch": "main", "turn": 2, "text": T2},
                                          {"branch": "b3", "turn": 4, "text": T2}]}})
        self.assertEqual(st["version_words"], {"b2": EDIT})  # b3 asked T2 again: the same words as main

    def test_a_shorter_prefix_branch_adds_no_evidence(self):
        s = self.session([("remember", {"key": "office", "value": "Room 312"})], "Noted. Room 312.",
                         [("remember", {"key": "standup time", "value": "10:30"})], "Saved.",
                         "Standup is at 10:30.")
        app = self.app(s)
        s.ask(T1)
        self.consume(self.post("/api/branch/regenerate", {"turn": 1}, app))  # b2: [T1], a prefix of main
        self.post("/api/branch/switch", {"branch": "main"}, app)
        s.ask(T2)
        st = self.get("/api/loom", app)
        self.assertEqual(st["branches"], ["main", "b2"])
        self.assertEqual(st["side_branches"], [])
        self.assertEqual(st["contradictions"], {"line": "main", "conflicts": [], "extra": []})
        self.assertEqual(st["alternatives"],
                         {1: [{"branch": "main", "turn": 1, "text": T1},
                              {"branch": "b2", "turn": 2, "text": T1}]})
        with self.assertRaises(ValueError):  # a prefix branch is not a side branch to add
            self.post("/api/loom/resolve", {"add_branches": ["b2"]}, app)

    def test_state_refuses_a_busy_day(self):
        _, app = self.chat_two_turns()
        with app.lock:
            with self.assertRaises(RuntimeError):
                self.get("/api/loom", app)


class ResolveTest(LoomApiCase):
    RESOLVE = {"keep_side": ["N0002", "N0003"], "add_branches": ["b2"]}

    def test_resolve_writes_and_returns_the_answer(self):
        s, app = self.three_branches()
        doc = self.post("/api/loom/resolve", dict(self.RESOLVE), app)
        self.assertEqual(doc["world"], s.world)
        self.assertEqual(doc["day"], 1)
        self.assertEqual(doc["line"], "main")
        self.assertEqual(doc["turn"], s.turn)
        self.assertEqual(doc["keep_side"], ["N0002", "N0003"])
        self.assertEqual(doc["add_branches"], ["b2"])
        self.assertTrue(doc["resolved_at"])
        stored = json.loads((s.dir / "loom-resolution.json").read_text())
        self.assertEqual(doc, stored)  # what the night reads is what the call returned
        self.assertEqual(self.get("/api/loom", app)["resolution"], stored)

    def test_resolve_validates_choices_and_writes_nothing(self):
        s, app = self.three_branches()
        bad = [{"keep_side": "N0002"}, {"keep_side": ["N0002", 7]}, {"keep_side": ["N0001"]},
               {"keep_side": ["N0004"]}, {"keep_side": ["N9999"]}, {"add_branches": "b2"},
               {"add_branches": ["b3"]}, {"add_branches": ["main"]}, {"add_branches": ["b9"]},
               {"keep_side": [], "add_branches": ["b2", 3]}, MISSING]
        for body in bad:
            with self.assertRaises(ValueError, msg=body):
                self.post("/api/loom/resolve", body, app)
        self.assertFalse((s.dir / "loom-resolution.json").exists())

    def test_resolve_accepts_an_empty_answer_and_dedupes(self):
        s, app = self.chat_two_turns()
        doc = self.post("/api/loom/resolve", {}, app)
        self.assertEqual((doc["keep_side"], doc["add_branches"]), ([], []))
        s, app = self.three_branches()
        doc = self.post("/api/loom/resolve", {"keep_side": ["N0002", "N0002"], "add_branches": ["b2", "b2"]}, app)
        self.assertEqual((doc["keep_side"], doc["add_branches"]), (["N0002"], ["b2"]))

    def test_resolve_refuses_a_busy_day_but_allows_a_sealed_one(self):
        s, app = self.three_branches()
        with app.lock:
            with self.assertRaises(RuntimeError):
                self.post("/api/loom/resolve", dict(self.RESOLVE), app)
        self.seal(s, "eval.json")
        doc = self.post("/api/loom/resolve", dict(self.RESOLVE), app)
        self.assertEqual(doc["add_branches"], ["b2"])


class SealedTest(LoomApiCase):
    def test_each_night_output_seals_the_day(self):
        for name in loom_api.NIGHT_OUTPUTS:
            with self.subTest(name):
                s, app = self.chat_two_turns()
                self.seal(s, name)
                self.assertTrue(self.get("/api/loom", app)["sealed"])
        s, app = self.chat_two_turns()  # a rerun night seals too
        self.seal(s, "eval.json", night="night-2b")
        self.assertTrue(self.get("/api/loom", app)["sealed"])

    def test_an_empty_night_or_settings_alone_do_not_seal(self):
        for names in [(), ("steering-decisions.json",), ("night-links.json",)]:
            with self.subTest(names):
                s, app = self.chat_two_turns()
                self.seal(s, *names)
                self.assertFalse(self.get("/api/loom", app)["sealed"])
        s, app = self.chat_two_turns()  # and the day still forks
        self.seal(s, "steering-decisions.json")
        s.client.replies.extend(["A third answer."])
        last = self.consume(self.post("/api/branch", {"at_turn": 2, "text": "When is standup, exactly?"}, app))[-1]
        self.assertEqual(last["type"], "turn_done")

    def test_sealed_days_refuse_forks_with_the_log_untouched(self):
        s, app = self.three_branches()
        self.seal(s, "consolidated.json")
        before = s.daylog_path.read_bytes()
        with self.assertRaises(RuntimeError):
            self.post("/api/branch", {"at_turn": 1, "text": EDIT}, app)
        with self.assertRaises(RuntimeError):
            self.post("/api/branch/regenerate", {"turn": 2}, app)
        self.assertEqual(s.daylog_path.read_bytes(), before)
        self.assertEqual(s.branch, "main")

    def test_left_out_names_unadded_branches_only_once_sealed(self):
        s, app = self.three_branches()
        self.assertEqual(self.get("/api/loom", app)["left_out"], [])
        self.seal(s, "eval.json")
        self.assertEqual(self.get("/api/loom", app)["left_out"], ["b2"])
        self.post("/api/loom/resolve", {"add_branches": ["b2"]}, app)
        self.assertEqual(self.get("/api/loom", app)["left_out"], [])


class SideBranchDedupTest(LoomApiCase):
    def test_branches_with_the_same_words_are_one_morning_offer(self):
        # b2 edits turn 1 (office=Room 10, N0002); b3 regenerates b2's turn with the same words and
        # saves one more fact (N0003). Back on main, both differ from the line, but they are one path.
        s = self.session([("remember", {"key": "office", "value": "Room 09"})], "Noted. Room 09.",
                         [("remember", {"key": "office", "value": "Room 10"})], "Saved. Room 10.",
                         [("remember", {"key": "standup time", "value": "10:30"})], "Saved standup.")
        app = self.app(s)
        s.ask("My office is Room 09.")
        self.consume(self.post("/api/branch", {"at_turn": 1, "text": "My office is Room 10."}, app))
        self.consume(self.post("/api/branch/regenerate", {"turn": 2}, app))
        self.post("/api/branch/switch", {"branch": "main"}, app)
        st = self.get("/api/loom", app)
        self.assertEqual(st["branches"], ["main", "b2", "b3"])
        self.assertEqual(st["side_branches"], ["b2"])  # the first branch represents those words
        # the duplicate path's note is baseline only, never side evidence
        self.assertEqual(st["contradictions"],
                         {"line": "main",
                          "conflicts": [{"key": "office", "line": {"id": "N0001", "value": "Room 09"},
                                         "side": {"id": "N0002", "value": "Room 10", "branch": "b2"}}],
                          "extra": []})
        with self.assertRaises(ValueError):
            self.post("/api/loom/resolve", {"add_branches": ["b3"]}, app)
        with self.assertRaises(ValueError):
            self.post("/api/loom/resolve", {"keep_side": ["N0003"]}, app)


class ResolveStalenessTest(LoomApiCase):
    def test_a_resolution_lapses_when_the_day_moves_on(self):
        s, app = self.three_branches()
        doc = self.post("/api/loom/resolve", {"keep_side": ["N0002"], "add_branches": ["b2"]}, app)
        self.assertEqual(self.get("/api/loom", app)["resolution"], doc)
        s.client.replies.extend(["Another answer."])
        s.ask("Anything else?")  # a new turn: the saved turn no longer matches
        st = self.get("/api/loom", app)
        self.assertIsNone(st["resolution"])
        self.assertTrue((s.dir / "loom-resolution.json").exists())  # the file stays; only the read lapses
        self.post("/api/loom/resolve", {"keep_side": ["N0002"], "add_branches": ["b2"]}, app)
        self.assertIsNotNone(self.get("/api/loom", app)["resolution"])
        self.post("/api/branch/switch", {"branch": "b2"}, app)  # another line: the answer lapses too
        self.assertIsNone(self.get("/api/loom", app)["resolution"])
        # ...and is removed, so the night (which trains the file's "line") follows b2, not the old main
        self.assertFalse((s.dir / "loom-resolution.json").exists())

    def test_switching_to_the_same_line_keeps_the_answer(self):
        s, app = self.three_branches()
        self.post("/api/loom/resolve", {"keep_side": [], "add_branches": []}, app)
        self.post("/api/branch/switch", {"branch": "main"}, app)
        self.assertTrue((s.dir / "loom-resolution.json").exists())

    def test_an_edit_on_another_line_removes_the_answer(self):
        s, app = self.three_branches()
        self.post("/api/loom/resolve", {"keep_side": [], "add_branches": []}, app)
        s.client.replies.extend(["Edited again."])
        self.consume(self.post("/api/branch", {"at_turn": 2, "text": "Standup moved to 11:00."}, app))
        self.assertNotEqual(s.branch, "main")
        self.assertFalse((s.dir / "loom-resolution.json").exists())


class RegenLineageTest(LoomApiCase):
    def test_a_same_text_regen_line_keeps_the_prior_branch_facts_as_baseline(self):
        # main turn 1 saves office=Room 09 (N0001); b2 edits it to Room 10 (N0002); b3 regenerates
        # b2's turn with the same words. b2's note is off b3's line, so b3 saves its own (N0003).
        s = self.session([("remember", {"key": "office", "value": "Room 09"})], "Noted. Room 09.",
                         [("remember", {"key": "office", "value": "Room 10"})], "Saved. Room 10.",
                         [("remember", {"key": "office", "value": "Room 10"})], "Still Room 10.")
        app = self.app(s)
        s.ask("My office is Room 09.")
        last = self.consume(self.post("/api/branch", {"at_turn": 1, "text": "My office is Room 10."}, app))[-1]
        self.assertEqual(last["notes_written"], ["N0002"])
        last = self.consume(self.post("/api/branch/regenerate", {"turn": 2}, app))[-1]
        self.assertEqual(last["notes_written"], ["N0003"])  # b2's Room 10 is set aside on b3's line
        self.assertEqual(s.branch, "b3")
        st = self.get("/api/loom", app)
        self.assertEqual(st["line"], "b3")
        self.assertEqual(st["forks"], {"b2": {"parent": "main", "at_turn": 1},
                                      "b3": {"parent": "b2", "at_turn": 2}})
        self.assertEqual(st["alternatives"],
                         {3: [{"branch": "b2", "turn": 2, "text": "My office is Room 10."},
                              {"branch": "b3", "turn": 3, "text": "My office is Room 10."}]})
        # b2's fact stays the baseline the older main fact is read against: a conflict, not an extra.
        self.assertEqual(st["contradictions"],
                         {"line": "b3",
                          "conflicts": [{"key": "office", "line": {"id": "N0003", "value": "Room 10"},
                                         "side": {"id": "N0001", "value": "Room 09", "branch": "main"}}],
                          "extra": []})
        # b2 has the same words as the current line, so its facts are baseline, never offered as side facts.
        self.assertEqual(st["side_branches"], ["main"])
        with self.assertRaises(ValueError):
            self.post("/api/loom/resolve", {"add_branches": ["b2"]}, app)
        doc = self.post("/api/loom/resolve", {"keep_side": ["N0001"]}, app)
        self.assertEqual(doc["keep_side"], ["N0001"])


class PriorDayTest(LoomApiCase):
    def get_day(self, app, day):
        req = routes.Request("GET", "/api/loom", f"day={day}", {}, None)
        return loom_api.state(app, req)

    def snapshot(self):
        return {str(p.relative_to(self.tmp.name)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in Path(self.tmp.name).rglob("*") if p.is_file()}

    def prior_and_today(self):
        prior, app = self.three_branches()
        self.seal(prior, "eval.json")
        expected = self.get("/api/loom", app)
        today = self.session(world=prior.world, day=2)
        app.session = today
        return prior, today, app, expected

    def test_prior_get_matches_open_loom_and_changes_no_files_or_session(self):
        prior, today, app, expected = self.prior_and_today()
        before = self.snapshot()
        self.assertEqual(self.get_day(app, 1), expected)
        self.assertEqual(self.get_day(app, 2), self.get("/api/loom", app))
        self.assertEqual(self.snapshot(), before)
        self.assertIs(app.session, today)
        self.assertEqual((today.branch, today.turn, today.client.replies), ("main", 0, []))
        self.assertEqual(expected["left_out"], ["b2"])
        self.assertTrue(expected["contradictions"]["conflicts"])

    def test_prior_resolve_targets_only_prior_day_and_survives_readback(self):
        prior, today, app, _ = self.prior_and_today()
        before = self.snapshot()
        body = {"day": 1, "keep_side": ["N0002", "N0002"], "add_branches": ["b2", "b2"]}
        doc = self.post("/api/loom/resolve", body, app)
        self.assertEqual((doc["world"], doc["day"], doc["line"], doc["turn"]),
                         (prior.world, 1, prior.branch, prior.turn))
        self.assertEqual((doc["keep_side"], doc["add_branches"]), (["N0002"], ["b2"]))
        after = self.snapshot()
        answer = f"{prior.world}/1/loom-resolution.json"
        self.assertEqual(set(after) - set(before), {answer})
        self.assertEqual({k: v for k, v in after.items() if k != answer}, before)
        self.assertEqual(json.loads((prior.dir / "loom-resolution.json").read_text()), doc)
        self.assertEqual(self.get_day(app, 1)["resolution"], doc)
        self.assertEqual(self.get_day(app, 1)["left_out"], [])
        self.assertIsNone(self.get("/api/loom", app)["resolution"])
        self.assertFalse((today.dir / "loom-resolution.json").exists())
        self.assertIs(app.session, today)

    def test_bad_prior_days_do_not_create_folders_or_answers(self):
        _, _, app, _ = self.prior_and_today()
        before = self.snapshot()
        for day in ("0", "-1", "1.5", "hello", "../1", "3", "999", "%C2%B2"):
            with self.subTest(day=day), self.assertRaises(ValueError):
                self.get_day(app, day)
        for day in (0, -1, 1.5, "1", True, None, 3, 999, "../1"):
            with self.subTest(day=day), self.assertRaises(ValueError):
                self.post("/api/loom/resolve", {"day": day}, app)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((Path(self.tmp.name) / app.session.world / "999").exists())

    def test_prior_choices_are_checked_against_prior_loom_not_today(self):
        prior, today, app, _ = self.prior_and_today()
        for body in ({"day": 1, "keep_side": ["N9999"]}, {"day": 1, "keep_side": ["N0001"]},
                     {"day": 1, "add_branches": ["b3"]}, {"day": 1, "add_branches": ["main"]},
                     {"day": 2, "add_branches": ["b2"]}, {"day": 2, "keep_side": ["N0002"]}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.post("/api/loom/resolve", body, app)
        self.assertFalse((prior.dir / "loom-resolution.json").exists())
        self.assertFalse((today.dir / "loom-resolution.json").exists())

    def test_prior_resolution_staleness_uses_prior_line_and_global_turn(self):
        prior, today, app, _ = self.prior_and_today()
        self.post("/api/loom/resolve", {"day": 1, "add_branches": ["b2"]}, app)
        today.client.replies.append("Today is different.")
        today.ask("A new day.")
        self.assertIsNotNone(self.get_day(app, 1)["resolution"])
        prior.switch("b2")
        self.assertIsNone(self.get_day(app, 1)["resolution"])
        prior.switch("main")
        self.assertIsNotNone(self.get_day(app, 1)["resolution"])
        prior.client.replies.append("Another prior turn.")
        prior.ask("One more thing.")
        self.assertIsNone(self.get_day(app, 1)["resolution"])

    def test_empty_prior_day_never_seeds_missing_notes_or_log(self):
        prior = self.session(world="empty", day=1)
        today = self.session(world="empty", day=2)
        (prior.dir / "notes.json").unlink(missing_ok=True)
        app = self.app(today)
        before = self.snapshot()
        st = self.get_day(app, 1)
        self.assertEqual(st["branches"], ["main"])
        self.assertEqual(st["contradictions"], {"line": "main", "conflicts": [], "extra": []})
        self.assertEqual(self.snapshot(), before)
        doc = self.post("/api/loom/resolve", {"day": 1}, app)
        self.assertEqual((doc["turn"], doc["keep_side"], doc["add_branches"]), (0, [], []))

    def test_prior_day_is_always_of_the_open_person(self):
        prior, today, app, _ = self.prior_and_today()
        other = self.session(world="other", day=1)
        req = routes.Request("GET", "/api/loom", "day=1&world=other", {}, None)
        self.assertEqual(loom_api.state(app, req)["branches"], ["main", "b2", "b3"])
        doc = self.post("/api/loom/resolve", {"day": 1, "world": "other", "add_branches": ["b2"]}, app)
        self.assertEqual(doc["world"], prior.world)
        self.assertFalse((other.dir / "loom-resolution.json").exists())


if __name__ == "__main__":
    unittest.main()
