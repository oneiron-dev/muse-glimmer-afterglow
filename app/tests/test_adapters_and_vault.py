"""Offline tests for adapter matching (several worlds' adapters on one server) and push_day's resume rules.

  python3 -m unittest discover -s app/tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import oneiron_vault, snapshot  # noqa: E402
from afterglow.llm import GlimmerClient, GlimmerError  # noqa: E402
from afterglow.notes import conversation_ref  # noqa: E402

LOADED = [{"id": 0, "path": "/data/runs/w1/1/night/adapter.gguf", "scale": 1.0},
          {"id": 1, "path": "/data/runs/w2/1/night/adapter.gguf", "scale": 1.0}]


def client(adapter_path, loaded=LOADED):
    c = GlimmerClient("http://127.0.0.1:9", adapter_path=adapter_path)
    c.get = lambda path, timeout=10: loaded
    return c


class AdapterTest(unittest.TestCase):
    def test_world_adapter_matches_full_path_not_basename(self):
        c = client("/data/runs/w2/1/night/adapter.gguf")
        self.assertEqual(c.world_adapter()["id"], 1)
        self.assertEqual(c.lora_field(), [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 1.0}])
        self.assertEqual(c.lora_field(0.0), [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 0.0}])

    def test_adapter_missing_from_a_loaded_server_raises(self):
        with self.assertRaises(GlimmerError):
            client("/data/runs/w3/1/night/adapter.gguf").lora_field()
        self.assertIsNone(client("/data/runs/w3/1/night/adapter.gguf", loaded=[]).lora_field())  # base weights
        self.assertEqual(client(None).lora_field(), [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 0.0}])

    def test_failed_adapter_list_raises_and_is_not_cached(self):
        c = GlimmerClient("http://127.0.0.1:9")

        def down(path, timeout=10):
            raise OSError("connection refused")

        c.get = down
        with self.assertRaises(GlimmerError):
            c.adapters()
        c.get = lambda path, timeout=10: LOADED
        self.assertEqual(c.adapters(), LOADED)

    def test_identity_compare_counts_new_fields(self):
        self.assertEqual(snapshot.compare({"model_sha256": "a"}, {"model_sha256": "a", "ctx": 65536}), ["ctx"])
        self.assertEqual(snapshot.compare({"server_started": "x"}, {"server_started": "y"}), [])


class FakeVault:
    origin = "https://vault.test"

    def __init__(self, fail_at=None):
        self.sent, self.fail_at = [], fail_at

    def client_for(self, author):
        return self

    def witness_run(self, conv, author, texts, at, ids=None):
        self.ids = getattr(self, "ids", []) + [ids]  # every attempt, the failed one too
        if len(self.sent) == self.fail_at:
            raise RuntimeError("transient 500")
        self.sent.append((author, texts, at))
        return {"receipt_ref": f"r{len(self.sent)}", "turn_short_id": "t"}


def rec(turn, role, content, second, **kw):
    return {"ts": f"2026-10-27T05:00:{second:02d}+09:00", "turn": turn, "role": role, "content": content, **kw}


class PushDayTest(unittest.TestCase):
    def day(self, records):
        tmp = tempfile.mkdtemp()
        env = mock.patch.dict(os.environ, {"AFTERGLOW_RUNS": tmp})
        env.start()
        self.addCleanup(env.stop)
        d = Path(tmp) / "wt" / "1"
        d.mkdir(parents=True)
        (d / "daylog.jsonl").write_text("\n".join(json.dumps(r) for r in records))

    def test_resumes_at_the_first_unsent_run(self):
        self.day([rec(1, "user", "hi", 0), rec(1, "assistant", "hello", 5),
                  rec(2, "user", "x", 10), rec(2, "assistant", "y", 15)])
        first = FakeVault(fail_at=1)
        with self.assertRaises(RuntimeError):
            oneiron_vault.push_day("wt", 1, vault=first)
        again = FakeVault()
        oneiron_vault.push_day("wt", 1, vault=again)
        self.assertEqual([a for a, _, _ in first.sent + again.sent], ["user", "companion", "user", "companion"])
        self.assertEqual(again.sent[0][2] - first.sent[0][2], 5)  # the user run keeps its own time
        third = FakeVault()
        self.assertEqual(oneiron_vault.push_day("wt", 1, vault=third)["turns"], 2)
        self.assertEqual(third.sent, [])
        # the run that failed is resent with the ids the first try gave it; other runs get other ids
        self.assertEqual(again.ids[0], first.ids[1])
        self.assertEqual(len(again.ids[0][0]), 32)
        self.assertNotEqual(again.ids[0], again.ids[1])

    def test_a_run_committed_before_its_receipt_is_counted_as_witnessed(self):
        self.day([rec(1, "user", "hi", 0), rec(1, "assistant", "hello", 5)])

        class Committed(FakeVault):  # the vault holds run 0's ids already (its receipt was lost in a 503)
            def witness_run(self, conv, author, texts, at, ids=None):
                if author == "user":
                    raise oneiron_vault.VaultError(400, '{"error":{"code":"BAD_REQUEST","message":"the witnessed '
                                                        'message already belongs to another turn"}}')
                return super().witness_run(conv, author, texts, at, ids)

        v = Committed()
        with mock.patch("sys.stderr"):
            out = oneiron_vault.push_day("wt", 1, vault=v)
        self.assertEqual([a for a, _, _ in v.sent], ["companion"])
        self.assertEqual(out["receipts"][0]["receipt_refs"][0], "already-witnessed")
        bad = FakeVault()
        bad.witness_run = lambda *a, **k: (_ for _ in ()).throw(oneiron_vault.VaultError(400, "other bad request"))
        self.day([rec(1, "user", "hi", 0), rec(1, "assistant", "hello", 5)])
        with self.assertRaises(oneiron_vault.VaultError):
            oneiron_vault.push_day("wt", 1, vault=bad)

    def test_a_timed_out_witness_is_resent_with_the_same_ids(self):
        v = oneiron_vault.OneironVault.__new__(oneiron_vault.OneironVault)
        bodies = []

        def facade(verb, body, timeout=None):
            bodies.append(json.dumps(body, sort_keys=True))
            if len(bodies) == 1:
                raise TimeoutError("The read operation timed out")
            return {"receipt_ref": "r1"}

        v.facade = facade
        with mock.patch("sys.stderr"):
            self.assertEqual(v.witness_run("c" * 32, "companion", ["a", "b"], 0, ids=["1" * 32, "2" * 32]),
                             {"receipt_ref": "r1"})
        self.assertEqual(len(bodies), 2)
        self.assertEqual(bodies[0], bodies[1])
        self.assertIn('"id": "' + "1" * 32, bodies[0])

    def test_skips_live_turns_repeated_notes_and_orphans(self):
        saved = {"name": "remember", "result": {"saved": "n1", "key": "office", "value": "Lisbon"}}
        again = {"name": "remember",
                 "result": {"saved": "n1", "key": "office", "value": "Lisbon", "note": "already saved"}}
        self.day([
            rec(1, "user", "office is Lisbon", 0),
            rec(1, "assistant", "noted", 1, tool_calls=[saved], notes_written=["n1"]),
            rec(2, "user", "lost turn", 2),
            rec(3, "user", "again, Lisbon", 3),
            rec(3, "assistant", "still noted", 4, tool_calls=[again], notes_written=[]),
            rec(4, "user", "live", 5),
            rec(4, "assistant", "sent live", 6, witnessed_live=conversation_ref("wt", 1, 1))])
        self.live_log([{"turn": 4, "run": 0, "receipt_ref": "r"}, {"turn": 4, "run": 1, "receipt_ref": "r"}])
        v = FakeVault()
        oneiron_vault.push_day("wt", 1, vault=v)
        texts = [t for _, ts, _ in v.sent for t in ts]
        self.assertEqual(texts, ["office is Lisbon", "Note saved: office: Lisbon", "noted", "again, Lisbon",
                                 "still noted"])

    def live_log(self, lines):
        path = Path(os.environ["AFTERGLOW_RUNS"]) / "wt" / "1" / oneiron_vault.LIVE_WITNESS_LOG
        path.write_text("".join(json.dumps(x) + "\n" for x in lines))

    def test_a_live_turn_with_a_failed_or_unsent_run_is_sent_with_the_live_ids(self):
        conv = conversation_ref("wt", 1, 1)
        self.day([rec(1, "user", "live one", 0), rec(1, "assistant", "a", 1, witnessed_live=conv),
                  rec(2, "user", "live two", 2), rec(2, "assistant", "b", 3, witnessed_live=conv)])
        # turn 1: the companion run failed after its user run went; turn 2: queued, then the app stopped
        self.live_log([{"turn": 1, "run": 0, "receipt_ref": "r"}, {"turn": 1, "run": 1, "error": "TimeoutError"}])
        v = FakeVault()
        with mock.patch("sys.stderr"):
            oneiron_vault.push_day("wt", 1, vault=v)
        self.assertEqual([t for _, ts, _ in v.sent for t in ts], ["live one", "a", "live two", "b"])
        self.assertEqual(v.ids[1], oneiron_vault.run_message_ids(conv, 1, 1, 1))  # the ids the live path used


if __name__ == "__main__":
    unittest.main()
