"""Offline tests for fresh-vault configuration, replay receipts, and exact transcript read-back."""
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import replay_vault_days as replay  # noqa: E402
from afterglow import oneiron_vault  # noqa: E402
from afterglow.notes import conversation_ref  # noqa: E402


def rec(turn, role, content, second):
    return {"ts": f"2026-10-27T05:00:{second:02d}+09:00", "turn": turn, "role": role, "content": content}


class FakeVault:
    origin = "https://vault.test"

    def __init__(self, fail_at=None, page_size=None, suppressed=0):
        self.sent, self.turns, self.fail_at = [], [], fail_at
        self.page_size, self.suppressed = page_size, suppressed
        self.by_author = {"companion": self, "user": self}

    def client_for(self, author):
        return self

    def witness_run(self, conv, author, texts, at, ids=None):
        if len(self.sent) == self.fail_at:
            raise RuntimeError("transient 500")
        self.sent.append((author, texts, ids))
        turn_id = f"{len(self.sent):032x}"
        self.turns.append({"id": turn_id, "messages": [{"id": mid, "text": text} for mid, text in zip(ids, texts)]})
        return {"receipt_ref": f"witness:{turn_id}", "turn_short_id": f"t{len(self.sent)}"}

    def transcript(self, conversation, limit=200, after=None):
        start = int(after) if after else 0
        end = min(start + self.page_size, len(self.turns)) if self.page_size else len(self.turns)
        next_ = str(end) if end < len(self.turns) else None
        return {"conversation": conversation, "turns": self.turns[start:end], "next": next_,
                "narrowing": {"suppressed_count": self.suppressed}}


class FakeSpeakerVault(FakeVault, oneiron_vault.SpeakerVaults):
    pass


class ReplayCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.day = self.source / "w2-solo-developer/1"
        self.day.mkdir(parents=True)
        self.records = [{"ts": "2026-10-27T05:00:00+09:00", "turn": 0, "role": "night_links"},
                        rec(1, "user", "hi", 0), rec(1, "assistant", "hello", 5),
                        rec(2, "user", "again", 10), rec(2, "assistant", "answer", 15)]
        (self.day / "daylog.jsonl").write_text("".join(json.dumps(r) + "\n" for r in self.records))

    def pushed(self, vault):
        output = self.root / "verify"
        day_dir = output / "runs/w2-solo-developer/1"
        day_dir.mkdir(parents=True)
        (day_dir / "daylog.jsonl").write_text((self.day / "daylog.jsonl").read_text())
        with mock.patch.dict(os.environ, {"AFTERGLOW_RUNS": str(output / "runs")}):
            summary = oneiron_vault.push_day("w2-solo-developer", 1, vault=vault)
        return day_dir, summary

    def test_origin_precedence_keeps_credential_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(oneiron_vault.vault_origin("https://old.test/"), "https://old.test")
            self.assertEqual(oneiron_vault.vault_origin("https://old.test", "https://explicit.test/"),
                             "https://explicit.test")
            with self.assertRaisesRegex(RuntimeError, "AFTERGLOW_ONEIRON_ORIGIN"):
                oneiron_vault.vault_origin()
        with mock.patch.dict(os.environ, {"AFTERGLOW_ONEIRON_ORIGIN": "https://fresh.test/"}):
            self.assertEqual(oneiron_vault.vault_origin("https://old.test"), "https://fresh.test")
            self.assertEqual(oneiron_vault.vault_origin("https://old.test", "https://explicit.test"),
                             "https://explicit.test")

    def test_speaker_credentials_must_share_one_vault(self):
        clients = [SimpleNamespace(origin="https://person.test"), SimpleNamespace(origin="https://agent.test")]
        with mock.patch.object(oneiron_vault, "OneironVault", side_effect=clients):
            with self.assertRaisesRegex(RuntimeError, "different vault origins"):
                oneiron_vault.SpeakerVaults("person", "agent")

    def test_transcript_is_an_authenticated_paged_get(self):
        vault = oneiron_vault.OneironVault.__new__(oneiron_vault.OneironVault)
        vault.origin, vault.bearer, vault.access = "https://vault.test", "Bearer test", {}
        vault.holder_proof = lambda: "proof"
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"turns":[],"next":null}')
        with mock.patch("urllib.request.urlopen", return_value=response) as opened:
            self.assertEqual(vault.transcript("c" * 32, after="1:" + "a" * 32)["turns"], [])
        request = opened.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        parsed = urllib.parse.urlparse(request.full_url)
        self.assertEqual(parsed.path, "/v1/core/conversations/" + "c" * 32 + "/transcript")
        self.assertEqual(urllib.parse.parse_qs(parsed.query), {"limit": ["200"], "after": ["1:" + "a" * 32]})
        self.assertEqual(request.headers["X-oneiron-binding"], "proof")

    def test_transcript_uses_the_witness_clock_retry(self):
        vault = oneiron_vault.OneironVault.__new__(oneiron_vault.OneironVault)
        vault.probes, vault.skew = 0, 0
        with mock.patch.object(vault, "_get", side_effect=[oneiron_vault.VaultError(401, "clock"), {"turns": []}]) as get:
            self.assertEqual(vault.transcript("c" * 32)["turns"], [])
        self.assertEqual(get.call_count, 2)
        self.assertEqual(vault.skew, -60)

    def test_push_day_logs_turn_time_and_failure(self):
        events = []
        with mock.patch.dict(os.environ, {"AFTERGLOW_RUNS": str(self.source)}):
            with self.assertRaisesRegex(RuntimeError, "transient 500"):
                oneiron_vault.push_day("w2-solo-developer", 1, vault=FakeVault(fail_at=2), on_turn=events.append)
        self.assertEqual(events[0]["outcome"], "witnessed")
        self.assertEqual(events[1]["outcome"], "error")
        self.assertEqual((events[1]["turn"], events[1]["run"], events[1]["author"]), (2, 0, "user"))
        self.assertIsInstance(events[0]["ms"], int)
        self.assertIsInstance(events[1]["ms"], int)
        self.assertIn("transient 500", events[1]["error"])

    def test_replay_isolates_receipts_pages_and_resumes(self):
        old_receipt = self.day / "oneiron.json"
        old_receipt.write_text('{"vault":"https://old.test"}\n')
        output = self.root / "replay"
        report = replay.replay_days(["w2-solo-developer"], [1], self.source, output,
                                    vault_factory=lambda world, day: FakeSpeakerVault(page_size=1), verify=True)
        self.assertEqual(report["days_complete"], 1)
        self.assertEqual(report["days_verified"], 1)
        self.assertEqual(report["write_ms"]["n"], 2)
        self.assertEqual(report["write_ms_by_speakers"]["person+agent"]["n"], 2)
        self.assertEqual(report["write_ms_by_speakers"]["agent"]["n"], 0)
        self.assertEqual(report["unresolved_failed_turns"], 0)
        self.assertEqual(report["readbacks"][0]["outcome"], "ok")
        self.assertEqual(report["readbacks"][0]["expected_turns"], 4)
        self.assertEqual(report["readbacks"][0]["expected_messages"], 4)
        self.assertEqual(report["readbacks"][0]["lanes"]["companion"]["pages"], 4)
        self.assertEqual(old_receipt.read_text(), '{"vault":"https://old.test"}\n')
        receipt = json.loads((output / "runs/w2-solo-developer/1/oneiron.json").read_text())
        self.assertEqual(receipt["vault"], "https://vault.test")
        self.assertEqual(receipt["speakers"], "person+agent")
        again = FakeSpeakerVault()
        replay.replay_days(["w2-solo-developer"], [1], self.source, output, vault_factory=lambda world, day: again)
        self.assertEqual(again.sent, [])
        resumed = replay.write_report(output)
        self.assertEqual(resumed["write_ms"]["n"], 2)
        self.assertEqual(resumed["days_verified"], 1)

    def test_agent_only_mode_is_day1_v1_without_a_person_template(self):
        output = self.root / "agent-only"
        report = replay.replay_days(["w2-solo-developer"], [1], self.source, output, verify=True, agent_only=True,
                                    vault_factory=lambda world, day: FakeVault())
        self.assertEqual(report["days_verified"], 1)
        self.assertEqual(report["write_ms_by_speakers"]["agent"]["n"], 2)
        self.assertEqual(report["write_ms_by_speakers"]["person+agent"]["n"], 0)
        receipt = json.loads((output / "runs/w2-solo-developer/1/oneiron.json").read_text())
        self.assertEqual(receipt["speakers"], "agent")
        self.assertEqual(receipt["conversation_ref"], conversation_ref("w2-solo-developer", 1, 1))
        with self.assertRaisesRegex(ValueError, "only for pre-enrolment day-1"):
            replay.replay_days(["w2-solo-developer"], [2], self.source, self.root / "bad-agent", agent_only=True,
                               vault_factory=lambda world, day: FakeVault())

    def test_from_turn_applies_to_one_day_and_persists(self):
        output = self.root / "replay"
        vault = FakeVault()
        replay.replay_days(["w2-solo-developer"], [1], self.source, output, from_turn=2,
                           vault_factory=lambda world, day: vault)
        self.assertEqual([texts for author, texts, ids in vault.sent], [["again"], ["answer"]])
        receipt = json.loads((output / "runs/w2-solo-developer/1/oneiron.json").read_text())
        self.assertEqual(receipt["skipped_before"], 2)
        with self.assertRaisesRegex(ValueError, "one --world and one --day"):
            replay.replay_days(["w2-solo-developer"], [1, 2], self.source, self.root / "other", from_turn=2,
                               vault_factory=lambda world, day: FakeVault())

    def test_output_may_not_contain_the_source_runs(self):
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            replay.replay_days(["w2-solo-developer"], [1], self.source, self.root,
                               vault_factory=lambda world, day: FakeVault())

    def test_readback_resolves_already_witnessed_and_checks_turn_mapping(self):
        vault = FakeVault()
        day_dir, summary = self.pushed(vault)
        receipt = summary["receipts"][0]["receipt_refs"][0]
        summary["receipts"][0]["receipt_refs"][0] = "already-witnessed"
        self.assertEqual(replay.verify_day(day_dir, summary, vault)["outcome"], "ok")
        summary["receipts"][0]["receipt_refs"][0] = receipt
        vault.turns[0]["messages"], vault.turns[1]["messages"] = vault.turns[1]["messages"], vault.turns[0]["messages"]
        result = replay.verify_day(day_dir, summary, vault)
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(len(result["run_mismatches"]), 2)

    def test_readback_rejects_changed_text(self):
        vault = FakeVault()
        day_dir, summary = self.pushed(vault)
        vault.turns[0]["messages"][0]["text"] = "changed"
        result = replay.verify_day(day_dir, summary, vault)
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(len(result["text_mismatch_ids"]), 1)
        self.assertIn("text_mismatch_ids=1", result["error"])

    def test_readback_rejects_schema_drift(self):
        vault = FakeVault()
        day_dir, summary = self.pushed(vault)
        del vault.turns[0]["messages"][0]["text"]
        with self.assertRaisesRegex(RuntimeError, "invalid message"):
            replay.verify_day(day_dir, summary, vault)

    def test_readback_rejects_suppressed_lanes(self):
        vault = FakeSpeakerVault(suppressed=1)
        day_dir, summary = self.pushed(vault)
        result = replay.verify_day(day_dir, summary, vault)
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["incomplete_lanes"], ["companion", "user"])


if __name__ == "__main__":
    unittest.main()
