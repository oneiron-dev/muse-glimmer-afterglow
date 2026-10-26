"""KV snapshot of the world's stable prefix in the user's slot (slot 0): restore at startup, re-warm after a
night update.

The snapshot holds only the prefix that is the same for every day of a world: the system prompt (persona
header and rules) and the tools, about 1K tokens and ~143 MB (each token costs ~142 KB of KV). The day
context (date, notes) and the day so far are prefilled after the restore. There is no end-of-day snapshot:
files would grow with the conversation (363 MB for a 2.5K-token day).

llama-server does not check what a slot file was made with: it carries no adapter state, and a
restore under other weights, another adapter or another template is silently accepted. So the
snapshot has an identity stored beside it, and restore() refuses any mismatch:

  model_sha256, drafter_sha256       from the server's /data/afterglow/serve/current.json on the serving box
  adapter_sha256, adapter_scale      the loaded adapter and the per-request scale the client sends
  template_sha256, template_kwargs   the chat template from /props and the reasoning strength
  prefix_sha256                      the messages and tools the slot was filled with

Metadata: runs/<world>/kv/start.json. Slot files: /data/afterglow/slots/<file> on the serving box.

Glimmer has sliding-window layers, so llama-server can reuse cached KV only up to its last context
checkpoint before the point where a new prompt differs. So the slot is always filled with exactly a
rendered prefix (up to the end token of the last message, before the next "<|start|>"): the next
request then extends the cached tokens instead of diverging inside them.
"""
import datetime as dt
import hashlib
import json
import os
import subprocess
import time

from . import paths
from .llm import HOSTS, SERVE_HOST, GlimmerClient
from .tools import TOOL_SCHEMAS

JST = dt.timezone(dt.timedelta(hours=9))
SERVE_STATE = f"{HOSTS['root']}/serve/current.json"
PREFIX_MARK = "afterglow-prefix-mark-7f3a"
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


def meta_path(world):
    d = paths.runs_root() / world / "kv"
    d.mkdir(parents=True, exist_ok=True)
    return d / "start.json"


def render_prefix(session, messages):
    """The prompt text of `messages` exactly as the chat template renders it ahead of the next message."""
    r = session.client.post("/apply-template", {
        "messages": messages + [{"role": "user", "content": PREFIX_MARK}], "tools": TOOL_SCHEMAS,
        "chat_template_kwargs": {"reasoning_strength": session.reasoning}})
    prompt = r["prompt"]
    return prompt[:prompt.rindex("<|start|>", 0, prompt.index(PREFIX_MARK))]


def prefill(session, messages, erase=False):
    """Fill slot 0 with exactly the rendered prefix of `messages` (no generated tokens). Without erase, the
    cached tokens that match are reused and only the rest is computed. Returns llama-server's timings."""
    client = session.client
    if erase:
        client.slot_action("erase")
    body = {"prompt": render_prefix(session, messages), "n_predict": 0, "id_slot": client.slot,
            "cache_prompt": True}
    lora = client.lora_field()
    if lora:
        body["lora"] = lora
    return client.post("/completion", body).get("timings") or {}


def rewarm(session):
    """Prefill the world's stable prefix (system prompt and tools) into slot 0 under the current weights and
    adapter, and save it as the world's start snapshot. Meta night calls this after a night update, once the
    server runs with the new adapter:
        from afterglow.agent import DaySession; from afterglow.snapshot import rewarm
        rewarm(DaySession(world, next_day))
    """
    client = session.client
    ident = identity(client, session.reasoning)
    prefix = session.prefix_messages()
    timings = prefill(session, prefix, erase=True)
    fname = f"afterglow-{session.world}-start-{sha256_json(ident)[:12]}.bin"
    t0 = time.time()
    r = client.slot_action("save", fname)
    meta = {"world": session.world, "slot": client.slot, "filename": fname, "saved_at": now_iso(),
            "n_tokens": r.get("n_saved"), "bytes": r.get("n_written"),
            "save_ms": (r.get("timings") or {}).get("save_ms"), "save_wall_s": round(time.time() - t0, 3),
            "identity": ident, "prefix_sha256": sha256_json({"messages": prefix, "tools": TOOL_SCHEMAS}),
            "prefill_ms": timings.get("prompt_ms")}
    meta_path(session.world).write_text(json.dumps(meta, indent=1))
    return meta


def check(session):
    """Return (meta, reasons): reasons is [] when the snapshot fits the running server and this session."""
    p = meta_path(session.world)
    if not p.exists():
        return None, ["no snapshot"]
    meta = json.loads(p.read_text())
    reasons = [f"{k} differs" for k in compare(meta["identity"], identity(session.client, session.reasoning))]
    if sha256_json({"messages": session.prefix_messages(), "tools": TOOL_SCHEMAS}) != meta["prefix_sha256"]:
        reasons.append("prefix differs")
    return meta, reasons


def start(session):
    """Startup: restore the world's prefix snapshot if it fits (else re-warm and save one), then prefill the day
    context and the day so far, so the user's next turn extends the cache. Returns a report for the UI."""
    meta, refused = check(session)
    report = {"refused": refused}
    if refused:
        meta = rewarm(session)
        report.update(kind="rewarmed", prefix_tokens=meta["n_tokens"], prefix_ms=meta["prefill_ms"])
    else:
        r = session.client.slot_action("restore", meta["filename"])
        report.update(kind="restored", prefix_tokens=r.get("n_restored"),
                      restore_ms=(r.get("timings") or {}).get("restore_ms"), prefix_cold_ms=meta.get("prefill_ms"))
    t = prefill(session, session.messages)
    report.update(day_tokens=t.get("prompt_n"), reused_tokens=t.get("cache_n"), day_prefill_ms=t.get("prompt_ms"))
    return report


if __name__ == "__main__":
    import argparse
    from .agent import DaySession

    ap = argparse.ArgumentParser(description="KV snapshot of a world's stable prefix")
    ap.add_argument("action", choices=["rewarm", "start", "check"])
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True)
    a = ap.parse_args()
    s = DaySession(a.world, a.day, GlimmerClient())
    out = {"rewarm": rewarm, "start": start, "check": lambda x: check(x)[1]}[a.action](s)
    print(json.dumps(out, indent=1))
