#!/usr/bin/env python3
"""A stand-in for the Glimmer llama-server, for building and testing the app with no GPU. Stdlib only.

Speaks the endpoints the app uses: /health, /props, /v1/models, /lora-adapters, /apply-template,
/completion, /slots/<id>?action=save|restore|erase, and /v1/chat/completions (streamed and not,
with reasoning_content and tool calls in llama-server's frame format). The "model" is a few rules:
a statement is saved with remember, a question is looked up with recall, then it answers from the
tool result. Replies are paced like real decoding (--tok-s). It also writes a serve-state file like
nelly's current.json so snapshot identities work.

  python3 app/serve/mock_glimmer.py --port 18690 --state-file /tmp/mock-serve-state.json
  AFTERGLOW_ENDPOINT=http://127.0.0.1:18690 AFTERGLOW_SERVE_STATE=/tmp/mock-serve-state.json ...
"""
import argparse
import hashlib
import json
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ALIAS = "muse-glimmer-30b"
TEMPLATE = "{# mock template for the Afterglow app #}{{ messages }}"


def render(messages, tools=None):
    """A stable text rendering, so prefixes behave like the real template."""
    out = []
    if tools:
        out.append("<|start|>system<|message|>tools:" + ",".join(t["function"]["name"] for t in tools) + "<|eot|>")
    for m in messages:
        body = m.get("content") or ""
        if m.get("tool_calls"):
            body += json.dumps(m["tool_calls"], sort_keys=True)
        out.append(f"<|start|>{m['role']}<|message|>{body}<|eot|>")
    return "".join(out)


def ntok(text):
    return max(1, len(text) // 4)


class Mock:
    def __init__(self, tok_s, state_file):
        self.tok_s = tok_s
        self.slots = {0: "", 1: ""}
        self.saved = {}
        self.lock = threading.Lock()
        if state_file:
            with open(state_file, "w") as f:
                json.dump({"state": "ready", "started": time.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
                           "model": {"path": "mock", "sha256": "mock-model"},
                           "drafter": {"path": "mock", "sha256": "mock-drafter"}, "lora": None}, f)

    def reply_plan(self, messages):
        """(reasoning, tool_call or None, content) for the next assistant message."""
        last = messages[-1]
        if last["role"] == "tool":
            result = json.loads(last.get("content") or "{}")
            if "saved" in result:
                was = f" (it was {result['replaced']['old_value']})" if result.get("replaced") else ""
                return ("The note is saved. Confirm briefly.", None,
                        f"Noted: {result['key']} is {result['value']}{was}.")
            if "notes" in result:
                if result["notes"]:
                    n = result["notes"][0]
                    return ("Found a note. Answer from it.", None, f"From my notes: {n['key']} is {n['value']}.")
                return ("Nothing saved on that.", None, "I have nothing saved about that yet.")
            return ("Tool done.", None, "Done.")
        text = (last.get("content") or "").strip()
        if text.endswith("?"):
            q = re.sub(r"[^\w\s:-]", "", text).strip()
            return (f"The user asks: {text}\n\nI should check my notes first.",
                    {"name": "recall", "arguments": {"query": q}}, None)
        m = re.match(r"(?i)^(?:correction:\s*)?(?:my|our|the)?\s*(.+?)\s+(?:is|are|moved to|=)\s+(.+?)\.?$", text)
        if m:
            key, value = m.group(1).strip(), m.group(2).strip()
        else:
            words = text.split()
            key, value = " ".join(words[:3]), " ".join(words[3:]) or text
        return (f"The user tells me: {text}\n\nThis is a durable fact. Save it.",
                {"name": "remember", "arguments": {"key": key, "value": value}}, None)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    mock: Mock = None

    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/health":
            return self._json({"status": "ok"})
        if p == "/props":
            return self._json({"chat_template": TEMPLATE, "model_alias": ALIAS, "total_slots": 2})
        if p == "/v1/models":
            return self._json({"data": [{"id": ALIAS}]})
        if p == "/lora-adapters":
            return self._json([])
        if p == "/slots":
            return self._json([{"id": i, "n_tokens": ntok(t)} for i, t in self.mock.slots.items()])
        return self._json({"error": {"code": 404, "message": "File Not Found"}}, 404)

    def do_POST(self):
        p, _, q = self.path.partition("?")
        body = self._body()
        m = self.mock
        if p == "/apply-template":
            return self._json({"prompt": render(body["messages"], body.get("tools"))})
        if p == "/completion":
            slot = body.get("id_slot", 0)
            t0 = time.time()
            n = ntok(body["prompt"])
            time.sleep(n / 3000)
            m.slots[slot] = body["prompt"]
            return self._json({"content": "", "tokens_evaluated": n,
                               "timings": {"prompt_n": n, "cache_n": 0, "prompt_ms": (time.time() - t0) * 1000}})
        if p.startswith("/slots/"):
            slot = int(p.rsplit("/", 1)[-1])
            action = dict(x.split("=") for x in q.split("&") if "=" in x).get("action")
            if action == "erase":
                m.slots[slot] = ""
                return self._json({"id_slot": slot, "n_erased": 0})
            if action == "save":
                m.saved[body["filename"]] = m.slots[slot]
                return self._json({"id_slot": slot, "filename": body["filename"], "n_saved": ntok(m.slots[slot]),
                                   "n_written": len(m.slots[slot]) * 1000, "timings": {"save_ms": 120.0}})
            if action == "restore":
                if body["filename"] not in m.saved:
                    return self._json({"error": {"code": 400, "message": "failed to restore slot"}}, 400)
                m.slots[slot] = m.saved[body["filename"]]
                return self._json({"id_slot": slot, "filename": body["filename"],
                                   "n_restored": ntok(m.slots[slot]), "timings": {"restore_ms": 45.0}})
        if p == "/v1/chat/completions":
            return self._chat(body)
        return self._json({"error": {"code": 404, "message": "File Not Found"}}, 404)

    def _chat(self, body):
        m = self.mock
        slot = body.get("id_slot", 0)
        prompt = render(body["messages"], body.get("tools"))
        cached = m.slots.get(slot, "")
        common = 0
        for a, b in zip(cached, prompt):
            if a != b:
                break
            common += 1
        cache_n, prompt_n = ntok(cached[:common]) if common else 0, ntok(prompt[common:])
        reasoning, call, content = m.reply_plan(body["messages"])
        max_tokens = body.get("max_tokens") or 1024
        t0 = time.time()
        time.sleep(prompt_n / 3000)
        prompt_ms = (time.time() - t0) * 1000
        pieces = [("reasoning_content", w) for w in re.findall(r"\S+\s*", reasoning)]
        if content:
            pieces += [("content", w) for w in re.findall(r"\S+\s*", content)]
        pieces = pieces[:max_tokens]
        m.slots[slot] = prompt + "".join(x for _, x in pieces)
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        tc = None
        if call and len(pieces) < max_tokens:
            tc = {"id": uuid.uuid4().hex[:24], "type": "function",
                  "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])}}
        finish = "tool_calls" if tc else ("length" if len(pieces) >= max_tokens else "stop")
        n_gen = len(pieces) + (8 if tc else 0)
        timings = {"cache_n": cache_n, "prompt_n": prompt_n, "prompt_ms": prompt_ms,
                   "predicted_n": n_gen, "predicted_per_second": float(m.tok_s)}
        if not body.get("stream"):
            msg = {"role": "assistant", "content": "".join(x for k, x in pieces if k == "content"),
                   "reasoning_content": "".join(x for k, x in pieces if k == "reasoning_content")}
            if tc:
                msg["tool_calls"] = [tc]
            return self._json({"id": cid, "object": "chat.completion", "model": ALIAS,
                               "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                               "usage": {"prompt_tokens": cache_n + prompt_n, "completion_tokens": n_gen},
                               "timings": timings})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def send(obj):
            data = b"data: " + (obj if isinstance(obj, bytes) else json.dumps(obj).encode()) + b"\n\n"
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()

        base = {"id": cid, "object": "chat.completion.chunk", "model": ALIAS}
        try:
            send(dict(base, choices=[{"index": 0, "delta": {"role": "assistant", "content": None}}]))
            for kind, text in pieces:
                time.sleep(1 / m.tok_s)
                send(dict(base, choices=[{"index": 0, "delta": {kind: text}, "finish_reason": None}]))
            if tc:
                send(dict(base, choices=[{"index": 0, "delta": {"tool_calls": [{
                    "index": 0, "id": tc["id"], "type": "function",
                    "function": {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}}]}}]))
            send(dict(base, choices=[{"index": 0, "delta": {}, "finish_reason": finish}], timings=timings,
                      usage={"prompt_tokens": cache_n + prompt_n, "completion_tokens": n_gen}))
            send(b"[DONE]")
            self.wfile.write(b"0\r\n\r\n")
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=18690)
    ap.add_argument("--tok-s", type=float, default=80.0)
    ap.add_argument("--state-file", default=None, help="write a serve-state file like nelly's current.json")
    a = ap.parse_args()
    Handler.mock = Mock(a.tok_s, a.state_file)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    srv.daemon_threads = True
    print(f"mock Glimmer at http://127.0.0.1:{a.port} ({a.tok_s:g} tok/s)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
