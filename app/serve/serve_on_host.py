#!/usr/bin/env python3
"""Run Muse Glimmer llama-server and the trace proxy on the serving box (nelly), inside one GPU booking.

llama-server binds 127.0.0.1:18611 (loopback only). The trace proxy binds the tailnet address
100.96.31.41:18610 and is the public endpoint. If either process exits, the other is stopped
and this script exits, so the booking ends and the health check fails. SIGTERM stops both.

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
LLAMA_BIN = "/data/minicpm-v47-20261013/src/llama.cpp/build/bin/llama-server"
GGUF_DIR = "/data/drafter-tune-20261014/models/official-gguf"
MODEL = f"{GGUF_DIR}/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf"
DRAFTER = f"{GGUF_DIR}/dflash-Muse-Glimmer-30B-Q4_K_M.gguf"
# Pinned sha256 values (lead/verify_weights.sh, checked on nelly 26 Oct 20:51 JST).
MODEL_SHA256 = "4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e"
DRAFTER_SHA256 = "b2e808bf656086fe86bd0d0bd990f01d33e377537a07c02d45371517c8b264ef"
ALIAS = "muse-glimmer-30b"
TEMPLATE_KWARGS = {"reasoning_strength": "minimal"}


def now_iso():
    return dt.datetime.now(JST).isoformat(timespec="seconds")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def llama_command(a):
    cmd = [LLAMA_BIN, "-m", MODEL, "--alias", ALIAS,
           "-ngl", "99", "-fa", "on", "-np", str(a.slots), "-c", str(a.ctx), "--jinja", "--metrics",
           "--chat-template-kwargs", json.dumps(TEMPLATE_KWARGS, separators=(",", ":")),
           "-md", DRAFTER, "-ngld", "all",
           "--spec-type", "draft-dflash", "--spec-draft-n-max", "15", "--spec-draft-n-min", "0",
           "--spec-draft-p-min", "0", "--spec-draft-sampling", "greedy",
           "--slot-save-path", os.path.join(a.root, "slots"),
           "--host", "127.0.0.1", "--port", str(a.upstream_port)]
    if a.lora:
        # Adapter at its default scale; clients still send a per-request "lora" on every call.
        # The RAM prompt cache is not adapter-aware (RESULT-LLAMACPP.md section 5b), so it is off with an adapter.
        cmd += ["--lora-scaled", f"{a.lora}:{a.lora_scale}", "--cache-ram", "0"]
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
        lora = None
        if a.lora:
            lora = {"path": a.lora, "sha256": sha256_file(a.lora), "scale": a.lora_scale}
        cmd = llama_command(a)
        self.write_state(state="starting", started=now_iso(),
                         endpoint=f"http://{a.listen}", alias=ALIAS, chat_template_kwargs=TEMPLATE_KWARGS,
                         slots=a.slots, ctx=a.ctx,
                         model={"path": MODEL, "sha256": MODEL_SHA256},
                         drafter={"path": DRAFTER, "sha256": DRAFTER_SHA256},
                         lora=lora, slot_save_path=os.path.join(a.root, "slots"),
                         trace_dir=os.path.join(a.root, "traces"), llama_cmd=cmd,
                         llama_log=f"{logs}/{stamp}-llama-server.log", proxy_log=f"{logs}/{stamp}-trace-proxy.log")
        env = dict(os.environ, LD_LIBRARY_PATH=os.path.dirname(LLAMA_BIN))
        self.procs["llama-server"] = subprocess.Popen(
            cmd, stdout=open(self.state["llama_log"], "w"), stderr=subprocess.STDOUT, env=env)
        self.procs["trace-proxy"] = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "trace_proxy.py"), "--listen", a.listen,
             "--upstream", f"127.0.0.1:{a.upstream_port}", "--trace-dir", os.path.join(a.root, "traces")],
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
    ap.add_argument("--listen", default="100.96.31.41:18610", help="trace proxy HOST:PORT (tailnet address)")
    ap.add_argument("--upstream-port", type=int, default=18611, help="llama-server port on 127.0.0.1")
    ap.add_argument("--slots", type=int, default=2)
    ap.add_argument("--ctx", type=int, default=65536, help="total context; each slot gets ctx/slots")
    ap.add_argument("--root", default="/data/afterglow")
    ap.add_argument("--lora", default=None, help="GGUF LoRA adapter path on this host")
    ap.add_argument("--lora-scale", type=float, default=1.0)
    a = ap.parse_args()

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
