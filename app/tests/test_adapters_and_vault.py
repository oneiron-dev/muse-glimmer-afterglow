"""Adapter matching (several worlds' adapters on one server) and push_day's resume rules, offline."""
import json

import pytest

from afterglow import oneiron_vault, snapshot
from afterglow.llm import GlimmerClient, GlimmerError

LOADED = [{"id": 0, "path": "/data/runs/w1/1/night/adapter.gguf", "scale": 1.0},
          {"id": 1, "path": "/data/runs/w2/1/night/adapter.gguf", "scale": 1.0}]


def client(adapter_path, loaded=LOADED):
    c = GlimmerClient("http://127.0.0.1:9", adapter_path=adapter_path)
    c.get = lambda path, timeout=10: loaded
    return c


def test_world_adapter_matches_full_path_not_basename():
    c = client("/data/runs/w2/1/night/adapter.gguf")
    assert c.world_adapter()["id"] == 1
    assert c.lora_field() == [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 1.0}]
    assert c.lora_field(0.0) == [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 0.0}]


def test_adapter_missing_from_a_loaded_server_raises():
    with pytest.raises(GlimmerError):
        client("/data/runs/w3/1/night/adapter.gguf").lora_field()
    assert client("/data/runs/w3/1/night/adapter.gguf", loaded=[]).lora_field() is None  # server runs base weights
    assert client(None).lora_field() == [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 0.0}]


def test_failed_adapter_list_raises_and_is_not_cached():
    c = GlimmerClient("http://127.0.0.1:9")

    def down(path, timeout=10):
        raise OSError("connection refused")

    c.get = down
    with pytest.raises(GlimmerError):
        c.adapters()
    c.get = lambda path, timeout=10: LOADED
    assert c.adapters() == LOADED


def test_identity_compare_counts_new_fields():
    assert snapshot.compare({"model_sha256": "a"}, {"model_sha256": "a", "ctx": 65536}) == ["ctx"]
    assert snapshot.compare({"server_started": "x"}, {"server_started": "y"}) == []


class FakeVault:
    origin = "https://vault.test"

    def __init__(self, fail_at=None):
        self.sent, self.fail_at = [], fail_at

    def client_for(self, author):
        return self

    def witness_run(self, conv, author, texts, at):
        if len(self.sent) == self.fail_at:
            raise RuntimeError("transient 500")
        self.sent.append((author, texts, at))
        return {"receipt_ref": f"r{len(self.sent)}", "turn_short_id": "t"}


def day(tmp_path, monkeypatch, records):
    monkeypatch.setenv("AFTERGLOW_RUNS", str(tmp_path))
    d = tmp_path / "wt" / "1"
    d.mkdir(parents=True)
    (d / "daylog.jsonl").write_text("\n".join(json.dumps(r) for r in records))
    return d


def rec(turn, role, content, second, **kw):
    return {"ts": f"2026-10-27T05:00:{second:02d}+09:00", "turn": turn, "role": role, "content": content, **kw}


def test_push_day_resumes_at_the_first_unsent_run(tmp_path, monkeypatch):
    day(tmp_path, monkeypatch, [rec(1, "user", "hi", 0), rec(1, "assistant", "hello", 5),
                                rec(2, "user", "x", 10), rec(2, "assistant", "y", 15)])
    first = FakeVault(fail_at=1)
    with pytest.raises(RuntimeError):
        oneiron_vault.push_day("wt", 1, vault=first)
    again = FakeVault()
    oneiron_vault.push_day("wt", 1, vault=again)
    assert [a for a, _, _ in first.sent + again.sent] == ["user", "companion", "user", "companion"]
    assert again.sent[0][2] - first.sent[0][2] == 5  # the user run keeps its own time, the answer its own
    third = FakeVault()
    assert oneiron_vault.push_day("wt", 1, vault=third)["turns"] == 2 and third.sent == []


def test_push_day_skips_live_turns_repeated_notes_and_orphans(tmp_path, monkeypatch):
    from afterglow.notes import conversation_ref

    saved = {"name": "remember", "result": {"saved": "n1", "key": "office", "value": "Lisbon"}}
    again = {"name": "remember", "result": {"saved": "n1", "key": "office", "value": "Lisbon", "note": "already saved"}}
    day(tmp_path, monkeypatch, [
        rec(1, "user", "office is Lisbon", 0), rec(1, "assistant", "noted", 1, tool_calls=[saved], notes_written=["n1"]),
        rec(2, "user", "lost turn", 2),
        rec(3, "user", "again, Lisbon", 3), rec(3, "assistant", "still noted", 4, tool_calls=[again], notes_written=[]),
        rec(4, "user", "live", 5), rec(4, "assistant", "sent live", 6, witnessed_live=conversation_ref("wt", 1, 1))])
    v = FakeVault()
    oneiron_vault.push_day("wt", 1, vault=v)
    texts = [t for _, ts, _ in v.sent for t in ts]
    assert texts == ["office is Lisbon", "Note saved: office: Lisbon", "noted", "again, Lisbon", "still noted"]
