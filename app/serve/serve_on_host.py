#!/usr/bin/env python3
"""Run Muse Glimmer llama-server and the trace proxy on a serving box, inside one GPU booking.

llama-server binds 127.0.0.1:18611 (loopback only). The trace proxy binds the box's tailnet address
on port 18610 and is the public endpoint. Paths per box are in hosts.json. If either process exits,
the other is stopped and this script exits, so the booking ends and the health check fails.
SIGTERM stops both.

State for clients: <root>/serve/current.json (weights, drafter, adapter, flags, state).
Python stdlib only. Started by glimmer_server.py on the Mac through `fleet run`.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
JST = dt.timezone(dt.timedelta(hours=9))
with open(os.path.join(HERE, "hosts.json")) as _f:
    CONF = json.load(_f)
# Pinned sha256 values (lead/verify_weights.sh: nelly 26 Oct 20:51 JST; mel copies 27 Oct 05:04 JST).
MODEL_SHA256 = CONF["model_sha256"]
DRAFTER_SHA256 = CONF["drafter_sha256"]
ALIAS = CONF["alias"]
TEMPLATE_KWARGS = {"reasoning_strength": "minimal"}


def now_iso():
    return dt.datetime.now(JST).isoformat(timespec="seconds")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def host_paths(name):
    h = CONF["hosts"][name]
    return {"llama_server": h["llama_server"], "model": os.path.join(h["gguf_dir"], CONF["model_file"]),
            "drafter": os.path.join(h["gguf_dir"], CONF["drafter_file"]),
            "listen": f"{h['tailnet']}:{CONF['proxy_port']}"}


def llama_server_path(a):
    return a.llama_server or host_paths(a.host)["llama_server"]


def llama_command(a):
    p = host_paths(a.host)
    cmd = [llama_server_path(a), "-m", p["model"], "--alias", ALIAS,
           "-ngl", "99", "-fa", "on", "-np", str(a.slots), "-c", str(a.ctx), "--jinja", "--metrics",
           "--chat-template-kwargs", json.dumps(TEMPLATE_KWARGS, separators=(",", ":")),
           "-md", p["drafter"], "-ngld", "all",
           "--spec-type", "draft-dflash", "--spec-draft-n-max", "15", "--spec-draft-n-min", "0",
           "--spec-draft-p-min", "0", "--spec-draft-sampling", "greedy",
           "--slot-save-path", os.path.join(a.root, "slots"),
           "--host", "127.0.0.1", "--port", str(a.upstream_port)]
    if a.lora:
        # One adapter per world (ids in the order given), loaded at scale 0: every client sends its scales per
        # request. A request without scales runs at the loaded scale (plain --lora would be 1.0 for every
        # adapter at once), so they load with --lora-scaled at 0. The RAM prompt cache is not adapter-aware
        # (RESULT-LLAMACPP.md section 5b), so it is off.
        for path in a.lora:
            cmd += ["--lora-scaled", f"{path}:0"]
        cmd += ["--lora-init-without-apply", "--cache-ram", "0"]
    if a.control_vector:
        # Steering dials (a build with per-request "control_vectors"), loaded at scale 0 in the order given: a
        # request that names none runs without them, so no proxy fill is needed for vectors.
        cmd += ["--control-vector-scaled", ",".join(f"{path}:0" for path in a.control_vector)]
        if a.control_vector_layer_range:  # one range for every vector (the dial files hold layers that break replies)
            cmd += ["--control-vector-layer-range", *map(str, a.control_vector_layer_range)]
    return cmd


class Host:
    def __init__(self, a):
        self.a = a
        self.state_path = os.path.join(a.root, "serve", "current.json")
        self.procs = {}
        self.state = {}
        self.stopping = False

    def write_state(self, **kw):
        self.state.update(kw, updated=now_iso())
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.state_path)

    def start(self):
        a = self.a
        for sub in ("slots", "traces", "serve/logs"):
            os.makedirs(os.path.join(a.root, sub), exist_ok=True)
        stamp = dt.datetime.now(JST).strftime("%Y%m%d-%H%M%S")
        logs = os.path.join(a.root, "serve", "logs")
        lora = [{"id": i, "path": path, "sha256": sha256_file(path)} for i, path in enumerate(a.lora or [])]
        vectors = [{"id": i, "path": path, "sha256": sha256_file(path), "layer_range": a.control_vector_layer_range}
                   for i, path in enumerate(a.control_vector or [])]
        cmd = llama_command(a)
        self.write_state(state="starting", started=now_iso(),
                         endpoint=f"http://{a.listen}", alias=ALIAS, chat_template_kwargs=TEMPLATE_KWARGS,
                         slots=a.slots, ctx=a.ctx,
                         host=a.host,
                         model={"path": host_paths(a.host)["model"], "sha256": MODEL_SHA256},
                         drafter={"path": host_paths(a.host)["drafter"], "sha256": DRAFTER_SHA256},
                         lora=lora, control_vectors=vectors, slot_save_path=os.path.join(a.root, "slots"),
                         trace_dir=os.path.join(a.root, "traces"), llama_cmd=cmd,
                         llama_log=f"{logs}/{stamp}-llama-server.log", proxy_log=f"{logs}/{stamp}-trace-proxy.log")
        env = dict(os.environ, LD_LIBRARY_PATH=os.path.dirname(llama_server_path(a)))
        self.procs["llama-server"] = subprocess.Popen(
            cmd, stdout=open(self.state["llama_log"], "w"), stderr=subprocess.STDOUT, env=env)
        self.procs["trace-proxy"] = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "trace_proxy.py"), "--listen", a.listen,
             "--upstream", f"127.0.0.1:{a.upstream_port}", "--trace-dir", os.path.join(a.root, "traces"),
             "--lora-count", str(len(a.lora or []))],
            stdout=open(self.state["proxy_log"], "w"), stderr=subprocess.STDOUT)
        self.write_state(pids={k: p.pid for k, p in self.procs.items()})
        print(f"{now_iso()} started llama-server pid {self.procs['llama-server'].pid}, "
              f"trace proxy pid {self.procs['trace-proxy'].pid}", flush=True)

    def stop(self, why):
        if self.stopping:
            return
        self.stopping = True
        for p in self.procs.values():
            if p.poll() is None:
                p.terminate()
        deadline = time.time() + 20
        for p in self.procs.values():
            try:
                p.wait(max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        self.write_state(state="stopped", stopped=now_iso(), why=why,
                         exits={k: p.returncode for k, p in self.procs.items()})
        print(f"{now_iso()} stopped: {why}", flush=True)

    def healthy(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.a.upstream_port}/health", timeout=3) as r:
                upstream = json.loads(r.read()).get("status") == "ok"
            with urllib.request.urlopen(f"http://{self.a.listen}/health", timeout=3) as r:
                proxied = json.loads(r.read()).get("status") == "ok"
            return upstream and proxied
        except (OSError, ValueError):
            return False

    def supervise(self):
        t0 = time.time()
        ready = False
        while True:
            for name, p in self.procs.items():
                if p.poll() is not None:
                    self.stop(f"{name} exited with {p.returncode}")
                    return 1
            if not ready and self.healthy():
                ready = True
                self.write_state(state="ready", ready=now_iso(), ready_s=round(time.time() - t0, 1))
                print(f"{now_iso()} ready in {time.time() - t0:.1f} s at http://{self.a.listen}", flush=True)
            time.sleep(2)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default=CONF["default"], choices=sorted(CONF["hosts"]), help="this box (hosts.json)")
    ap.add_argument("--listen", default=None, help="trace proxy HOST:PORT (default: the box's tailnet address)")
    ap.add_argument("--upstream-port", type=int, default=CONF["upstream_port"], help="llama-server port on 127.0.0.1")
    ap.add_argument("--slots", type=int, default=2)
    ap.add_argument("--ctx", type=int, default=65536, help="total context; each slot gets ctx/slots")
    ap.add_argument("--root", default=CONF["root"])
    ap.add_argument("--llama-server", default=None, help="llama-server binary (default: the box's in hosts.json)")
    ap.add_argument("--lora", action="append", help="GGUF LoRA adapter on this host; repeat, ids in the order given")
    ap.add_argument("--control-vector", action="append",
                    help="GGUF control vector (a steering dial) on this host, loaded at scale 0; repeat, ids in the order "
                         "given; needs a llama-server built with per-request control_vectors")
    ap.add_argument("--control-vector-layer-range", type=int, nargs=2, metavar=("START", "END"),
                    help="layers the control vectors apply to (one range for all)")
    ap.add_argument("--print-command", action="store_true", help="print the llama-server command and exit")
    a = ap.parse_args()
    a.listen = a.listen or host_paths(a.host)["listen"]
    if a.print_command:
        print(" ".join(llama_command(a)))
        return

    host = Host(a)

    def on_signal(signum, _frame):
        host.stop(f"signal {signal.Signals(signum).name}")
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    host.start()
    sys.exit(host.supervise())


if __name__ == "__main__":
    main()
