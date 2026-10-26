#!/usr/bin/env python3
"""Prove the trace proxy is byte-transparent against the real llama-server (run on nelly).

Starts a second, temporary proxy on 127.0.0.1:18612 that also writes each raw upstream body
to a tee dir, sends one non-streamed and one streamed chat completion through it (slot 1),
and checks: bytes the client got == bytes llama-server sent, every SSE frame names the alias,
and a trace record exists with reasoning and content. Prints one JSON report; exit 0 = pass.
"""
import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UPSTREAM = "127.0.0.1:18611"
PORT = 18612
ALIAS = "muse-glimmer-30b"


def post_raw(body):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=300)
    conn.request("POST", "/v1/chat/completions", body=json.dumps(body), headers={"Content-Type": "application/json"})
    r = conn.getresponse()
    parts = []
    while True:
        d = r.read1(65536)
        if not d:
            break
        parts.append(d)
    conn.close()
    return r.status, b"".join(parts)


def main():
    tmp = tempfile.mkdtemp(prefix="afterglow-passthrough-")
    trace_dir, tee_dir = os.path.join(tmp, "traces"), os.path.join(tmp, "tee")
    proxy = subprocess.Popen([sys.executable, os.path.join(HERE, "trace_proxy.py"), "--listen", f"127.0.0.1:{PORT}",
                              "--upstream", UPSTREAM, "--trace-dir", trace_dir, "--tee-dir", tee_dir],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report = {"tmp": tmp}
    ok = True
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2).read()
                break
            except OSError:
                time.sleep(0.1)
        msgs = [{"role": "user", "content": "The standup moved from 09:30 to 10:30. When is standup now? One line."}]
        for name, stream in (("non_stream", False), ("stream", True)):
            body = {"model": ALIAS, "messages": msgs, "max_tokens": 200, "temperature": 0, "id_slot": 1,
                    "stream": stream, "chat_template_kwargs": {"reasoning_strength": "low"}}
            status, got = post_raw(body)
            time.sleep(0.3)
            recs = []
            for fn in os.listdir(trace_dir):
                with open(os.path.join(trace_dir, fn)) as f:
                    recs += [json.loads(line) for line in f]
            rec = [r for r in recs if r.get("stream") == stream or (not stream and "stream" not in r)][-1]
            with open(os.path.join(tee_dir, rec["request_id"] + ".body"), "rb") as f:
                sent = f.read()
            if stream:
                models = sorted({json.loads(l[6:]).get("model") for l in got.decode().splitlines()
                                 if l.startswith("data: {")}, key=str)
            else:
                models = [json.loads(got).get("model")]
            item = {"status": status, "bytes": len(got), "byte_identical": got == sent, "frame_models": models,
                    "trace_reply_model": rec.get("reply_model"), "trace_finish": rec.get("finish_reason"),
                    "trace_reasoning_chars": len(rec.get("reasoning_content") or ""),
                    "trace_content": rec.get("content")}
            report[name] = item
            ok = ok and status == 200 and got == sent and models == [ALIAS] and rec.get("reply_model") == ALIAS
    finally:
        proxy.terminate()
        proxy.wait(10)
    report["pass"] = ok
    print(json.dumps(report, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
