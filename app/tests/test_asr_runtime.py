"""Bounded offline tests for warm ASR managers (fake workers, no model)."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import asr_runtime, asr_worker  # noqa: E402

FAKE_QWEN = r"""
import sys, json, os, time
mode = "echo"
for i, a in enumerate(sys.argv):
    if a == "--model" and i + 1 < len(sys.argv):
        mode = sys.argv[i + 1]
name, _, arg = mode.partition(":")
print(json.dumps({"ready": True, "model": mode}), flush=True)
for raw in sys.stdin:
    if not raw.strip():
        continue
    doc = json.loads(raw)
    if doc.get("shutdown") is True or doc.get("command") == "shutdown":
        break
    if name == "error":
        print(json.dumps({"ok": False, "error": "bad audio (fake)"}), flush=True)
    elif name == "malformed":
        print("not-json", flush=True)
    elif name == "partial":
        sys.stdout.write('{"ok": true')
        sys.stdout.flush()
        time.sleep(10)
    elif name == "crash-once":
        c = arg
        n = 0
        try:
            if os.path.exists(c):
                n = int(open(c).read().strip() or "0")
        except Exception:
            n = 0
        try:
            open(c, "w").write(str(n + 1))
        except Exception:
            pass
        if n == 0:
            sys.exit(1)
        print(json.dumps({"ok": True, "text": "recovered"}), flush=True)
    elif name == "delay":
        time.sleep(float(arg or "0.3"))
        print(json.dumps({"ok": True, "text": f"heard:{doc.get('path')}:lang={doc.get('language')}"}), flush=True)
    else:
        print(json.dumps({"ok": True, "text": f"heard:{doc.get('path')}:lang={doc.get('language')}"}), flush=True)
"""

FAKE_WHISPER = r"""#!/usr/bin/env python3
import sys, os
from http.server import BaseHTTPRequestHandler, HTTPServer
model = host = port = None
argv = sys.argv
i = 0
while i < len(argv):
    a = argv[i]
    if a in ("-m", "--model") and i + 1 < len(argv):
        model = argv[i + 1]
        i += 2
        continue
    if a == "--host" and i + 1 < len(argv):
        host = argv[i + 1]
        i += 2
        continue
    if a == "--port" and i + 1 < len(argv):
        port = int(argv[i + 1])
        i += 2
        continue
    i += 1
pf = os.environ.get("FAKE_PID_FILE")
if pf:
    try:
        open(pf, "w").write(str(os.getpid()))
    except Exception:
        pass
mode = os.environ.get("FAKE_WHISPER_MODE", "normal")

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/health"):
            if mode == "health-503":
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"busy")
            elif mode == "health-404":
                self.send_response(404)
                self.end_headers()
            else:
                body = b'{"status":"ok"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a, **k):
        pass

HTTPServer((host, port), H).serve_forever()
"""


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True


class Base(unittest.TestCase):
    def tmpdir(self):
        t = tempfile.TemporaryDirectory(prefix="asr-test-")
        self.addCleanup(t.cleanup)
        return Path(t.name)

    def fake_qwen(self, tmp):
        p = tmp / "fake_qwen.py"
        p.write_text(FAKE_QWEN)
        return str(p)

    def fake_whisper(self, tmp):
        p = tmp / "fake_whisper.py"
        p.write_text(FAKE_WHISPER)
        p.chmod(0o755)
        return str(p)

    def wav(self, tmp, name="a.wav"):
        p = tmp / name
        p.write_bytes(b"fake")
        return str(p)

    def model_file(self, tmp, name="m.bin"):
        p = tmp / name
        p.write_bytes(b"fake-model")
        return str(p)

    def qwen(self, worker_path, model="echo", **kw):
        kw.setdefault("start_timeout", 5)
        kw.setdefault("transcribe_timeout", 5)
        kw.setdefault("stop_timeout", 2)
        q = asr_runtime.QwenWorker(model=model, python=sys.executable,
                                   worker_path=worker_path, **kw)
        self.addCleanup(q.stop)
        return q


class QwenTest(Base):
    def test_protocol_reuse(self):
        tmp = self.tmpdir()
        q = self.qwen(self.fake_qwen(tmp))
        wav = self.wav(tmp)
        self.assertIn("lang=English", q.transcribe(wav, language="English"))
        pid1 = q.status()["pid"]
        self.assertTrue(q.status()["warm"])
        self.assertIn("lang=None", q.transcribe(wav))
        self.assertEqual(q.status()["pid"], pid1)
        self.assertTrue(pid_alive(pid1))

    def test_partial_line_times_out(self):
        tmp = self.tmpdir()
        q = self.qwen(self.fake_qwen(tmp), model="partial", transcribe_timeout=0.4)
        t0 = time.time()
        with self.assertRaises(RuntimeError):
            q.transcribe(self.wav(tmp))
        self.assertLess(time.time() - t0, 2.0)
        self.assertFalse(q.status()["warm"])

    def test_crash_then_next_restarts(self):
        tmp = self.tmpdir()
        counter = str(tmp / "counter")
        q = self.qwen(self.fake_qwen(tmp), model=f"crash-once:{counter}")
        with self.assertRaises(RuntimeError):
            q.transcribe(self.wav(tmp))
        self.assertEqual(q.transcribe(self.wav(tmp)), "recovered")
        self.assertTrue(q.status()["warm"])
        self.assertTrue(pid_alive(q.status()["pid"]))

    def test_bad_json_kills_error_stays_warm(self):
        tmp = self.tmpdir()
        q = self.qwen(self.fake_qwen(tmp), model="malformed")
        with self.assertRaises(RuntimeError):
            q.transcribe(self.wav(tmp))
        self.assertFalse(q.status()["warm"])
        q2 = self.qwen(self.fake_qwen(tmp), model="error")
        with self.assertRaisesRegex(RuntimeError, "bad audio"):
            q2.transcribe(self.wav(tmp))
        pid = q2.status()["pid"]
        self.assertTrue(q2.status()["warm"])
        with self.assertRaises(RuntimeError):
            q2.transcribe(self.wav(tmp))
        self.assertEqual(q2.status()["pid"], pid)

    def test_serialized_calls(self):
        tmp = self.tmpdir()
        q = self.qwen(self.fake_qwen(tmp), model="delay:0.3")
        wav = self.wav(tmp)
        bar = threading.Barrier(2)
        out = []

        def one():
            bar.wait()
            out.append(q.transcribe(wav))

        ts = [threading.Thread(target=one) for _ in range(2)]
        t0 = time.time()
        [t.start() for t in ts]
        [t.join(timeout=10) for t in ts]
        self.assertGreaterEqual(time.time() - t0, 0.5)
        self.assertEqual(len(out), 2)

    def test_status_never_raises_nor_spawns(self):
        q = asr_runtime.QwenWorker(python="/nonexistent/python",
                                   worker_path="/nonexistent/worker.py")
        self.addCleanup(q.stop)
        self.assertEqual(q.status()["warm"], False)
        self.assertIsNone(q.status()["pid"])
        self.assertIsNone(q._proc)
        with self.assertRaises(RuntimeError):
            q.transcribe("/nonexistent/a.wav")

    def test_no_mlx_import(self):
        self.assertNotIn("mlx_audio", sys.modules)
        self.assertNotIn("mlx", sys.modules)

    def test_parent_watcher_stays_alive(self):
        t = asr_worker.start_parent_watcher(os.getppid(), interval=0.05)
        self.assertTrue(t.daemon)
        time.sleep(0.2)
        self.assertTrue(t.is_alive())

    def test_parent_watcher_death_exits_mid_sleep(self):
        tmp = self.tmpdir()
        child = tmp / "watchdie.py"
        child.write_text(
            "import sys, time; sys.path.insert(0, sys.argv[1]);"
            "from afterglow import asr_worker;"
            "asr_worker.start_parent_watcher(999999, interval=0.05);"
            "time.sleep(10)")
        app_root = str(Path(__file__).resolve().parents[1])
        t0 = time.time()
        r = subprocess.run([sys.executable, str(child), app_root],
                           capture_output=True, text=True, timeout=15)
        self.assertLess(time.time() - t0, 2.0)
        self.assertEqual(r.returncode, 0)

    def test_atexit_kills_qwen_child(self):
        tmp = self.tmpdir()
        fw = self.fake_qwen(tmp)
        wav = self.wav(tmp)
        child = tmp / "child.py"
        child.write_text(
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from afterglow import asr_runtime;"
            "q=asr_runtime.QwenWorker(model='echo', python=sys.executable,"
            f" worker_path={fw!r}, start_timeout=5, transcribe_timeout=5);"
            f"q.transcribe({wav!r}); print(q.status()['pid'], flush=True)")
        app_root = str(Path(__file__).resolve().parents[1])
        r = subprocess.run([sys.executable, str(child), app_root],
                           capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)
        pid = int(r.stdout.strip())
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and pid_alive(pid):
            time.sleep(0.05)
        self.assertFalse(pid_alive(pid))

    def test_atexit_does_not_block_on_stuck_transcribe(self):
        tmp = self.tmpdir()
        fw = self.fake_qwen(tmp)
        wav = self.wav(tmp)
        child = tmp / "stuck.py"
        child.write_text(
            "import sys, threading, time\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from afterglow import asr_runtime\n"
            f"q=asr_runtime.QwenWorker(model='delay:10', python=sys.executable, worker_path={fw!r}, start_timeout=5, transcribe_timeout=30)\n"
            f"t=threading.Thread(target=q.transcribe, args=({wav!r},), daemon=True)\n"
            "t.start()\n"
            "deadline=time.monotonic()+5\n"
            "while time.monotonic()<deadline and not q.status()['warm']:\n"
            "    time.sleep(0.05)\n"
            "print(q.status()['pid'], flush=True)\n")
        app_root = str(Path(__file__).resolve().parents[1])
        t0 = time.time()
        r = subprocess.run([sys.executable, str(child), app_root],
                           capture_output=True, text=True, timeout=15)
        self.assertLess(time.time() - t0, 5.0)
        self.assertEqual(r.returncode, 0, r.stderr)
        pid = int(r.stdout.strip().split()[0])
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and pid_alive(pid):
            time.sleep(0.05)
        self.assertFalse(pid_alive(pid))


class WhisperTest(Base):
    def test_reuse_and_model_switch(self):
        tmp = self.tmpdir()
        w = asr_runtime.WhisperServer(binary=self.fake_whisper(tmp),
                                      start_timeout=10, stop_timeout=2)
        self.addCleanup(w.stop)
        m1, m2 = self.model_file(tmp, "m1.bin"), self.model_file(tmp, "m2.bin")
        url1 = w.ensure_started(m1)
        self.assertTrue(url1.startswith("http://127.0.0.1:"))
        pid1 = w.status(m1)["pid"]
        self.assertTrue(w.status(m1)["warm"])
        self.assertEqual(w.ensure_started(m1), url1)
        self.assertEqual(w.status(m1)["pid"], pid1)
        self.assertFalse(w.status(m2)["warm"])
        url2 = w.ensure_started(m2)
        pid2 = w.status(m2)["pid"]
        self.assertNotEqual(pid2, pid1)
        self.assertTrue(w.status(m2)["warm"])
        self.assertFalse(pid_alive(pid1))
        w.stop()
        self.assertFalse(w.status(m2)["warm"])
        with self.assertRaises(RuntimeError):
            w.ensure_started(str(tmp / "missing.bin"))

    def test_health_404_ready_503_not(self):
        tmp = self.tmpdir()
        w = asr_runtime.WhisperServer(binary=self.fake_whisper(tmp),
                                      start_timeout=5, stop_timeout=2)
        self.addCleanup(w.stop)
        m = self.model_file(tmp)
        with mock.patch.dict(os.environ, {"FAKE_WHISPER_MODE": "health-404"}):
            url = w.ensure_started(m)
        self.assertTrue(w.status(m)["warm"])
        w.stop()
        w2 = asr_runtime.WhisperServer(binary=self.fake_whisper(tmp),
                                       start_timeout=1, stop_timeout=2)
        self.addCleanup(w2.stop)
        with mock.patch.dict(os.environ, {"FAKE_WHISPER_MODE": "health-503"}):
            t0 = time.time()
            with self.assertRaises(RuntimeError):
                w2.ensure_started(m)
            self.assertLess(time.time() - t0, 5.0)
        self.assertFalse(w2.status(m)["warm"])

    def test_wrapper_parent_death_kills_server(self):
        tmp = self.tmpdir()
        fw = self.fake_whisper(tmp)
        m = self.model_file(tmp)
        pidfile = str(tmp / "srv.pid")
        child = tmp / "wchild.py"
        child.write_text(
            "import sys, os; sys.path.insert(0, sys.argv[1]);"
            "from afterglow import asr_runtime;"
            f"os.environ['FAKE_PID_FILE']={pidfile!r};"
            "w=asr_runtime.WhisperServer("
            f"binary={fw!r}, start_timeout=10);"
            f"url=w.ensure_started({m!r});"
            f"print(w.status()['pid'], open({pidfile!r}).read().strip(), flush=True);"
            "os._exit(0)")
        app_root = str(Path(__file__).resolve().parents[1])
        r = subprocess.run([sys.executable, str(child), app_root],
                           capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)
        wrap_pid, srv_pid = (int(x) for x in r.stdout.strip().split())
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and (pid_alive(wrap_pid) or pid_alive(srv_pid)):
            time.sleep(0.05)
        self.assertFalse(pid_alive(wrap_pid))
        self.assertFalse(pid_alive(srv_pid))


if __name__ == "__main__":
    unittest.main()
