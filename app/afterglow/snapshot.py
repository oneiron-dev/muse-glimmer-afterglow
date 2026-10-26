"""KV snapshots of the user's slot (slot 0): save at end of day, restore at startup, re-warm after a night update.

llama-server does not check what a slot file was made with: it carries no adapter state, and a
restore under other weights, another adapter or another template is silently accepted. So every
snapshot has an identity stored beside it, and restore() refuses any mismatch:

  model_sha256, drafter_sha256       from the server's /data/afterglow/serve/current.json on the serving box
  adapter_sha256, adapter_scale      the loaded adapter and the per-request scale the client sends
  template_sha256, template_kwargs   the chat template from /props and the reasoning strength
  prefix_sha256, prefix_messages     the messages and tools the slot was filled with

Kinds: "start" = the day's persona context (system prompt with notes and tools), made by rewarm();
       "end"   = the whole day's conversation, saved by save_end_of_day().
Metadata: runs/<world>/<day>/kv/<kind>.json. Slot files: /data/afterglow/slots/<file> on the serving box.

Glimmer has sliding-window layers, so llama-server can reuse cached KV only up to its last context
checkpoint before the point where a new prompt differs. A slot saved mid-conversation ends in generated
tokens and holds many checkpoints (23 after four turns: a 2.25 GB file whose restore was not reused).
So every snapshot is made the same way: erase the slot, prefill exactly the rendered prefix (up to the
end token of the last message, before the next "<|start|>"), then save. The next request then extends
the cached tokens: after a restore, 2,248 of 2,253 tokens were reused, and the file was 341 MB.
"""
import datetime as dt
import hashlib
import json
import os
import subprocess
import time

from .llm import HOSTS, SERVE_HOST, GlimmerClient
from .tools import TOOL_SCHEMAS

JST = dt.timezone(dt.timedelta(hours=9))
SERVE_STATE = f"{HOSTS['root']}/serve/current.json"
_identity_cache = {}


def now_iso():
    return dt.datetime.now(JST).isoformat(timespec="seconds")


def sha256_json(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def serve_state(max_age_s=30):
    """The running server's current.json (weights and adapter hashes), read over ssh and cached briefly.
    AFTERGLOW_SERVE_STATE=<local file> reads a local copy instead (the mock server writes one)."""
    hit = _identity_cache.get("state")
    if hit and time.time() - hit[0] < max_age_s:
        return hit[1]
    if os.environ.get("AFTERGLOW_SERVE_STATE"):
        with open(os.environ["AFTERGLOW_SERVE_STATE"]) as f:
            state = json.load(f)
        _identity_cache["state"] = (time.time(), state)
        return state
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=5", SERVE_HOST, f"cat {SERVE_STATE}"],
                       capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise RuntimeError(f"cannot read {SERVE_HOST}:{SERVE_STATE}: {r.stderr.strip()}")
    state = json.loads(r.stdout)
    _identity_cache["state"] = (time.time(), state)
    return state


def identity(client, reasoning):
    st = serve_state()
    props = client.get("/props")
    lora = client.lora_field()
    adapter = st.get("lora") or {}
    return {
        "model_sha256": st["model"]["sha256"],
        "drafter_sha256": st["drafter"]["sha256"],
        "adapter_sha256": adapter.get("sha256"),
        "adapter_scale": lora[0]["scale"] if lora else None,
        "template_sha256": hashlib.sha256((props.get("chat_template") or "").encode()).hexdigest(),
        "template_kwargs": {"reasoning_strength": reasoning},
        "server_started": st.get("started"),
    }


def compare(saved, current):
    """Fields that differ (server_started is informational: a restarted server with the same files is fine)."""
    return [k for k in saved if k != "server_started" and saved.get(k) != current.get(k)]


def slot_file(world, day, kind, ident):
    return f"afterglow-{world}-d{day}-{kind}-{sha256_json(ident)[:12]}.bin"


def meta_path(session, kind):
    d = session.dir / "kv"
    d.mkdir(exist_ok=True)
    return d / f"{kind}.json"


def _save(session, kind, messages, ident, prefill_timings=None):
    client = session.client
    fname = slot_file(session.world, session.day, kind, ident)
    t0 = time.time()
    r = client.slot_action("save", fname)
    meta = {
        "kind": kind, "world": session.world, "day": session.day, "slot": client.slot, "filename": fname,
        "saved_at": now_iso(), "n_tokens": r.get("n_saved"), "bytes": r.get("n_written"),
        "save_ms": (r.get("timings") or {}).get("save_ms"), "save_wall_s": round(time.time() - t0, 3),
        "identity": ident, "prefix_messages": len(messages),
        "prefix_sha256": sha256_json({"messages": messages, "tools": TOOL_SCHEMAS}),
        "prefill_timings": prefill_timings,
    }
    meta_path(session, kind).write_text(json.dumps(meta, indent=1))
    return meta


PREFIX_MARK = "afterglow-prefix-mark-7f3a"


def render_prefix(session, messages):
    """The prompt text of `messages` exactly as the chat template renders it ahead of the next message."""
    r = session.client.post("/apply-template", {
        "messages": messages + [{"role": "user", "content": PREFIX_MARK}], "tools": TOOL_SCHEMAS,
        "chat_template_kwargs": {"reasoning_strength": session.reasoning}})
    prompt = r["prompt"]
    return prompt[:prompt.rindex("<|start|>", 0, prompt.index(PREFIX_MARK))]


def prefill_exact(session, messages):
    """Erase slot 0 and fill it with exactly the rendered prefix of `messages` (no generated tokens)."""
    client = session.client
    client.slot_action("erase")
    body = {"prompt": render_prefix(session, messages), "n_predict": 0, "id_slot": client.slot,
            "cache_prompt": True}
    lora = client.lora_field()
    if lora:
        body["lora"] = lora
    return client.post("/completion", body).get("timings")


def save_end_of_day(session):
    """Snapshot the whole day's conversation (re-prefilled exactly, see the module note)."""
    ident = identity(session.client, session.reasoning)
    timings = prefill_exact(session, session.messages)
    return _save(session, "end", session.messages, ident, prefill_timings=timings)


def rewarm(session):
    """Prefill the day's persona context (system prompt with notes, and the tools) into slot 0 under the current
    weights and adapter, and save it as the day's "start" snapshot. Meta night calls this after a night update,
    once the server runs with the new adapter:
        from afterglow.agent import DaySession; from afterglow.snapshot import rewarm
        rewarm(DaySession(world, next_day))
    """
    ident = identity(session.client, session.reasoning)
    prefix = session.prefix_messages()
    timings = prefill_exact(session, prefix)
    return _save(session, "start", prefix, ident, prefill_timings=timings)


def check(session, kind):
    """Return (meta, reasons): reasons is [] when the snapshot fits the running server and this session."""
    p = meta_path(session, kind)
    if not p.exists():
        return None, ["no snapshot"]
    meta = json.loads(p.read_text())
    reasons = [f"{k} differs" for k in compare(meta["identity"], identity(session.client, session.reasoning))]
    n = meta["prefix_messages"]
    msgs = session.messages[:n]
    if len(msgs) < n or sha256_json({"messages": msgs, "tools": TOOL_SCHEMAS}) != meta["prefix_sha256"]:
        reasons.append("prefix differs from this session's messages")
    return meta, reasons


def restore(session, kinds=("end", "start"), probe=True):
    """Restore the newest snapshot that fits; refuse any that does not. With probe, time the first token of a
    one-token request on the restored context (the TTFT the user will see). Returns a report dict."""
    client = session.client
    report = {"tried": []}
    for kind in kinds:
        meta, reasons = check(session, kind)
        report["tried"].append({"kind": kind, "refused": reasons})
        if reasons:
            continue
        t0 = time.time()
        r = client.slot_action("restore", meta["filename"])
        report.update(kind=kind, filename=meta["filename"], n_tokens=r.get("n_restored"),
                      restore_ms=(r.get("timings") or {}).get("restore_ms"), restore_wall_s=round(time.time() - t0, 3),
                      cold_prefill_ms=(meta.get("prefill_timings") or {}).get("prompt_ms"))
        if probe:
            report["probe"] = ttft_probe(session)
        return report
    report["kind"] = None
    return report


def ttft_probe(session):
    """Time to first token on the current slot 0 contents: a one-token request with the session's messages and a
    throwaway user line. (The cache keeps the session's messages; the next real turn reuses them.)"""
    msgs = session.messages + [{"role": "user", "content": "(startup check: reply OK)"}]
    t0 = time.time()
    done = session.client.chat(msgs, TOOL_SCHEMAS, reasoning=session.reasoning, max_tokens=1, temperature=0)
    t = done.get("timings") or {}
    return {"ttft_s": round(time.time() - t0, 3), "cache_n": t.get("cache_n"), "prompt_n": t.get("prompt_n"),
            "prompt_ms": t.get("prompt_ms")}


def cold_probe(session):
    """The same probe after erasing slot 0: what startup costs with no snapshot."""
    session.client.slot_action("erase")
    return ttft_probe(session)


if __name__ == "__main__":
    import argparse
    from .agent import DaySession

    ap = argparse.ArgumentParser(description="KV snapshot tools for the user's slot")
    ap.add_argument("action", choices=["rewarm", "save-end", "restore", "check"])
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True)
    a = ap.parse_args()
    s = DaySession(a.world, a.day, GlimmerClient())
    if a.action == "rewarm":
        out = rewarm(s)
    elif a.action == "save-end":
        out = save_end_of_day(s)
    elif a.action == "restore":
        out = restore(s)
    else:
        out = {k: check(s, k)[1] for k in ("start", "end")}
    print(json.dumps(out, indent=1))
