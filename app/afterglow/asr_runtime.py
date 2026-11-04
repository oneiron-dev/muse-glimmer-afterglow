"""Warm ASR: persistent Qwen worker and owned whisper-server.

Stdlib only, no MLX. Import/constructors spawn nothing. Lock-free atexit
cleanup plus parent watchers cover exit and SIGTERM/SIGKILL.
Failures are RuntimeError only. status() never raises, spawns, or blocks.
"""
import atexit
import json
import os
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ASR_MODEL = "mlx-community/Qwen3-ASR-1.7B-8bit"
ASR_PYTHON = Path("/Users/olety/.cache/afterglow/asr-venv/bin/python")


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True


def _readline(proc, buf, timeout):
    deadline = time.monotonic() + timeout
    fd = proc.stdout.fileno()
    while True:
        i = buf.find(b"\n")
        if i >= 0:
            line = bytes(buf[:i]).decode("utf-8", errors="replace")
            del buf[:i + 1]
            if line.strip():
                return line
            continue
        left = deadline - time.monotonic()
        if left <= 0:
            if proc.poll() is not None:
                raise RuntimeError("worker died waiting for reply")
            raise RuntimeError("worker timed out waiting for reply")
        try:
            r, _, _ = select.select([proc.stdout], [], [], left)
        except (ValueError, OSError):
            raise RuntimeError("worker died waiting for reply")
        if not r:
            if proc.poll() is not None:
                raise RuntimeError("worker died waiting for reply")
            raise RuntimeError("worker timed out waiting for reply")
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            raise RuntimeError("worker died waiting for reply")
        if not chunk:
            raise RuntimeError("worker died waiting for reply")
        buf.extend(chunk)


def _kill(proc):
    try:
        proc.terminate()
        proc.wait(timeout=2)
        return
    except Exception:
        pass
    try:
        proc.kill()
        proc.wait(timeout=2)
    except Exception:
        pass


def _close(p):
    try:
        if p.stdin:
            p.stdin.close()
    except Exception:
        pass
    try:
        if p.stdout:
            p.stdout.close()
    except Exception:
        pass


class QwenWorker:
    def __init__(self, model=ASR_MODEL, python=ASR_PYTHON, worker_path=None,
                 start_timeout=180.0, transcribe_timeout=120.0, stop_timeout=5.0):
        self._model = model
        self._python = str(python)
        self._worker_path = str(worker_path) if worker_path else str(
            Path(__file__).with_name("asr_worker.py"))
        self._start_timeout = start_timeout
        self._transcribe_timeout = transcribe_timeout
        self._stop_timeout = stop_timeout
        self._lock = threading.RLock()
        self._proc = None
        self._warm = False
        self._buf = bytearray()

    def _cleanup_locked(self):
        p, self._proc, self._warm = self._proc, None, False
        self._buf.clear()
        if p is None:
            return
        _kill(p)
        try:
            p.stdin.close()
            p.stdout.close()
        except Exception:
            pass

    def _ensure_locked(self):
        p = self._proc
        if p is not None and self._warm and p.poll() is None:
            return
        self._cleanup_locked()
        if not os.path.isfile(self._python) or not os.path.isfile(self._worker_path):
            raise RuntimeError("Qwen: worker files missing")
        try:
            proc = subprocess.Popen([self._python, self._worker_path, "--model", self._model],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=None, text=False, bufsize=0)
        except OSError as e:
            raise RuntimeError(f"Qwen: spawn failed: {e}") from e
        try:
            doc = json.loads(_readline(proc, self._buf, self._start_timeout))
        except RuntimeError:
            _kill(proc)
            _close(proc)
            raise
        except ValueError:
            _kill(proc)
            _close(proc)
            raise RuntimeError("Qwen: malformed ready handshake")
        if not isinstance(doc, dict) or doc.get("ready") is not True:
            _kill(proc)
            _close(proc)
            raise RuntimeError(f"Qwen: startup failed: {doc}")
        self._proc = proc
        self._warm = True
        atexit.register(_kill, proc)

    def transcribe(self, wav_path, language=None):
        try:
            if language is not None and not isinstance(language, str):
                raise RuntimeError("Qwen: language must be text or null")
            wav = str(wav_path)
            if not os.path.isfile(wav):
                raise RuntimeError(f"Qwen: audio not found: {wav}")
            with self._lock:
                self._ensure_locked()
                p = self._proc
                try:
                    p.stdin.write((json.dumps({"path": wav, "language": language}) + "\n").encode())
                except OSError as e:
                    self._cleanup_locked()
                    raise RuntimeError(f"Qwen: worker died: {e}") from e
                try:
                    line = _readline(p, self._buf, self._transcribe_timeout)
                except RuntimeError:
                    self._cleanup_locked()
                    raise
                try:
                    doc = json.loads(line)
                except ValueError:
                    self._cleanup_locked()
                    raise RuntimeError("Qwen: malformed response")
                if not isinstance(doc, dict) or "ok" not in doc:
                    self._cleanup_locked()
                    raise RuntimeError("Qwen: malformed response")
                if doc.get("ok") is True:
                    text = doc.get("text")
                    if not isinstance(text, str):
                        self._cleanup_locked()
                        raise RuntimeError("Qwen: malformed response")
                    return text.strip()
                raise RuntimeError(f"Qwen: {doc.get('error') or 'transcription failed'}")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Qwen: failed: {e}") from e

    def status(self):
        try:
            p = self._proc
            alive = p is not None and p.poll() is None
            return {"warm": bool(alive and self._warm), "pid": p.pid if alive else None,
                    "model": self._model}
        except Exception:
            return {"warm": False, "pid": None, "model": ASR_MODEL}

    def stop(self):
        try:
            with self._lock:
                p, self._proc, self._warm = self._proc, None, False
                self._buf.clear()
                if p is None:
                    return
                try:
                    p.stdin.write(b'{"shutdown": true}\n')
                    p.wait(timeout=self._stop_timeout)
                except Exception:
                    _kill(p)
                try:
                    p.stdin.close()
                    p.stdout.close()
                except Exception:
                    pass
        except Exception:
            pass


class WhisperServer:
    def __init__(self, binary=None, start_timeout=60.0, stop_timeout=5.0):
        self._binary = binary
        self._start_timeout = start_timeout
        self._stop_timeout = stop_timeout
        self._worker_path = str(Path(__file__).with_name("asr_worker.py"))
        self._lock = threading.RLock()
        self._proc = None
        self._server_pid = None
        self._model = None
        self._url = None

    def _stop_locked(self):
        p, spid = self._proc, self._server_pid
        self._proc = self._server_pid = self._model = self._url = None
        if p is not None:
            try:
                p.terminate()
                p.wait(timeout=self._stop_timeout)
            except Exception:
                _kill(p)
            try:
                p.stdout.close()
            except Exception:
                pass
        if spid is not None and _alive(spid):
            try:
                os.kill(spid, signal.SIGTERM)
            except Exception:
                pass
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and _alive(spid):
                time.sleep(0.05)
            if _alive(spid):
                try:
                    os.kill(spid, signal.SIGKILL)
                except Exception:
                    pass

    def _wait(self, url, proc, spid):
        deadline = time.monotonic() + self._start_timeout
        health = url.rstrip("/") + "/health"
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f"Whisper: exited ({proc.poll()}) during startup")
            if not _alive(spid):
                raise RuntimeError("Whisper: server died during startup")
            try:
                with urllib.request.urlopen(health, timeout=2):
                    return
            except urllib.error.HTTPError as e:
                try:
                    e.close()
                except Exception:
                    pass
                if e.code == 404:
                    return
            except (urllib.error.URLError, OSError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Whisper: did not become ready in time")
            time.sleep(0.1)

    def ensure_started(self, model):
        try:
            m = str(model)
            if not os.path.isfile(m):
                raise RuntimeError(f"Whisper: model not found: {m}")
            with self._lock:
                p = self._proc
                alive = p is not None and p.poll() is None
                if alive and self._model == m and self._server_pid and _alive(self._server_pid):
                    return self._url
                self._stop_locked()
                b = self._binary or shutil.which("whisper-server") or "/opt/homebrew/bin/whisper-server"
                if not b or not os.path.isfile(b):
                    raise RuntimeError(f"Whisper: binary not found: {b}")
                s = socket.socket()
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
                s.close()
                url = f"http://127.0.0.1:{port}"
                try:
                    proc = subprocess.Popen(
                        [sys.executable, self._worker_path, "--whisper", "--binary", b,
                         "--model", m, "--host", "127.0.0.1", "--port", str(port)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=None, text=False, bufsize=0)
                except OSError as e:
                    raise RuntimeError(f"Whisper: spawn failed: {e}") from e
                try:
                    spid = int(json.loads(_readline(proc, bytearray(), 5.0)).get("server_pid"))
                except (RuntimeError, ValueError, AttributeError) as e:
                    _kill(proc)
                    _close(proc)
                    raise RuntimeError(f"Whisper: handshake failed: {e}") from e
                try:
                    self._wait(url, proc, spid)
                except RuntimeError:
                    _kill(proc)
                    _close(proc)
                    raise
                self._proc, self._server_pid, self._model, self._url = proc, spid, m, url
                atexit.register(_kill, proc)
                return url
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Whisper: failed: {e}") from e

    def status(self, model=None):
        try:
            p, spid = self._proc, self._server_pid
            alive = p is not None and p.poll() is None and (spid is None or _alive(spid))
            warm = alive and (model is None or str(model) == self._model)
            return {"warm": bool(warm), "pid": p.pid if alive else None,
                    "url": self._url if alive else None, "model": self._model if alive else None}
        except Exception:
            return {"warm": False, "pid": None, "url": None, "model": None}

    def stop(self):
        try:
            with self._lock:
                self._stop_locked()
        except Exception:
            pass
