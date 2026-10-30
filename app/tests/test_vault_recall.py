"""Offline tests for the vault notes backend (notes.OneironNotes, agent.DaySession(notes_backend="vault"), the recall
tool) and vault recall (oneiron_vault.OneironVault.recall): every recall names deadline_ms; the vault answers the recall
tool; a vault error or a pack the vault cut short (retrieval_meta.partial) answers that call from the local notes and is
logged; every recall is timed into the day log; notes reach the vault with their turn, in push_day's shape and ids.
No network: the facade and the vault are stand-ins.

  python3 -m unittest discover -s app/tests
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import oneiron_vault  # noqa: E402
from afterglow.agent import DaySession  # noqa: E402
from afterglow.notes import (LocalJsonNotes, OneironNotes, conversation_ref, spread, terms,  # noqa: E402
                             world_conversations)
from afterglow.tools import ToolBox  # noqa: E402


class StandInVault:
    """A vault that keeps what it is witnessed and answers recall from it: one TURN item per witnessed run whose
    messages share a word with the query, quoting those messages. `extra` items come first in every reply; `fail`
    makes recall raise, `partial` marks the pack cut short."""

    origin = "https://vault.test"

    def __init__(self, partial=False, fail=None, extra=(), waking=None, down=False):
        self.partial, self.fail, self.extra, self.waking, self.down = partial, fail, list(extra), waking, down
        self.runs, self.asked, self.woken = [], [], 0

    def wake(self):
        if self.waking:  # a cold start: the wake returns when the test sets the event
            self.waking.wait(5)
        if self.down:
            raise oneiron_vault.VaultError(503, "vault_wake_timeout")
        self.woken += 1

    def client_for(self, author):
        return self

    def witness_run(self, conv, author, texts, at, ids=None):
        if self.down:
            raise oneiron_vault.VaultError(503, "vault_wake_timeout")
        self.runs.append({"conv": conv, "author": author, "texts": list(texts), "at": at, "ids": ids})
        return {"receipt_ref": f"r{len(self.runs)}", "turn_short_id": f"t{len(self.runs)}"}

    def recall(self, query, limit=5, timeout=None):
        self.asked.append({"query": query, "limit": limit, "timeout": timeout})
        if self.down:
            raise oneiron_vault.VaultError(503, "vault_wake_timeout")
        if self.fail:
            raise self.fail
        items = list(self.extra)
        for i, run in enumerate(self.runs):
            hits = [t for t in run["texts"] if terms(query) & terms(t)]
            if hits:
                items.append({"short_id": f"t{i + 1}", "kind": "TURN", "value_text": " ".join(run["texts"]),
                              "conversation_id": run["conv"],
                              "cited_messages": [{"short_id": f"m{i + 1}.{j}", "value_text": t}
                                                 for j, t in enumerate(hits)]})
        return {"items": items[:limit], "retrieval_meta": {"partial": self.partial}}


def item(conv, text, kind="TURN"):
    return {"short_id": "x", "kind": kind, "value_text": text, "conversation_id": conv,
            "cited_messages": [{"short_id": "xm", "value_text": text}] if conv else []}


class VaultClientTest(unittest.TestCase):
    def test_the_client_names_its_deadline_and_waits_past_it(self):
        sent = {}
        v = oneiron_vault.OneironVault.__new__(oneiron_vault.OneironVault)
        v.facade = lambda verb, body, timeout=None: sent.update(verb=verb, body=body, timeout=timeout) or {}
        v.recall("lab mate", limit=3)
        self.assertEqual(sent["verb"], "vault.recall")
        self.assertEqual(sent["body"]["deadline_ms"], 30000)
        self.assertGreater(sent["timeout"], 30)

    def test_the_two_speaker_client_recalls_with_the_companion_credential(self):
        sv = oneiron_vault.SpeakerVaults.__new__(oneiron_vault.SpeakerVaults)
        asked = []
        sv.by_author = {"user": None, "companion": type("C", (), {"recall": lambda self, *a, **kw: asked.append(kw) or {}})()}
        sv.recall("lab mate")
        self.assertEqual(asked[0]["deadline_ms"], 30000)

    def test_a_partial_pack_is_not_finished(self):
        self.assertTrue(oneiron_vault.recall_partial({"retrieval_meta": {"partial": True}}))
        self.assertFalse(oneiron_vault.recall_partial({"items": []}))

    def test_the_latency_spread_is_nearest_rank(self):
        self.assertEqual(spread(list(range(1, 11))), {"n": 10, "p50": 5, "p90": 9, "max": 10})
        self.assertEqual(spread([3100]), {"n": 1, "p50": 3100, "p90": 3100, "max": 3100})
        self.assertEqual(spread([]), {"n": 0})

    def test_a_day_reads_its_world_s_earlier_days_and_its_own_conversation(self):
        convs = world_conversations("w", 3, 2, tag="m1")
        self.assertIn(conversation_ref("w", 1, 1), convs)
        self.assertIn(conversation_ref("w", 2, 2), convs)
        self.assertIn(conversation_ref("w", 3, 2, "m1"), convs)
        self.assertNotIn(conversation_ref("w", 3, 2), convs)  # the day's real conversation, under a measured rerun
        self.assertLessEqual({conversation_ref("w", 3, 1), conversation_ref("w", 3, 2)}, world_conversations("w", 3, 2))
        self.assertNotIn(conversation_ref("other", 2, 2), convs)
        self.assertEqual(conversation_ref("w", 3, 2), conversation_ref("w", 3))  # no tag: the ids push_day uses


class VaultNotesTest(unittest.TestCase):
    def notes(self, wait=True, **kw):
        local = LocalJsonNotes(Path(tempfile.mkdtemp()) / "notes.json")
        n = OneironNotes(local, "w", 2, vault=StandInVault(**kw))
        if wait:
            n._up.wait(5)
        return n

    def test_a_recall_while_the_vault_wakes_answers_from_the_local_notes(self):
        started = threading.Event()
        n = self.notes(wait=False, waking=started)
        n.local.remember("lab mate", "Jonas Weber", day=1, turn=1)
        self.assertEqual([x["value"] for x in n.recall("lab mate")], ["Jonas Weber"])
        self.assertEqual((n.recalls[-1]["outcome"], n.vault.asked), ("fallback_waking", []))  # nothing waited
        self.assertEqual((n.vault_state, n.wakes), ("waking", []))
        started.set()
        n._up.wait(5)
        self.assertEqual((n.vault_state, n.vault.woken, n.wakes[0]["outcome"]), ("up", 1, "awake"))
        self.assertIsInstance(n.wakes[0]["ms"], int)
        n.recall("lab mate")
        self.assertEqual(n.recalls[-1]["outcome"], "vault")
        self.assertEqual(n.summary()["recall_outcomes"], {"fallback_waking": 1, "vault": 1})

    def fast_retry(self):
        patch = mock.patch.object(OneironNotes, "PROBE_BACKOFF_S", (0.01,))
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_down_vault_answers_locally_at_once_and_comes_back(self):
        self.fast_retry()
        n = self.notes(wait=False, down=True)
        for _ in range(200):  # the first wake fails: down
            if n.vault_state == "down":
                break
            time.sleep(0.01)
        n.local.remember("lab mate", "Jonas Weber", day=1, turn=1)
        t0 = time.time()
        self.assertEqual([x["value"] for x in n.recall("lab mate")], ["Jonas Weber"])
        self.assertLess(time.time() - t0, 0.5)  # no wait on a down vault
        self.assertEqual((n.recalls[-1]["outcome"], n.vault.asked), ("fallback_down", []))
        n.vault.down = False  # bel back: the next wake answers
        self.assertTrue(n._up.wait(5))
        n.recall("lab mate")
        self.assertEqual(n.recalls[-1]["outcome"], "vault")
        self.assertEqual(n.vault.asked[-1]["timeout"], OneironNotes.RECALL_TIMEOUT_S)
        self.assertEqual(n.wakes[-1]["outcome"], "awake")
        self.assertIn("error", {w["outcome"] for w in n.wakes})

    def test_a_failed_recall_marks_the_vault_down_until_a_wake_answers(self):
        n = self.notes(fail=TimeoutError("timed out"))
        n.PROBE_BACKOFF_S = (60,)
        n.vault.wake = lambda: (_ for _ in ()).throw(TimeoutError("timed out"))
        n.recall("lab mate")
        self.assertEqual(n.recalls[-1]["outcome"], "fallback_error")
        for _ in range(200):
            if n.wakes and n.wakes[-1]["outcome"] == "error":
                break
            time.sleep(0.01)
        n.recall("lab mate")
        self.assertEqual((n.recalls[-1]["outcome"], len(n.vault.asked)), ("fallback_down", 1))  # not asked again

    def test_a_recall_after_a_quiet_spell_wakes_the_vault_and_does_not_wait(self):
        n = self.notes()
        n.vault.waking = threading.Event()  # this wake is a cold start
        n._last_ok = time.time() - OneironNotes.IDLE_STOP_S - 1
        n.recall("lab mate")
        self.assertEqual((n.recalls[-1]["outcome"], len(n.vault.asked)), ("fallback_waking", 0))
        n.vault.waking.set()
        self.assertTrue(n._up.wait(5))
        self.assertEqual(n.vault.woken, 2)

    def test_witness_runs_on_a_down_vault_are_logged_unsent_for_push_day(self):
        log = Path(tempfile.mkdtemp()) / oneiron_vault.LIVE_WITNESS_LOG
        local = LocalJsonNotes(log.parent / "notes.json")
        n = OneironNotes(local, "w", 2, vault=StandInVault(down=True), witness_log=log)
        n.PROBE_BACKOFF_S = (60,)
        n.witness_turn({"ts": "2026-10-28T09:00:00+09:00", "turn": 1, "content": "Hi."},
                       {"ts": "2026-10-28T09:00:05+09:00", "turn": 1, "content": "Hello.", "tool_calls": []})
        self.assertTrue(n.flush(5))
        lines = [json.loads(x) for x in log.read_text().splitlines()]
        self.assertTrue(all(x.get("error") for x in lines))
        self.assertEqual(oneiron_vault.live_witness_sent(log), {})  # push_day resends both runs

    def test_flush_gives_up_after_its_timeout(self):
        n = self.notes()
        hold = threading.Event()
        n.vault.witness_run = lambda *a, **kw: hold.wait(5) and {}
        n.witness_turn({"ts": "2026-10-28T09:00:00+09:00", "turn": 1, "content": "Hi."},
                       {"ts": "2026-10-28T09:00:05+09:00", "turn": 1, "content": "Hello.", "tool_calls": []})
        self.assertFalse(n.flush(0.1))
        hold.set()
        self.assertTrue(n.flush(5))

    def test_a_run_committed_before_a_timeout_counts_as_sent(self):
        n = self.notes()

        def committed(*a, **kw):
            raise oneiron_vault.VaultError(400, '{"error":{"message":"the witnessed message already belongs to '
                                                'another turn"}}')

        n.vault.witness_run = committed
        r = n.remember("desk", "D-14", day=2, turn=1)
        n.witness_turn({"ts": "2026-10-28T09:00:00+09:00", "turn": 1, "content": "My desk is D-14."},
                       {"ts": "2026-10-28T09:00:05+09:00", "turn": 1, "content": "Saved.", "notes_written": [r["note"]["id"]],
                        "tool_calls": [{"name": "remember", "result": {"saved": r["note"]["id"], "key": "desk",
                                                                       "value": "D-14"}}]})
        n.flush()
        self.assertEqual((n.errors, n.unacknowledged), ([], set()))

    def test_the_vault_answers_the_recall_tool(self):
        n = self.notes()
        conv = conversation_ref("w", 1, 1)  # the stand-in is a single-credential vault: version 1
        n.vault.extra = [item(conv, "Note saved: lab mate: Jonas Weber")]
        n.local.remember("lab mate", "Jonas Weber", day=1, turn=1)
        n.local.remember("standup time", "10:30", day=1, turn=2)
        out, _ = ToolBox(n, None, day=2).run("recall", {"query": "lab mate"})
        self.assertEqual(out["notes"], [{"key": "lab mate", "value": "Jonas Weber", "saved": ""}])
        call = n.recalls[-1]
        self.assertEqual((call["outcome"], call["kept"], call["items"]), ("vault", 1, 1))
        self.assertIsInstance(call["ms"], int)
        self.assertEqual(call["local_values"], ["Jonas Weber"])
        self.assertEqual(n.vault.asked[0]["limit"], 20)
        self.assertEqual(n.errors, [])

    def test_a_partial_pack_answers_from_the_local_notes_and_is_logged(self):
        n = self.notes(partial=True)
        n.vault.extra = [item(conversation_ref("w", 1, 1), "Note saved: lab mate: Somebody Else")]
        n.local.remember("lab mate", "Jonas Weber", day=1, turn=1)
        out, _ = ToolBox(n, None, day=2).run("recall", {"query": "lab mate"})
        self.assertEqual([x["value"] for x in out["notes"]], ["Jonas Weber"])  # the full local list, not the cut pack
        self.assertNotIn("vault", out)
        self.assertEqual(n.recalls[-1]["outcome"], "fallback_partial")
        self.assertIn("ms", n.recalls[-1])
        self.assertIn("partial", n.errors[0])

    def test_a_vault_error_answers_from_the_local_notes_and_is_logged(self):
        n = self.notes(fail=oneiron_vault.VaultError(503, "cold start"))
        n.local.remember("lab mate", "Jonas Weber", day=1, turn=1)
        out, _ = ToolBox(n, None, day=2).run("recall", {"query": "lab mate"})
        self.assertEqual([x["value"] for x in out["notes"]], ["Jonas Weber"])
        call = n.recalls[-1]
        self.assertEqual(call["outcome"], "fallback_error")
        self.assertIn("HTTP 503", call["error"])
        self.assertEqual(call["values"], ["Jonas Weber"])
        self.assertIn("HTTP 503", n.errors[0])

    def test_items_of_other_worlds_claims_and_stale_values_are_dropped(self):
        n = self.notes()
        mine = conversation_ref("w", 1, 1)
        n.local.remember("venue", "Room 312", day=1, turn=1)
        n.local.remember("venue", "Hall C", day=1, turn=2)  # the same key: Room 312 is superseded
        n.vault.extra = [item(conversation_ref("other-world", 1, 2), "Note saved: venue: Their Room"),
                         item(None, "venue is Hall B", kind="CLAIM"),
                         item(mine, "Note saved: venue: Room 312"),
                         item(mine, "Note saved: venue: Hall C (replaces Room 312)"),
                         item(mine, "The talk moved to Hall C.")]
        out = n.recall("venue")
        self.assertEqual([(x["key"], x["value"]) for x in out],
                         [("venue", "Hall C"), ("from the conversation", "The talk moved to Hall C.")])
        self.assertEqual(n.recalls[-1]["dropped"], {"other_world": 1, "no_conversation": 1, "superseded": 1})

    def test_a_note_the_vault_has_not_acknowledged_comes_from_the_local_copy(self):
        n = self.notes()
        n._queue.put = lambda job: None  # the worker never sends: the turn is still on its way to the vault
        n.remember("parking spot", "P4-17", day=2, turn=1)
        self.assertEqual([x["value"] for x in n.recall("parking spot")], ["P4-17"])
        self.assertEqual(n.recalls[-1]["unacknowledged"], 1)


class Turns:
    """GlimmerClient stand-in: each turn's replies in order; a reply is a list of tool calls or the answer text."""
    endpoint = "http://127.0.0.1:9"
    adapter_path = None

    def __init__(self, *replies):
        self.replies = list(replies)

    def adapters(self):
        return []

    def stream_chat(self, messages, tools, **kw):
        r = self.replies.pop(0)
        msg = ({"role": "assistant", "content": r} if isinstance(r, str) else
               {"role": "assistant", "content": "", "tool_calls": [
                   {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
                   for i, (name, args) in enumerate(r)]})
        yield {"type": "done", "message": msg, "finish_reason": "stop", "ttft_s": 0.1, "total_s": 0.2, "timings": {}}


class VaultDaySessionTest(unittest.TestCase):
    def setUp(self):
        os.environ["AFTERGLOW_RUNS"] = tempfile.mkdtemp()
        self.addCleanup(os.environ.pop, "AFTERGLOW_RUNS", None)

    def session(self, client, backend="vault", vault=None, tag=None, wait=True):
        with mock.patch.object(oneiron_vault, "OneironVault", lambda: vault):  # the stand-in, not the credentialed client
            s = DaySession("w-test", 1, client, date="2026-10-27", notes_backend=backend, vault_tag=tag,
                           decide_supersede=False, decide_route=False, web_search=False)
        if backend == "vault" and wait:
            s.notes._up.wait(5)
        return s

    def daylog(self, s):
        return [json.loads(x) for x in s.daylog_path.read_text().splitlines()]

    def test_write_through_then_read_from_the_vault_with_timing_in_the_day_log(self):
        vault = StandInVault()
        s = self.session(Turns([("remember", {"key": "lab mate", "value": "Jonas Weber"})], "Saved.",
                               [("recall", {"query": "lab mate"})], "Jonas Weber."), vault=vault)
        s.ask("My lab mate is Jonas Weber.")
        s.notes.flush()
        # the local file has the note at once; the vault got the turn in push_day's shape, with push_day's ids
        local = json.loads((s.dir / "notes.json").read_text())["notes"]
        self.assertEqual([(n["key"], n["value"]) for n in local], [("lab mate", "Jonas Weber")])
        conv = conversation_ref("w-test", 1, 1)
        self.assertEqual([(r["author"], r["texts"]) for r in vault.runs],
                         [("user", ["My lab mate is Jonas Weber."]),
                          ("companion", ["Note saved: lab mate: Jonas Weber", "Saved."])])
        self.assertEqual(vault.runs[1]["ids"], oneiron_vault.run_message_ids(conv, 1, 1, 2))
        self.assertEqual(s.notes.unacknowledged, set())
        sent = [json.loads(x) for x in (s.dir / oneiron_vault.LIVE_WITNESS_LOG).read_text().splitlines()]
        self.assertEqual([(x["turn"], x["run"], x["receipt_ref"]) for x in sent], [(1, 0, "r1"), (1, 1, "r2")])
        self.assertTrue(all(isinstance(x["ms"], int) for x in sent))

        s.ask("Who is my lab mate?")
        s.end_day()
        recs = [r for r in self.daylog(s) if r["role"] == "assistant"]
        self.assertEqual(recs[0]["witnessed_live"], conv)
        self.assertEqual(recs[0]["vault_recalls"], [])
        self.assertEqual((recs[0]["vault_wakes"][0]["outcome"], recs[0]["vault_state"]), ("awake", "up"))
        self.assertNotIn("vault_wakes", recs[1])  # each wake is logged once, on the turn it ended by
        call = recs[1]["vault_recalls"][0]
        # the vault's answer: the user's own words and the stored note, both from the day-1 turn
        self.assertEqual((call["query"], call["outcome"], call["values"]),
                         ("lab mate", "vault", ["My lab mate is Jonas Weber.", "Jonas Weber"]))
        self.assertIsInstance(call["ms"], int)
        self.assertEqual(recs[1]["notes_errors"], {"total": 0, "new": []})
        tool = next(tc for tc in recs[1]["tool_calls"] if tc["name"] == "recall")
        self.assertEqual([(x["key"], x["value"]) for x in tool["result"]["notes"]],
                         [("from the conversation", "My lab mate is Jonas Weber."), ("lab mate", "Jonas Weber")])
        summary = s.notes.summary()
        self.assertEqual((summary["recalls"], summary["recall_outcomes"], summary["recall_ms"]["n"]), (1, {"vault": 1}, 1))
        self.assertEqual((summary["witness_runs"], summary["witness_errors"], summary["errors"]), (4, 0, 0))
        self.assertEqual(([w["outcome"] for w in summary["wakes"]], summary["calls_over_20s"]), (["awake"], 0))
        # a later push_day sends nothing twice: every live run has a receipt
        pushed = StandInVault()
        oneiron_vault.push_day("w-test", 1, vault=pushed)
        self.assertEqual(pushed.runs, [])

    def test_a_failed_vault_counts_in_the_day_log_and_the_turn_still_answers(self):
        vault = StandInVault(fail=TimeoutError("timed out"))
        s = self.session(Turns([("recall", {"query": "standup"})], "I have no note of that."), vault=vault)
        s.notes.local.remember("standup time", "10:30", day=0, turn=1)
        ev = s.ask("When is standup?")
        self.assertEqual(ev["content"], "I have no note of that.")
        rec = [r for r in self.daylog(s) if r["role"] == "assistant"][0]
        self.assertEqual(rec["vault_recalls"][0]["outcome"], "fallback_error")
        self.assertEqual(rec["tool_calls"][0]["result"]["notes"][0]["value"], "10:30")  # the local notes answered
        self.assertEqual(rec["notes_errors"]["total"], 1)

    def test_a_restarted_day_serves_unsent_notes_from_the_local_copy(self):
        vault = StandInVault()
        s = self.session(Turns([("remember", {"key": "lab mate", "value": "Jonas Weber"})], "Saved.",
                               [("remember", {"key": "desk", "value": "D-14"})], "Saved."), vault=vault)
        s.ask("My lab mate is Jonas Weber.")
        s.notes.flush()
        s.notes._queue.put = lambda job: None  # the app stops before turn 2 reaches the vault
        s.ask("My desk is D-14.")
        again = self.session(Turns(), vault=vault)
        by_key = {n["key"]: n["id"] for n in again.notes.all()}
        self.assertEqual(again.notes.unacknowledged, {by_key["desk"]})

    def test_a_day_on_a_down_vault_answers_from_the_local_notes_without_waiting(self):
        vault = StandInVault(down=True)
        with mock.patch.object(OneironNotes, "PROBE_BACKOFF_S", (60,)):
            s = self.session(Turns([("remember", {"key": "desk", "value": "D-14"})], "Saved.",
                                   [("recall", {"query": "desk"})], "D-14."), vault=vault, wait=False)
            for _ in range(200):
                if s.notes.vault_state == "down":
                    break
                time.sleep(0.01)
            t0 = time.time()
            s.ask("My desk is D-14.")
            s.ask("Where is my desk?")
            s.end_day(flush_timeout=5)
            self.assertLess(time.time() - t0, 2)
        recs = [r for r in self.daylog(s) if r["role"] == "assistant"]
        self.assertEqual(recs[1]["content"], "D-14.")
        self.assertEqual(recs[1]["vault_recalls"][0]["outcome"], "fallback_down")
        self.assertEqual(recs[1]["tool_calls"][0]["result"]["notes"][0]["value"], "D-14")
        self.assertEqual(recs[1]["vault_state"], "down")
        self.assertGreater(recs[1]["notes_errors"]["total"], 0)
        self.assertEqual(oneiron_vault.live_witness_sent(s.dir / oneiron_vault.LIVE_WITNESS_LOG), {})

    def test_a_measured_rerun_witnesses_into_its_own_conversation(self):
        vault = StandInVault()
        s = self.session(Turns("Hello."), vault=vault, tag="measure-1")
        s.ask("Hi.")
        s.notes.flush()
        self.assertEqual({r["conv"] for r in vault.runs}, {conversation_ref("w-test", 1, 1, "measure-1")})
        with self.assertRaises(SystemExit):  # push_day never sends a measured rerun into the day's conversation
            oneiron_vault.push_day("w-test", 1, vault=StandInVault())

    def test_the_local_backend_is_unchanged(self):
        s = self.session(Turns("Hello."), backend="local")
        s.ask("Hi.")
        rec = [r for r in self.daylog(s) if r["role"] == "assistant"][0]
        self.assertFalse({"vault_recalls", "notes_errors", "witnessed_live"} & set(rec))
        self.assertIsInstance(s.notes, LocalJsonNotes)
        with self.assertRaises(ValueError):
            self.session(Turns(), backend="oneiron")


if __name__ == "__main__":
    unittest.main()
