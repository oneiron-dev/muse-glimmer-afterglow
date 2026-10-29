"""KV snapshot of the world's stable prefix in the user's slot (slot 0): restore at startup, re-warm after a
night update.

The snapshot holds only the prefix that is the same for every day of a world: the system prompt (persona
header and rules) and the tools, about 1.2K tokens. A slot file is kept at or under MAX_SNAPSHOT_BYTES (the
owner's ~150 MB rule; each token costs ~170-195 KB of KV on llama.cpp v0.7.0), so it holds the longest
leading part of the prefix that fits, and the rest of the prefix is prefilled after the restore with the day
context (date, notes) and the day so far. There is no end-of-day snapshot: files would grow with the
conversation (363 MB for a 2.5K-token day).

llama-server does not check what a slot file was made with: it carries no adapter state, and a
restore under other weights, another adapter or another template is silently accepted. So the
snapshot has an identity stored beside it, and restore() refuses any mismatch:

  model_sha256, drafter_sha256       from the server's /data/afterglow/serve/current.json on the serving box
  llama_server, ctx, slots           the server binary and its context layout, from the same file
  adapter_path, adapter_sha256,      the loaded adapter (its serving path and file hash) and the per-request
  adapter_scale                      scale the client sends
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
import urllib.parse
import uuid

from . import paths
from .llm import HOSTS, GlimmerClient, GlimmerError

JST = dt.timezone(dt.timedelta(hours=9))
SERVE_STATE = f"{HOSTS['root']}/serve/current.json"
MAX_SNAPSHOT_BYTES = 150_000_000


def now_iso():
    return dt.datetime.now(JST).isoformat(timespec="seconds")


def sha256_json(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def serving_box(endpoint):
    """The hosts.json box whose proxy `endpoint` is, so the weights identity and the KV come from one server."""
    addr = urllib.parse.urlparse(endpoint).hostname
    box = next((name for name, h in HOSTS["hosts"].items() if h["tailnet"] == addr), None)
    if box is None:
        raise GlimmerError(f"{endpoint} is no serving box in hosts.json; set AFTERGLOW_SERVE_STATE for a mock")
    return box


def serve_state(endpoint):
    """The running server's current.json (weights and adapter hashes), read fresh over ssh from the box behind
    `endpoint`. AFTERGLOW_SERVE_STATE=<local file> reads a local copy instead (the mock server writes one)."""
    if os.environ.get("AFTERGLOW_SERVE_STATE"):
        with open(os.environ["AFTERGLOW_SERVE_STATE"]) as f:
            return json.load(f)
    box = serving_box(endpoint)
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=5", box, f"cat {SERVE_STATE}"],
                       capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise GlimmerError(f"cannot read {box}:{SERVE_STATE}: {r.stderr.strip()}")
    return json.loads(r.stdout)


def identity(client, reasoning):
    st = serve_state(client.endpoint)
    props = client.get("/props")
    mine = client.world_adapter(client.adapters(max_age_s=0))
    loaded = st.get("lora") or []
    adapter = next((x for x in loaded if mine and x.get("id") == mine["id"]), {}) if isinstance(loaded, list) else loaded
    return {
        "model_sha256": st["model"]["sha256"],
        "drafter_sha256": st["drafter"]["sha256"],
        "llama_server": (st.get("llama_cmd") or [None])[0],
        "ctx": st.get("ctx"),
        "slots": st.get("slots"),
        "adapter_path": mine["path"] if mine else None,
        "adapter_sha256": adapter.get("sha256"),
        "adapter_scale": (client.lora_scale or 0.0) if mine else None,  # the scale lora_field() sends
        "template_sha256": hashlib.sha256((props.get("chat_template") or "").encode()).hexdigest(),
        "template_kwargs": {"reasoning_strength": reasoning},
        "server_started": st.get("started"),
        # the control vectors the chat sends; absent with no dial, so a snapshot saved before any dial still matches
        **({"dials": [{"id": d["id"], "scale": d["scale"]} for d in client.dials]} if client.dials else {}),
    }


def compare(saved, current):
    """Fields that differ, over both sides' keys, so a field added later counts as a difference
    (server_started is informational: a restarted server with the same files is fine)."""
    return sorted(k for k in set(saved) | set(current) if k != "server_started" and saved.get(k) != current.get(k))


def identity_hash(ident):
    return sha256_json({k: v for k, v in ident.items() if k != "server_started"})


def meta_path(world):
    d = paths.runs_root() / world / "kv"
    d.mkdir(parents=True, exist_ok=True)
    return d / "start.json"


def render_prefix(session, messages):
    """The prompt text of `messages` exactly as the chat template renders it ahead of the next message."""
    mark = f"afterglow-prefix-mark-{uuid.uuid4().hex}"
    r = session.client.post("/apply-template", {
        "messages": messages + [{"role": "user", "content": mark}], "tools": session.tool_schemas,
        "chat_template_kwargs": {"reasoning_strength": session.reasoning}})
    prompt = r["prompt"]
    at = prompt.find(mark)
    cut = prompt.rfind("<|start|>", 0, at)
    if prompt.count(mark) != 1 or cut < 0 or not prompt[cut:at].startswith("<|start|>user"):
        raise GlimmerError("the chat template no longer renders a user turn as <|start|>user…; cannot cut the prefix")
    return prompt[:cut]


def prefix_tokens(session, messages):
    """The rendered prefix of `messages` as the token ids llama-server makes of that prompt text."""
    return session.client.post("/tokenize", {"content": render_prefix(session, messages), "add_special": True,
                                             "parse_special": True})["tokens"]


def prefill(session, messages, erase=False, n_tokens=None):
    """Fill slot 0 with exactly the rendered prefix of `messages` (no generated tokens), or with its first
    `n_tokens` tokens. Without erase, the cached tokens that match are reused and only the rest is computed.
    Returns llama-server's timings."""
    client = session.client
    if erase:
        client.slot_action("erase")
    prompt = render_prefix(session, messages) if n_tokens is None else prefix_tokens(session, messages)[:n_tokens]
    body = {"prompt": prompt, "n_predict": 0, "id_slot": client.slot, "cache_prompt": True, **client.scale_fields()}
    return client.post("/completion", body).get("timings") or {}


def rewarm(session, max_bytes=MAX_SNAPSHOT_BYTES):
    """Prefill the world's stable prefix (system prompt and tools) into slot 0 under the current weights and
    adapter, and save it as the world's start snapshot: the whole prefix, or its longest leading part whose slot
    file fits `max_bytes`. Meta night calls this after a night update, once the server runs with the new adapter:
        from afterglow.agent import DaySession; from afterglow.snapshot import rewarm
        rewarm(DaySession(world, next_day))
    """
    client = session.client
    ident = identity(client, session.reasoning)
    prefix = session.prefix_messages()
    n_total = len(prefix_tokens(session, prefix))
    fname = f"afterglow-{session.world}-start-{identity_hash(ident)[:12]}.bin"
    n = None  # the whole prefix first; shorter while the file is over max_bytes
    for _ in range(4):
        timings = prefill(session, prefix, erase=True, n_tokens=n)
        t0 = time.time()
        r = client.slot_action("save", fname)
        if not r.get("n_saved"):
            raise GlimmerError(f"slot save of {fname} wrote nothing: {r}")
        if r.get("n_written", 0) <= max_bytes:
            break
        n = int(r["n_saved"] * max_bytes / r["n_written"] * 0.97)
    else:
        raise GlimmerError(f"{fname}: {r.get('n_written')} bytes after 4 tries, over {max_bytes}")
    changed = compare(ident, identity(client, session.reasoning))
    if changed:  # the server changed under the prefill: this file holds KV of unknown weights
        raise GlimmerError(f"the server changed while re-warming ({', '.join(changed)}); run rewarm again")
    meta = {"world": session.world, "slot": client.slot, "filename": fname, "saved_at": now_iso(),
            "n_tokens": r.get("n_saved"), "prefix_tokens": n_total, "bytes": r.get("n_written"), "max_bytes": max_bytes,
            "save_ms": (r.get("timings") or {}).get("save_ms"), "save_wall_s": round(time.time() - t0, 3),
            "identity": ident, "prefix_sha256": sha256_json({"messages": prefix, "tools": session.tool_schemas}),
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
    if sha256_json({"messages": session.prefix_messages(), "tools": session.tool_schemas}) != meta["prefix_sha256"]:
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
        # llama-server drops a slot's cached prompt when a request's adapter or dial scales differ from the slot's
        # last ones, and a restore does not set them. So the slot takes this world's scales first.
        scales = session.client.scale_fields()
        if scales:
            session.client.post("/completion", {"prompt": "<|start|>", "n_predict": 0, "id_slot": session.client.slot,
                                                "cache_prompt": True, **scales})
        r = session.client.slot_action("restore", meta["filename"])
        if not r.get("n_restored"):
            raise GlimmerError(f"slot restore of {meta['filename']} restored nothing: {r}")
        report.update(kind="restored", prefix_tokens=r.get("n_restored"),
                      restore_ms=(r.get("timings") or {}).get("restore_ms"), prefix_cold_ms=meta.get("prefill_ms"))
    t = prefill(session, session.messages)
    report.update(day_tokens=t.get("prompt_n"), reused_tokens=t.get("cache_n"), day_prefill_ms=t.get("prompt_ms"))
    return report


if __name__ == "__main__":
    import argparse
    from .agent import DaySession

    ap = argparse.ArgumentParser(description="KV snapshot of a world's stable prefix")
    ap.add_argument("action", choices=["rewarm", "rewarm-all", "start", "check"])
    ap.add_argument("--world")
    ap.add_argument("--day", type=int)
    a = ap.parse_args()
    if a.action == "rewarm-all":
        # After a server restart or an adapter change: one fresh prefix snapshot per world under the new weights.
        for wdir in sorted(p for p in paths.runs_root().iterdir() if p.is_dir() and any(d.name.isdigit() for d in p.iterdir())):
            day = max(int(d.name) for d in wdir.iterdir() if d.name.isdigit())
            m = rewarm(DaySession(wdir.name, day, GlimmerClient()))
            print(json.dumps({"world": wdir.name, "day": day, "tokens": m["n_tokens"], "of": m["prefix_tokens"], "bytes": m["bytes"],
                              "adapter_scale": m["identity"]["adapter_scale"]}))
        raise SystemExit(0)
    if not a.world or a.day is None:
        ap.error("--world and --day are required")
    s = DaySession(a.world, a.day, GlimmerClient())
    out = {"rewarm": rewarm, "start": start, "check": lambda x: check(x)[1]}[a.action](s)
    print(json.dumps(out, indent=1))
