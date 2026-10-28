#!/usr/bin/env python3
"""Start, stop and health-check the Muse Glimmer server; print its endpoint.

  python3 app/serve/glimmer_server.py [--host mel|nelly] start [--adapters runs/serving/adapters.json] [--hours 14] [--dry-run]
  python3 app/serve/glimmer_server.py [--host mel|nelly] stop
  python3 app/serve/glimmer_server.py [--host mel|nelly] health [--deep]
  python3 app/serve/glimmer_server.py [--host mel|nelly] endpoint
  python3 app/serve/glimmer_server.py [--host mel|nelly] status

Runs on the Mac. The server runs on the serving box (hosts.json; default mel) in one long GPU
booking (serve_on_host.py): llama-server on 127.0.0.1:18611 behind the trace proxy on the box's
tailnet address, port 18610. The booking goes through `fleet run`; when the box is fleet-OFF
(serving has it to itself), this script copies app/serve there and books with the box's own
`gpu run`. Stdlib only.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "hosts.json")) as _f:
    CONF = json.load(_f)
WHO = "meta-day"
GOAL = "glimmer-serve: Muse Glimmer llama-server + trace proxy for Afterglow (day agent, night jobs, Dreamer)"
ALIAS = CONF["alias"]
UPSTREAM = f"http://127.0.0.1:{CONF['upstream_port']}"
STATE_PATH = f"{CONF['root']}/serve/current.json"
SYNC_DIR = f"/data/fleet-work/sync/{WHO}/serve"  # where `fleet run --sync` puts app/serve
HOST = ENDPOINT = None


def configure(host):
    global HOST, ENDPOINT
    HOST = host
    ENDPOINT = f"http://{CONF['hosts'][host]['tailnet']}:{CONF['proxy_port']}"


def http_json(url, body=None, timeout=10):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ssh(cmd, timeout=60):
    return subprocess.run(["ssh", "-o", "ConnectTimeout=10", HOST, cmd], capture_output=True, text=True,
                          timeout=timeout)


def serving_jobs():
    """Running and queued GPU jobs on the serving box that are this server."""
    r = ssh("gpu status --json")
    if r.returncode != 0:
        sys.exit(f"gpu status on {HOST} failed: {r.stderr.strip()}")
    gpu = json.loads(r.stdout).get("gpu") or {}
    jobs = [gpu["holder"]] if gpu.get("holder") else []
    jobs += gpu.get("waiting") or []
    return [j for j in jobs if j.get("who") == WHO and (j.get("goal") or "").startswith("glimmer-serve")]


def fleet_off():
    r = ssh("gpu status")
    return r.returncode == 0 and "FLEET OFF" in r.stdout


def remote_state():
    r = ssh(f"cat {STATE_PATH}")
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


def check(deep=False):
    """Return (ok, report). Checks the proxy from here and llama-server on the box's loopback."""
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


def adapter_args(manifest_path):
    """serve_on_host.py flags from the night job's serving manifest: its llama-server build and the adapters,
    one --lora per adapter in ascending id (llama-server numbers them in the order given).

    The manifest (runs/serving/adapters.json) lists every adapter the server loads:
      {"llama_server": "/data/.../llama-server", "llama_sha256": "...",
       "adapters": [{"id": 0, "path": "/data/.../adapter.gguf", "sha256": "...",
                     "world": "w1-ines-varga", "day": 1, "decision": "keep"}, ...]}
    ids are 0..n-1; paths are absolute and unique (the app finds a world's adapter by its full path). An entry
    without "world" (a decision adapter, say) is loaded too; the day agent sends it scale 0."""
    with open(manifest_path) as f:
        m = json.load(f)
    if m.get("serving_host", HOST) != HOST:
        sys.exit(f"{manifest_path}: written for {m['serving_host']}, not {HOST}; its paths are staged there")
    adapters = m.get("adapters")
    if not isinstance(adapters, list) or not all(isinstance(x.get("id"), int) and str(x.get("path", "")).startswith("/")
                                                 for x in adapters):
        sys.exit(f"{manifest_path}: every adapter needs an int id and an absolute path")
    adapters = sorted(adapters, key=lambda x: x["id"])
    if [x["id"] for x in adapters] != list(range(len(adapters))):
        sys.exit(f"{manifest_path}: adapter ids must be 0..n-1 without gaps, got {[x['id'] for x in adapters]}")
    if len({x["path"] for x in adapters}) != len(adapters):
        sys.exit(f"{manifest_path}: an adapter path is listed twice")
    if any(c in x["path"] for x in adapters for c in ":,"):
        sys.exit(f"{manifest_path}: adapter paths cannot hold ':' or ',' (llama-server's --lora-scaled FNAME:SCALE)")
    for x in adapters:
        print(f"  lora {x['id']}: {x.get('world') or '(no world)'} day {x.get('day', '?')} "
              f"{x.get('decision', '')} {x['path']}", flush=True)
    out = ["--llama-server", m["llama_server"]] if m.get("llama_server") else []
    for x in adapters:
        out += ["--lora", x["path"]]
    # Steering dials, for a build with per-request control_vectors: {"control_vectors": [{"id", "path", "sha256",
    # "axis", "toward"}]}, ids 0..n-1 in load order, all loaded at scale 0.
    vectors = sorted(m.get("control_vectors") or [], key=lambda x: x["id"])
    if [x["id"] for x in vectors] != list(range(len(vectors))) or not all(str(x.get("path", "")).startswith("/")
                                                                          for x in vectors):
        sys.exit(f"{manifest_path}: control vector ids must be 0..n-1 with absolute paths")
    for x in vectors:
        print(f"  control vector {x['id']}: {x.get('axis', '?')} toward {x.get('toward', '?')} {x['path']}", flush=True)
        out += ["--control-vector", x["path"]]
    return out


def cmd_start(a):
    running = serving_jobs()
    if running and not a.dry_run:
        sys.exit(f"already booked: {[j.get('job') for j in running]}; run `stop` first")
    inner = ["timeout", "--kill-after=30s", f"{a.hours}h", "python3", "serve_on_host.py", "--host", HOST]
    if a.adapters:
        inner += adapter_args(a.adapters)
    mem = a.mem or CONF["hosts"][HOST]["mem"]
    if fleet_off():
        # The box takes no fleet jobs; serving has it to itself. Copy app/serve and book with its own gpu run.
        r = subprocess.CompletedProcess([], 0) if a.dry_run else subprocess.run(["rsync", "-a", "--delete", "--exclude", "__pycache__", HERE + "/", f"{HOST}:{SYNC_DIR}/"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            sys.exit(f"rsync to {HOST} failed: {r.stderr.strip()}")
        remote = (f"cd {SYNC_DIR} && gpu run --who {WHO} --goal {shlex.quote(GOAL)} --mem {mem} "
                  f"--est {a.hours}h --detach -- " + " ".join(shlex.quote(x) for x in inner))
        cmd = ["ssh", "-o", "ConnectTimeout=10", HOST, remote]
    else:
        cmd = ["fleet", "run", "--who", WHO, "--goal", GOAL, "--need", "cuda,gpu-4090", "--host", HOST,
               "--mem", mem, "--est", f"{a.hours}h", "--sync", HERE, "--detach", "--"] + inner
    print("+", " ".join(cmd), flush=True)
    if a.dry_run:
        r = subprocess.run([sys.executable, os.path.join(HERE, "serve_on_host.py"), "--print-command"] + inner[5:],
                           capture_output=True, text=True, timeout=30)
        print("llama-server command:", (r.stdout + r.stderr).strip())
        return 0
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    sys.stdout.write(r.stdout + r.stderr)
    if r.returncode != 0:
        sys.exit(f"booking failed ({r.returncode})")
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
        print(f"no serving job on {HOST}")
        return 0
    for j in jobs:
        r = ssh(f"gpu stop {j['job']} --why {json.dumps(a.why)}")
        print(f"stop {j['job']}: {(r.stdout + r.stderr).strip()}")
    t0 = time.time()
    while serving_jobs() and time.time() - t0 < 90:
        time.sleep(3)
    if serving_jobs():
        # `gpu stop` can take a running job for one that has not started and defer the stop. The booking's
        # command is serve_on_host.py, which stops llama-server and the proxy on SIGTERM; its exit ends the job.
        r = ssh("pkill -TERM -f '^python3 serve_on_host.py'")
        print(f"gpu stop left the job running; SIGTERM to serve_on_host.py (pkill exit {r.returncode})", flush=True)
        t0 = time.time()
        while serving_jobs() and time.time() - t0 < 60:
            time.sleep(3)
    left = serving_jobs()
    if left:
        sys.exit(f"still running: {[j.get('job') for j in left]}")
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
    ap.add_argument("--host", default=CONF["default"], choices=sorted(CONF["hosts"]), help="serving box")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--adapters", help="serving manifest from the night job (runs/serving/adapters.json): "
                                      "its llama-server build and kept adapters")
    s.add_argument("--dry-run", action="store_true", help="print the booking and llama-server commands only")
    s.add_argument("--hours", type=int, default=14, help="hard stop of the booking")
    s.add_argument("--mem", default=None, help="RAM booking (default per box in hosts.json)")
    s.add_argument("--wait", type=int, default=900, help="seconds to wait for health (the GPU may be queued)")
    st = sub.add_parser("stop")
    st.add_argument("--why", default="glimmer_server.py stop")
    h = sub.add_parser("health")
    h.add_argument("--deep", action="store_true", help="also a chat and a streamed chat on slot 1 (alias check)")
    sub.add_parser("endpoint")
    sub.add_parser("status")
    a = ap.parse_args()
    configure(a.host)
    if a.cmd == "endpoint":
        print(f"{ENDPOINT}/v1")
        return 0
    return {"start": cmd_start, "stop": cmd_stop, "health": cmd_health, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
