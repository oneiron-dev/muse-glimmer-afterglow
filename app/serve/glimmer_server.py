#!/usr/bin/env python3
"""Start, stop and health-check the Muse Glimmer server on nelly; print its endpoint.

  python3 app/serve/glimmer_server.py start [--lora /data/.../adapter.gguf] [--lora-scale 1] [--hours 14]
  python3 app/serve/glimmer_server.py stop
  python3 app/serve/glimmer_server.py health [--deep]
  python3 app/serve/glimmer_server.py endpoint
  python3 app/serve/glimmer_server.py status

Runs on the Mac. The server runs on nelly in one long fleet GPU booking (serve_on_host.py):
llama-server on 127.0.0.1:18611 behind the trace proxy on the tailnet address 100.96.31.41:18610.
Stdlib only.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = "nelly"
WHO = "meta-day"
GOAL = "glimmer-serve: Muse Glimmer llama-server + trace proxy for Afterglow (day agent, night jobs, Dreamer)"
ENDPOINT = "http://100.96.31.41:18610"
UPSTREAM = "http://127.0.0.1:18611"
ALIAS = "muse-glimmer-30b"
STATE_PATH = "/data/afterglow/serve/current.json"


def http_json(url, body=None, timeout=10):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ssh(cmd, timeout=60):
    return subprocess.run(["ssh", "-o", "ConnectTimeout=10", HOST, cmd], capture_output=True, text=True,
                          timeout=timeout)


def serving_jobs():
    """Running and queued GPU jobs on nelly that are this server."""
    r = ssh("gpu status --json")
    if r.returncode != 0:
        sys.exit(f"gpu status on {HOST} failed: {r.stderr.strip()}")
    gpu = json.loads(r.stdout).get("gpu") or {}
    jobs = [gpu["holder"]] if gpu.get("holder") else []
    jobs += gpu.get("waiting") or []
    return [j for j in jobs if j.get("who") == WHO and (j.get("goal") or "").startswith("glimmer-serve")]


def remote_state():
    r = ssh(f"cat {STATE_PATH}")
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


def check(deep=False):
    """Return (ok, report). Checks the proxy from here and llama-server on nelly's loopback."""
    report = {"endpoint": ENDPOINT}
    try:
        report["proxy_health"] = http_json(f"{ENDPOINT}/health", timeout=5).get("status")
    except (OSError, ValueError) as e:
        report["proxy_health"] = f"down: {e}"
    r = ssh(f"curl -s -m 5 {UPSTREAM}/health")
    report["upstream_health"] = (json.loads(r.stdout).get("status") if r.returncode == 0 and r.stdout.strip()
                                 else f"down: {r.stderr.strip() or r.returncode}")
    ok = report["proxy_health"] == "ok" and report["upstream_health"] == "ok"
    if ok:
        models = [m.get("id") for m in http_json(f"{ENDPOINT}/v1/models").get("data", [])]
        report["v1_models"] = models
        ok = models == [ALIAS]
    if ok and deep:
        # Slot 1 so the user's slot 0 keeps its context. The reply model must be the alias, whatever was asked.
        body = {"model": "any-other-name", "id_slot": 1, "max_tokens": 64, "temperature": 0,
                "messages": [{"role": "user", "content": "Reply with the word ready."}]}
        t0 = time.time()
        reply = http_json(f"{ENDPOINT}/v1/chat/completions", body, timeout=120)
        report["chat"] = {"reply_model": reply.get("model"), "s": round(time.time() - t0, 2),
                          "content": reply["choices"][0]["message"].get("content")}
        req = urllib.request.Request(f"{ENDPOINT}/v1/chat/completions", data=json.dumps(dict(body, stream=True)).encode(),
                                     headers={"Content-Type": "application/json"})
        frames = []
        with urllib.request.urlopen(req, timeout=120) as r:
            for raw in r:
                line = raw.decode().strip()
                if line.startswith("data: {"):
                    frames.append(json.loads(line[6:]).get("model"))
        report["stream"] = {"frames": len(frames), "models": sorted(set(frames), key=str)}
        ok = report["chat"]["reply_model"] == ALIAS and report["stream"]["models"] == [ALIAS]
    return ok, report


def cmd_start(a):
    running = serving_jobs()
    if running:
        sys.exit(f"already booked: {[j.get('job') for j in running]}; run `stop` first")
    inner = ["timeout", "--kill-after=30s", f"{a.hours}h", "python3", "serve_on_host.py"]
    if a.lora:
        inner += ["--lora", a.lora, "--lora-scale", str(a.lora_scale)]
    cmd = ["fleet", "run", "--who", WHO, "--goal", GOAL, "--need", "cuda,gpu-4090", "--host", HOST,
           "--mem", a.mem, "--est", f"{a.hours}h", "--sync", HERE, "--detach", "--"] + inner
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    sys.stdout.write(r.stdout + r.stderr)
    if r.returncode != 0:
        sys.exit(f"fleet run failed ({r.returncode})")
    m = re.search(r"job (\d{4}-\d{6}-[0-9a-f]{4})", r.stdout + r.stderr)
    print(f"job: {m.group(1) if m else '?'}", flush=True)
    t0 = time.time()
    while time.time() - t0 < a.wait:
        ok, report = check()
        if ok:
            ok, report = check(deep=True)
            report["state"] = remote_state()
            print(json.dumps(report, indent=1))
            print(f"READY {ENDPOINT}/v1 (model {ALIAS}) after {time.time() - t0:.0f} s")
            return 0 if ok else 1
        time.sleep(5)
    print(json.dumps(check()[1], indent=1))
    sys.exit(f"not healthy after {a.wait} s; see `status`")


def cmd_stop(a):
    jobs = serving_jobs()
    if not jobs:
        print("no serving job on nelly")
        return 0
    for j in jobs:
        r = ssh(f"gpu stop {j['job']} --why {json.dumps(a.why)}")
        print(f"stop {j['job']}: {(r.stdout + r.stderr).strip()}")
    t0 = time.time()
    while serving_jobs() and time.time() - t0 < 90:
        time.sleep(3)
    left = serving_jobs()
    if left:
        sys.exit(f"still running after 90 s: {[j.get('job') for j in left]}")
    print("stopped")
    return 0


def cmd_health(a):
    ok, report = check(deep=a.deep)
    print(json.dumps(report, indent=1))
    print("OK" if ok else "DOWN")
    return 0 if ok else 1


def cmd_status(a):
    print(json.dumps({"jobs": serving_jobs(), "state": remote_state()}, indent=1))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--lora", help="GGUF LoRA adapter path on nelly")
    s.add_argument("--lora-scale", type=float, default=1.0)
    s.add_argument("--hours", type=int, default=14, help="hard stop of the booking")
    s.add_argument("--mem", default="24G", help="RAM booking on nelly")
    s.add_argument("--wait", type=int, default=900, help="seconds to wait for health (the GPU may be queued)")
    st = sub.add_parser("stop")
    st.add_argument("--why", default="glimmer_server.py stop")
    h = sub.add_parser("health")
    h.add_argument("--deep", action="store_true", help="also a chat and a streamed chat on slot 1 (alias check)")
    sub.add_parser("endpoint")
    sub.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "endpoint":
        print(f"{ENDPOINT}/v1")
        return 0
    return {"start": cmd_start, "stop": cmd_stop, "health": cmd_health, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
