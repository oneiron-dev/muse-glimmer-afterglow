"""Client for the Muse Glimmer llama-server (OpenAI-compatible API behind the trace proxy). Stdlib only."""
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

# The serving boxes (app/serve/hosts.json). AFTERGLOW_SERVE_HOST picks one; AFTERGLOW_ENDPOINT overrides the URL.
HOSTS = json.loads((Path(__file__).resolve().parents[1] / "serve" / "hosts.json").read_text())
SERVE_HOST = os.environ.get("AFTERGLOW_SERVE_HOST", HOSTS["default"])
ALIAS = HOSTS["alias"]
USER_SLOT = 0


class GlimmerError(RuntimeError):
    pass


def default_endpoint():
    """AFTERGLOW_ENDPOINT, else the proxy on the serving box (read at call time, so tests can point it at a mock)."""
    host = os.environ.get("AFTERGLOW_SERVE_HOST", HOSTS["default"])
    return os.environ.get("AFTERGLOW_ENDPOINT") or f"http://{HOSTS['hosts'][host]['tailnet']}:{HOSTS['proxy_port']}"


def world_adapter_entry(world):
    """The world's latest kept adapter in the night job's serving manifest (runs/serving/adapters.json, schema in
    serve/glimmer_server.py adapter_args): {"id", "world", "day", "path", "sha256", ...}. None when the manifest
    is absent or lists no kept adapter for the world."""
    from . import paths

    f = paths.runs_root() / "serving" / "adapters.json"
    if not f.exists():
        return None
    entries = json.loads(f.read_text()).get("adapters")
    if not isinstance(entries, list) or not all(isinstance(a, dict) and a.get("path") for a in entries):
        raise GlimmerError(f"{f}: expected {{\"adapters\": [{{\"id\", \"world\", \"path\", ...}}]}}")
    kept = [a for a in entries if a.get("world") == world and a.get("decision", "keep") == "keep"]
    return max(kept, key=lambda a: a.get("day", 0)) if kept else None


class GlimmerClient:
    """One llama-server. Every chat call pins a slot and, when an adapter is loaded, sends its scale per request
    (slot files and the global scale do not track adapters, so a per-request scale is the only safe form)."""

    def __init__(self, endpoint=None, slot=USER_SLOT, lora_scale=1.0, adapter_path=None, timeout=600):
        self.endpoint = (endpoint or default_endpoint()).rstrip("/")
        self.slot = slot
        self.lora_scale = lora_scale  # the scale for this world's adapter, when the server has it loaded
        self.adapter_path = adapter_path  # this world's adapter on the server; every other adapter gets scale 0
        self.timeout = timeout
        self._adapters = (0.0, [])

    # --- plain HTTP ---
    def get(self, path, timeout=10):
        with urllib.request.urlopen(self.endpoint + path, timeout=timeout) as r:
            return json.loads(r.read())

    def post(self, path, body, timeout=None, headers=None):
        req = urllib.request.Request(self.endpoint + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise GlimmerError(f"{path}: HTTP {e.code}: {e.read()[:500]!r}") from e

    def health(self):
        try:
            return self.get("/health", timeout=5).get("status") == "ok"
        except (OSError, ValueError):
            return False

    def adapters(self, max_age_s=10):
        """Loaded LoRA adapters: [{"id", "path", "scale"}]; [] when none. Cached briefly; a failed request raises
        (a failure is never taken for "no adapters", which would send requests without their scales)."""
        if time.time() - self._adapters[0] > max_age_s:
            try:
                self._adapters = (time.time(), self.get("/lora-adapters"))
            except (OSError, ValueError) as e:
                raise GlimmerError(f"/lora-adapters: {e}") from e
        return self._adapters[1]

    def world_adapter(self, loaded=None):
        """The loaded adapter that is this world's ({"id", "path", "scale"}), matched by its full serving path
        (every night's file is named adapter.gguf). None when the world has no adapter or the server has none
        loaded; raises when the server has adapters but not this world's, or the path twice."""
        if not self.adapter_path:
            return None
        loaded = self.adapters() if loaded is None else loaded
        hits = [a for a in loaded if a.get("path") == self.adapter_path]
        if len(hits) > 1 or (loaded and not hits):
            raise GlimmerError(f"the server's adapters {[a.get('path') for a in loaded]} hold this world's "
                               f"{self.adapter_path} {len(hits)} times; restart it with the serving manifest")
        return hits[0] if hits else None

    def lora_field(self, scale=None, loaded=None):
        """Per-request scales for every loaded adapter: this world's at `scale` (default lora_scale), all others
        0. None when the server has no adapter loaded. (Slot files and the global scale do not track adapters,
        so the scale is always sent per request.) Pass `loaded` to build several fields from one adapter list."""
        loaded = self.adapters() if loaded is None else loaded
        if not loaded:
            return None
        mine = self.world_adapter(loaded)
        scale = (self.lora_scale if scale is None else scale) or 0.0
        return [{"id": a["id"], "scale": scale if mine and a["id"] == mine["id"] else 0.0} for a in loaded]

    def request_body(self, messages, tools=None, *, reasoning="low", max_tokens=1024, temperature=0.3,
                     stream=True, n_predict=None, cache_prompt=True):
        body = {"model": ALIAS, "messages": messages, "id_slot": self.slot, "temperature": temperature,
                "max_tokens": max_tokens, "stream": stream, "cache_prompt": cache_prompt,
                "chat_template_kwargs": {"reasoning_strength": reasoning}}
        if stream:
            body["stream_options"] = {"include_usage": True}
        if tools:
            body["tools"] = tools
        if n_predict is not None:
            body["n_predict"] = n_predict
            body["max_tokens"] = n_predict
        lora = self.lora_field()
        if lora:
            body["lora"] = lora
        return body

    # --- streaming chat ---
    def stream_chat(self, messages, tools=None, **kw):
        """Yield events while Glimmer answers:
        {"type": "reasoning"|"content", "text"}, {"type": "tool_call_start", "index", "name"},
        {"type": "stats", "tokens", "elapsed_s", "tok_s"} about every 0.25 s, and last
        {"type": "done", "message", "finish_reason", "timings", "usage", "ttft_s", "total_s"}."""
        body = self.request_body(messages, tools, stream=True, **kw)
        req = urllib.request.Request(self.endpoint + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        t0 = time.time()
        ttft = None
        n_tokens = 0
        last_stats = 0.0
        reasoning, content = [], []
        calls = {}
        finish = timings = usage = None
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            raise GlimmerError(f"chat: HTTP {e.code}: {e.read()[:500]!r}") from e
        with resp:
            for raw in resp:
                line = raw.strip()
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload == b"[DONE]":
                    break
                frame = json.loads(payload)
                if frame.get("error"):
                    raise GlimmerError(f"chat: {frame['error']}")
                if frame.get("timings"):
                    timings = frame["timings"]
                if frame.get("usage"):
                    usage = frame["usage"]
                for choice in frame.get("choices") or []:
                    delta = choice.get("delta") or {}
                    got = False
                    if delta.get("reasoning_content"):
                        reasoning.append(delta["reasoning_content"])
                        got = True
                        yield {"type": "reasoning", "text": delta["reasoning_content"]}
                    if delta.get("content"):
                        content.append(delta["content"])
                        got = True
                        yield {"type": "content", "text": delta["content"]}
                    for tc in delta.get("tool_calls") or []:
                        got = True
                        i = tc.get("index", 0)
                        if i not in calls:
                            calls[i] = {"id": tc.get("id") or f"call_{i}", "type": "function",
                                        "function": {"name": "", "arguments": ""}}
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            calls[i]["function"]["name"] += fn["name"]
                            yield {"type": "tool_call_start", "index": i, "name": calls[i]["function"]["name"]}
                        calls[i]["function"]["arguments"] += fn.get("arguments") or ""
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
                    if got:
                        n_tokens += 1
                        if ttft is None:
                            ttft = time.time() - t0
                now = time.time()
                if ttft is not None and now - last_stats > 0.25:
                    last_stats = now
                    gen_s = max(1e-6, now - t0 - ttft)
                    yield {"type": "stats", "tokens": n_tokens, "elapsed_s": round(now - t0, 3),
                           "ttft_s": round(ttft, 3), "tok_s": round(n_tokens / gen_s, 1) if n_tokens > 1 else None}
        message = {"role": "assistant", "content": "".join(content)}
        if reasoning:
            message["reasoning_content"] = "".join(reasoning)
        if calls:
            message["tool_calls"] = [calls[i] for i in sorted(calls)]
        yield {"type": "done", "message": message, "finish_reason": finish, "timings": timings, "usage": usage,
               "ttft_s": round(ttft, 3) if ttft is not None else None, "total_s": round(time.time() - t0, 3)}

    def answer_channel(self, messages, tools=None, *, reasoning="low", max_tokens=320, temperature=0.3, why=None):
        """A final answer when Glimmer ended its turn with an empty reply (an adapter can close the turn inside
        the reasoning channel): render the same conversation with the chat template, open the answer channel and
        complete there, on the same slot with the same per-request scales. The caller counts every use."""
        body = {"messages": messages, "chat_template_kwargs": {"reasoning_strength": reasoning}}
        if tools:
            body["tools"] = tools
        rendered = self.post("/apply-template", body)["prompt"]
        prompt = rendered.removesuffix("<|start|>assistant") + "<|start|>assistant to=user<|message|>"
        req = {"prompt": prompt, "n_predict": max_tokens, "temperature": temperature, "id_slot": self.slot,
               "cache_prompt": True}
        lora = self.lora_field()
        if lora:
            req["lora"] = lora
        t0 = time.time()
        # The trace proxy records this /completion (it records chat calls only) with the reason.
        r = self.post("/completion", req, timeout=self.timeout,
                      headers={"X-Afterglow-Trace": f"answer_channel_retry:{why or 'unknown'}"})
        text = re.sub(r"(<\|(eot|end|return)\|>\s*)+$", "", (r.get("content") or "").strip()).strip()
        return {"content": text, "stop_type": r.get("stop_type"), "timings": r.get("timings"),
                "total_s": round(time.time() - t0, 3)}

    def chat(self, messages, tools=None, **kw):
        """Non-streamed convenience: returns the final 'done' event."""
        done = None
        for ev in self.stream_chat(messages, tools, **kw):
            if ev["type"] == "done":
                done = ev
        return done

    # --- slots (KV snapshots) ---
    def slots(self):
        return self.get("/slots")

    def slot_action(self, action, filename=None, slot=None):
        slot = self.slot if slot is None else slot
        body = {"filename": filename} if filename else {}
        return self.post(f"/slots/{slot}?action={action}", body, timeout=300)
