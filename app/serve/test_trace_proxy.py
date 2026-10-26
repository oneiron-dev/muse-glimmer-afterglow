#!/usr/bin/env python3
"""Tests for trace_proxy.py against a fake llama-server (stdlib only; no GPU).

  python3 app/serve/test_trace_proxy.py -v
"""
import http.client
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ALIAS = "muse-glimmer-30b"
FRAME_DELAY_S = 0.25


def sse_frames():
    base = {"id": "chatcmpl-fixed", "object": "chat.completion.chunk", "created": 1761500000, "model": ALIAS}
    deltas = [
        {"role": "assistant", "content": None},
        {"reasoning_content": "The note says the standup moved"},
        {"reasoning_content": " to 10:30."},
        {"content": "Standup is at "},
        {"content": "10:30."},
        {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                         "function": {"name": "recall", "arguments": "{\"query\":"}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": " \"standup\"}"}}]},
    ]
    for d in deltas:
        yield dict(base, choices=[{"index": 0, "delta": d, "finish_reason": None}])
    yield dict(base, choices=[{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
               usage={"prompt_tokens": 40, "completion_tokens": 12, "total_tokens": 52},
               timings={"prompt_n": 40, "predicted_n": 12, "predicted_per_second": 83.4})


NON_STREAM = {
    "id": "chatcmpl-fixed", "object": "chat.completion", "created": 1761500000, "model": ALIAS,
    "choices": [{"index": 0, "finish_reason": "stop",
                 "message": {"role": "assistant", "content": "Standup is at 10:30.",
                             "reasoning_content": "The note says it moved."}}],
    "usage": {"prompt_tokens": 40, "completion_tokens": 7, "total_tokens": 47},
    "timings": {"prompt_n": 40, "predicted_n": 7},
}


class FakeLlamaServer(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    disconnects = []

    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._json({"status": "ok"})
        elif self.path == "/slots":
            self._json([{"id": 0, "n_ctx": 32768}, {"id": 1, "n_ctx": 32768}])
        else:
            self._json({"error": {"code": 404, "message": "File Not Found"}}, 404)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path.startswith("/slots/"):
            self._json({"id_slot": 0, "path": self.path, "echo_len": len(body)})
            return
        req = json.loads(body)
        if not req.get("stream"):
            self._json(NON_STREAM)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for frame in sse_frames():
                data = b"data: " + json.dumps(frame).encode() + b"\n\n"
                self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                self.wfile.flush()
                time.sleep(FRAME_DELAY_S if req.get("slow") else 0)
            done = b"data: [DONE]\n\n"
            self.wfile.write(b"%x\r\n%s\r\n0\r\n\r\n" % (len(done), done))
        except OSError:
            FakeLlamaServer.disconnects.append(time.time())

    do_DELETE = do_GET


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_up(port, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1).read()
            return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("proxy did not come up")


def raw_post(port, path, obj):
    """Return (status, content-type, raw body bytes, seconds to first body byte)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    body = json.dumps(obj).encode()
    t0 = time.time()
    conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
    r = conn.getresponse()
    first = None
    parts = []
    while True:
        d = r.read1(65536)
        if not d:
            break
        first = first if first is not None else time.time() - t0
        parts.append(d)
    conn.close()
    return r.status, r.getheader("Content-Type"), b"".join(parts), first


class TraceProxyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.up_port, cls.px_port = free_port(), free_port()
        cls.upstream = http.server.ThreadingHTTPServer(("127.0.0.1", cls.up_port), FakeLlamaServer)
        cls.upstream.daemon_threads = True
        cls.upstream.handle_error = lambda *a: None
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        cls.tmp = tempfile.mkdtemp(prefix="trace-proxy-test-")
        cls.trace_dir = os.path.join(cls.tmp, "traces")
        cls.tee_dir = os.path.join(cls.tmp, "tee")
        cls.proxy = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "trace_proxy.py"), "--listen", f"127.0.0.1:{cls.px_port}",
             "--upstream", f"127.0.0.1:{cls.up_port}", "--trace-dir", cls.trace_dir, "--tee-dir", cls.tee_dir],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait_up(cls.px_port)

    @classmethod
    def tearDownClass(cls):
        cls.proxy.terminate()
        cls.proxy.wait(5)
        cls.upstream.shutdown()

    def trace_for(self, match, timeout=5):
        """The newest trace record that matches; the proxy writes it just after the last byte."""
        t0 = time.time()
        while True:
            hits = [t for t in self.traces() if match(t)]
            if hits or time.time() - t0 > timeout:
                return hits[-1]
            time.sleep(0.02)

    def traces(self):
        if not os.path.isdir(self.trace_dir):
            return []
        out = []
        for name in sorted(os.listdir(self.trace_dir)):
            with open(os.path.join(self.trace_dir, name)) as f:
                out += [json.loads(line) for line in f]
        return out

    def test_non_stream_byte_identical_and_traced(self):
        req = {"model": "gpt-6.1-sol-sub", "messages": [{"role": "user", "content": "When is standup?"}]}
        direct = raw_post(self.up_port, "/v1/chat/completions", req)
        proxied = raw_post(self.px_port, "/v1/chat/completions", req)
        self.assertEqual(direct[:3], proxied[:3])
        rec = self.trace_for(lambda t: t["requested_model"] == "gpt-6.1-sol-sub")
        self.assertEqual(rec["reply_model"], ALIAS)
        self.assertEqual(rec["content"], "Standup is at 10:30.")
        self.assertEqual(rec["reasoning_content"], "The note says it moved.")
        self.assertEqual(rec["finish_reason"], "stop")
        self.assertEqual(rec["messages"], req["messages"])
        self.assertEqual(rec["client"], "127.0.0.1")
        for key in ("request_time", "request_id", "usage", "timings", "done_time"):
            self.assertIn(key, rec)
        with open(os.path.join(self.tee_dir, rec["request_id"] + ".body"), "rb") as f:
            self.assertEqual(f.read(), proxied[2])

    def test_stream_byte_identical_unbuffered_and_traced(self):
        req = {"model": ALIAS, "stream": True, "slow": True, "messages": [{"role": "user", "content": "stream it"}]}
        direct = raw_post(self.up_port, "/v1/chat/completions", req)
        t0 = time.time()
        proxied = raw_post(self.px_port, "/v1/chat/completions", req)
        total = time.time() - t0
        self.assertEqual(direct[:3], proxied[:3])
        self.assertEqual(proxied[1], "text/event-stream")
        # Unbuffered: the first frame arrives long before the last one is sent.
        self.assertLess(proxied[3], FRAME_DELAY_S)
        self.assertGreater(total, 6 * FRAME_DELAY_S)
        models = {json.loads(l[6:])["model"] for l in proxied[2].decode().splitlines()
                  if l.startswith("data: {")}
        self.assertEqual(models, {ALIAS})
        rec = self.trace_for(lambda t: t["messages"][0]["content"] == "stream it")
        self.assertEqual(rec["reply_model"], ALIAS)
        self.assertEqual(rec["reasoning_content"], "The note says the standup moved to 10:30.")
        self.assertEqual(rec["content"], "Standup is at 10:30.")
        self.assertEqual(rec["finish_reason"], "tool_calls")
        self.assertEqual(rec["tool_calls"][0]["function"],
                         {"name": "recall", "arguments": "{\"query\": \"standup\"}"})
        self.assertEqual(rec["stream_frames"], 8)
        self.assertEqual(rec["timings"]["predicted_per_second"], 83.4)
        with open(os.path.join(self.tee_dir, rec["request_id"] + ".body"), "rb") as f:
            self.assertEqual(f.read(), proxied[2])

    def test_other_paths_pass_through_untraced(self):
        n_before = len(self.traces())
        for path in ("/health", "/slots", "/nope"):
            a = urllib.request.Request(f"http://127.0.0.1:{self.up_port}{path}")
            b = urllib.request.Request(f"http://127.0.0.1:{self.px_port}{path}")
            try:
                da = urllib.request.urlopen(a).read()
            except urllib.error.HTTPError as e:
                da = (e.code, e.read())
            try:
                db = urllib.request.urlopen(b).read()
            except urllib.error.HTTPError as e:
                db = (e.code, e.read())
            self.assertEqual(da, db, path)
        save = raw_post(self.px_port, "/slots/0?action=save", {"filename": "user.bin"})
        self.assertEqual(json.loads(save[2])["path"], "/slots/0?action=save")
        self.assertEqual(len(self.traces()), n_before)

    def test_client_disconnect_closes_upstream(self):
        n = len(FakeLlamaServer.disconnects)
        conn = http.client.HTTPConnection("127.0.0.1", self.px_port, timeout=10)
        conn.request("POST", "/v1/chat/completions",
                     body=json.dumps({"stream": True, "slow": True, "messages": [{"role": "user", "content": "bye"}]}),
                     headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        r.read1(100)
        conn.sock.close()
        conn.close()
        t0 = time.time()
        while len(FakeLlamaServer.disconnects) == n and time.time() - t0 < 5:
            time.sleep(0.05)
        self.assertGreater(len(FakeLlamaServer.disconnects), n)
        rec = self.trace_for(lambda t: t["messages"][0]["content"] == "bye")
        self.assertTrue(rec.get("client_disconnected"))


class UpstreamDownTest(unittest.TestCase):
    def test_502_when_upstream_down(self):
        px = free_port()
        with tempfile.TemporaryDirectory() as tmp:
            p = subprocess.Popen([sys.executable, os.path.join(HERE, "trace_proxy.py"), "--listen", f"127.0.0.1:{px}",
                                  "--upstream", f"127.0.0.1:{free_port()}", "--trace-dir", tmp],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                t0 = time.time()
                while True:
                    try:
                        urllib.request.urlopen(f"http://127.0.0.1:{px}/health", timeout=1)
                    except urllib.error.HTTPError as e:
                        self.assertEqual(e.code, 502)
                        break
                    except OSError:
                        if time.time() - t0 > 10:
                            raise
                        time.sleep(0.05)
            finally:
                p.terminate()
                p.wait(5)

    def test_refuses_wildcard_bind(self):
        r = subprocess.run([sys.executable, os.path.join(HERE, "trace_proxy.py"), "--listen", "0.0.0.0:18699"],
                           capture_output=True, text=True, timeout=10)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refusing", r.stderr)


if __name__ == "__main__":
    unittest.main()
