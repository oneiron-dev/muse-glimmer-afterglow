#!/usr/bin/env python3
"""Pass-through HTTP proxy in front of llama-server that keeps a thought log.

Every request goes upstream unchanged (method, path, headers, body), with one exception
below. Every response comes back unchanged: status, headers and body bytes, streamed as
they arrive, so SSE frames are not buffered or rewritten. Hop-by-hop headers (Connection,
Transfer-Encoding, ...) are per connection and are set again by this proxy.

The exception: with --lora-count N (N adapters loaded), a generation request with no
"lora" field gets one that sets every adapter to scale 0. llama-server checks a slot's
cached prompt against the request's adapter scales only when the request names them; a
request without them would reuse KV computed under another adapter. The trace record
says "lora_filled": true.

For each POST /v1/chat/completions the proxy appends one JSON line to
<trace-dir>/<YYYY-MM-DD>.jsonl (JST date of the request): the request, the reply model,
reasoning_content, content, tool calls, finish reason, usage and timings.

Python stdlib only, so it runs on the serving box with no setup.

  python3 trace_proxy.py --listen 100.96.31.41:18610 --upstream 127.0.0.1:18611 \
      --trace-dir /data/afterglow/traces
"""
import argparse
import datetime as dt
import http.client
import http.server
import json
import os
import sys
import threading
import uuid

JST = dt.timezone(dt.timedelta(hours=9))
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection",
    "te", "trailer", "trailers", "transfer-encoding", "upgrade", "expect",
}
TRACED_PATHS = {"/v1/chat/completions", "/chat/completions"}
# llama-server endpoints that run a slot with per-request adapter scales
LORA_PATHS = TRACED_PATHS | {"/completion", "/completions", "/v1/completions", "/infill"}
READ_SIZE = 65536


def now_iso():
    return dt.datetime.now(JST).isoformat(timespec="milliseconds")


class TraceWriter:
    """Appends one JSON record per line to a per-day file. Thread-safe."""

    def __init__(self, trace_dir):
        self.trace_dir = trace_dir
        self.lock = threading.Lock()
        if trace_dir:
            os.makedirs(trace_dir, exist_ok=True)

    def write(self, record):
        if not self.trace_dir:
            return
        day = record["request_time"][:10]
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self.lock:
            with open(os.path.join(self.trace_dir, f"{day}.jsonl"), "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()


def fill_lora(body, lora_count):
    """The body with "lora" set to every adapter at scale 0 when it names none; (body, filled)."""
    try:
        req = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body, False
    if not isinstance(req, dict) or req.get("lora"):
        return body, False
    req["lora"] = [{"id": i, "scale": 0.0} for i in range(lora_count)]
    return json.dumps(req, ensure_ascii=False).encode(), True


def summarise_request(body):
    try:
        req = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return {"request_parse_error": True}
    if not isinstance(req, dict):
        return {"request_parse_error": True}
    keep = ("model", "messages", "tools", "stream", "max_tokens", "temperature", "id_slot",
            "chat_template_kwargs", "lora", "cache_prompt")
    out = {k: req[k] for k in keep if k in req}
    out["requested_model"] = out.pop("model", None)
    return out


def summarise_reply_json(body):
    """Fields from a non-streamed chat completion."""
    try:
        r = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return {"reply_parse_error": True}
    if not isinstance(r, dict):
        return {"reply_parse_error": True}
    choice = (r.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    return {
        "reply_id": r.get("id"),
        "reply_model": r.get("model"),
        "reasoning_content": msg.get("reasoning_content"),
        "content": msg.get("content"),
        "tool_calls": msg.get("tool_calls"),
        "finish_reason": choice.get("finish_reason"),
        "usage": r.get("usage"),
        "timings": r.get("timings"),
        "error": r.get("error"),
    }


class StreamAccumulator:
    """Rebuilds one chat completion from SSE bytes as they pass. Never changes the bytes."""

    def __init__(self):
        self.buf = b""
        self.frames = 0
        self.models = []
        self.reply_id = None
        self.reasoning = []
        self.content = []
        self.tool_calls = {}
        self.finish_reason = None
        self.usage = None
        self.timings = None
        self.error = None

    def feed(self, data):
        self.buf += data
        while True:
            cut = self.buf.find(b"\n")
            if cut < 0:
                return
            line, self.buf = self.buf[:cut].strip(), self.buf[cut + 1:]
            if line.startswith(b"data:"):
                self._frame(line[5:].strip())

    def _frame(self, payload):
        if payload == b"[DONE]":
            return
        try:
            f = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(f, dict):
            return
        self.frames += 1
        model = f.get("model")
        if model not in self.models:
            self.models.append(model)
        self.reply_id = self.reply_id or f.get("id")
        if f.get("error"):
            self.error = f["error"]
        for choice in f.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("reasoning_content"):
                self.reasoning.append(delta["reasoning_content"])
            if delta.get("content"):
                self.content.append(delta["content"])
            for tc in delta.get("tool_calls") or []:
                slot = self.tool_calls.setdefault(tc.get("index", 0), {"id": None, "type": "function",
                                                                       "function": {"name": "", "arguments": ""}})
                slot["id"] = tc.get("id") or slot["id"]
                fn = tc.get("function") or {}
                slot["function"]["name"] += fn.get("name") or ""
                slot["function"]["arguments"] += fn.get("arguments") or ""
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
        if f.get("usage"):
            self.usage = f["usage"]
        if f.get("timings"):
            self.timings = f["timings"]

    def summary(self):
        if self.buf.strip():
            self.feed(b"\n")
        return {
            "reply_id": self.reply_id,
            "reply_model": self.models[0] if len(self.models) == 1 else self.models,
            "stream_frames": self.frames,
            "reasoning_content": "".join(self.reasoning) or None,
            "content": "".join(self.content) or None,
            "tool_calls": [self.tool_calls[i] for i in sorted(self.tool_calls)] or None,
            "finish_reason": self.finish_reason,
            "usage": self.usage,
            "timings": self.timings,
            "error": self.error,
        }


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    disable_nagle_algorithm = True
    server_version = "afterglow-trace-proxy"
    upstream = ("127.0.0.1", 18611)
    upstream_timeout = 3600
    writer = TraceWriter(None)
    tee_dir = None
    lora_count = 0

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{now_iso()} {self.client_address[0]} {fmt % args}\n")

    def _read_body(self):
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            parts = []
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    return b"".join(parts)
                parts.append(self.rfile.read(size))
                self.rfile.readline()
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _forward(self):
        request_time = now_iso()
        body = self._read_body()
        path_only = self.path.split("?", 1)[0]
        traced = self.command == "POST" and path_only in TRACED_PATHS
        record = None
        filled = False
        if self.lora_count and self.command == "POST" and path_only in LORA_PATHS:
            body, filled = fill_lora(body, self.lora_count)
        if traced:
            record = {"request_time": request_time, "request_id": uuid.uuid4().hex,
                      "client": self.client_address[0], "path": self.path}
            record.update(summarise_request(body))
            if filled:
                record["lora_filled"] = True

        conn = http.client.HTTPConnection(*self.upstream, timeout=self.upstream_timeout)
        try:
            # Original headers in their order and case; only the per-connection ones and the length are set again.
            conn.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
            for k, v in self.headers.items():
                if k.lower() not in HOP_BY_HOP and k.lower() != "content-length":
                    conn.putheader(k, v)
            if body or self.command in ("POST", "PUT", "PATCH"):
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body if body else None)
            resp = conn.getresponse()
        except OSError as e:
            conn.close()
            msg = json.dumps({"error": {"code": 502, "type": "proxy_upstream_unreachable",
                                        "message": f"upstream {self.upstream[0]}:{self.upstream[1]}: {e}"}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            if record is not None:
                record.update({"status": 502, "error": str(e), "done_time": now_iso()})
                self._trace(record)
            return

        is_sse = "text/event-stream" in (resp.getheader("Content-Type") or "")
        length = resp.getheader("Content-Length")
        chunked_out = length is None and self.command != "HEAD" and resp.status not in (204, 304)
        self.log_request(resp.status)
        self.send_response_only(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() not in HOP_BY_HOP:
                self.send_header(k, v)
        if chunked_out:
            self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        acc = StreamAccumulator() if (traced and is_sse) else None
        collected = [] if (traced and not is_sse) or self.tee_dir else None
        client_gone = False
        try:
            while True:
                data = resp.read1(READ_SIZE) if self.command != "HEAD" else b""
                if not data:
                    break
                if acc is not None:
                    acc.feed(data)
                if collected is not None:
                    collected.append(data)
                if client_gone:
                    continue
                try:
                    if chunked_out:
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                    else:
                        self.wfile.write(data)
                    self.wfile.flush()
                except OSError:
                    # Client left: stop reading so llama-server sees the close and cancels the task.
                    client_gone = True
                    break
            if chunked_out and not client_gone:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
        except OSError:
            client_gone = True
        finally:
            conn.close()
        if client_gone:
            self.close_connection = True

        if self.tee_dir and record is not None:
            os.makedirs(self.tee_dir, exist_ok=True)
            with open(os.path.join(self.tee_dir, f"{record['request_id']}.body"), "wb") as f:
                f.write(b"".join(collected))
        if record is not None:
            record["status"] = resp.status
            if acc is not None:
                record.update(acc.summary())
            else:
                record.update(summarise_reply_json(b"".join(collected)))
            if client_gone:
                record["client_disconnected"] = True
            record["done_time"] = now_iso()
            self._trace(record)

    def _trace(self, record):
        try:
            self.writer.write(record)
        except OSError as e:
            sys.stderr.write(f"{now_iso()} trace write failed: {e}\n")

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _forward


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def host_port(text):
    host, _, port = text.rpartition(":")
    return host, int(port)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--listen", required=True, help="HOST:PORT to bind (a tailnet or loopback address)")
    ap.add_argument("--upstream", default="127.0.0.1:18611", help="llama-server HOST:PORT")
    ap.add_argument("--trace-dir", default="/data/afterglow/traces")
    ap.add_argument("--tee-dir", default=None,
                    help="test only: also write each traced response body, raw, to <tee-dir>/<request_id>.body")
    ap.add_argument("--lora-count", type=int, default=0,
                    help="adapters loaded in llama-server; requests that name no scales get all of them at 0")
    args = ap.parse_args()

    host, port = host_port(args.listen)
    if host in ("", "0.0.0.0", "::"):
        sys.exit("refusing to bind all interfaces; give a tailnet or loopback address")
    ProxyHandler.upstream = host_port(args.upstream)
    ProxyHandler.writer = TraceWriter(args.trace_dir)
    ProxyHandler.tee_dir = args.tee_dir
    ProxyHandler.lora_count = args.lora_count
    srv = Server((host, port), ProxyHandler)
    print(f"{now_iso()} trace proxy {host}:{port} -> {args.upstream}, traces in {args.trace_dir}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
