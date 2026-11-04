"""Warm vision: one persistent mlx-vlm worker for the Mac vision choices.

Stdlib only, no MLX. Importing this module and constructing a worker spawn
nothing. Cleanup is lock-free (atexit plus a parent watcher in the child
cover exit and SIGTERM/SIGKILL). Failures raise RuntimeError; status()
never raises, spawns, or blocks.

The vision weights only arrive through an explicit Download: the backend
calls ensure_runtime() to build the venv, then configure() and
set_keep_loaded() to follow Use and Keep loaded. With no Mac vision choice
in use the worker never starts and vision.describe returns None.
"""
import atexit
import json
import math
import os
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

VLM_VENV = Path("/Users/olety/.cache/afterglow/vlm-venv")
VLM_PYTHON = VLM_VENV / "bin" / "python"
MLX_VLM_PIN = "mlx-vlm==0.7.11"
MLX_VLM_VERSION = "0.7.11"
VLM_UV_PYTHON = "3.12"

MAX_OBSERVATIONS = 6
MAX_PNG_BYTES = 8 * 1024 * 1024
MAX_QUESTION_CHARS = 2000

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


# --- facts validation: total, fail closed ------------------------------------------------------------

def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _repair_box(box):
    """[x, y, w, h] as fractions of the crop, or None. Small models often write [x1, y1, x2, y2]; a box that is not
    a valid x-y-width-height but is a valid pair of corners is converted. Anything else drops the box, not the fact."""
    if not isinstance(box, (list, tuple)) or len(box) != 4 or not all(_is_num(v) for v in box):
        return None
    x, y, w, h = (float(v) for v in box)
    if max(x, y, w, h) > 1 and min(x, y, w, h) >= 0 and max(x, y, w, h) <= 1000:
        x, y, w, h = x / 1000, y / 1000, w / 1000, h / 1000  # Qwen's 0-1000 grid
    if 0 <= x <= 1 and 0 <= y <= 1 and w > 0 and h > 0 and x + w <= 1 + 1e-9 and y + h <= 1 + 1e-9:
        return [x, y, w, h]
    x2, y2 = w, h
    if 0 <= x < x2 <= 1 + 1e-9 and 0 <= y < y2 <= 1 + 1e-9:
        return [x, y, x2 - x, y2 - y]
    return None


def validate_facts(doc, crop_id):
    """A worker reply as clean facts, or RuntimeError.

    The protocol is strict: a reply that is not ok, names another crop, or has no observation list is rejected and
    the caller answers None. The model's own mistakes are repaired instead of throwing every fact away: ids are
    renumbered v0, v1, ...; only the first MAX_OBSERVATIONS are kept (the prompt puts controls and warnings first);
    a box in corner form is converted and an unusable box is dropped (the text stays a fact, it just gets no pin);
    a missing label or state becomes empty; unknowns keep their strings."""
    if not isinstance(doc, dict) or doc.get("ok") is not True:
        raise RuntimeError("VLM: malformed facts")
    if not isinstance(crop_id, str) or not crop_id:
        raise RuntimeError("VLM: bad crop id")
    if doc.get("crop_id") != crop_id:
        raise RuntimeError("VLM: crop id mismatch")
    obs = doc.get("observations")
    if not isinstance(obs, list):
        raise RuntimeError("VLM: bad observations")
    ms = doc.get("ms")
    if not _is_num(ms) or ms < 0:
        raise RuntimeError("VLM: bad timing")
    clean = []
    for o in obs:
        if len(clean) >= MAX_OBSERVATIONS:
            break
        if not isinstance(o, dict):
            continue
        text, label, kind, state = o.get("text"), o.get("label", ""), o.get("kind"), o.get("state", "")
        text = text if isinstance(text, str) else ""
        label = label if isinstance(label, str) else ""
        if not (text.strip() or label.strip()):  # small models sometimes describe under another key
            alt = next((o[k] for k in ("description", "caption", "value", "content") if isinstance(o.get(k), str) and o[k].strip()), "")
            text = alt
        if not (text.strip() or label.strip()):
            continue  # nothing to say
        item = {"id": f"v{len(clean)}", "kind": kind[:32] if isinstance(kind, str) and kind.strip() else "object",
                "text": text[:2000], "label": label[:200], "box": _repair_box(o.get("box"))}
        if isinstance(state, str) and state.strip():
            item["state"] = state.strip()[:40]  # a control's state (disabled, selected, error, warning)
        clean.append(item)
    unknowns = doc.get("unknowns", [])
    unknowns = [u[:500] for u in unknowns if isinstance(u, str)][:32] if isinstance(unknowns, list) else []
    return {"crop_id": crop_id, "observations": clean, "unknowns": unknowns, "ms": int(ms)}


# --- worker ------------------------------------------------------------------------------------------

class VlmWorker:
    def __init__(self, model, python=VLM_PYTHON, worker_path=None,
                 start_timeout=300.0, describe_timeout=180.0, stop_timeout=5.0):
        self._model = model
        self._python = str(python)
        self._worker_path = str(worker_path) if worker_path else str(
            Path(__file__).with_name("vlm_worker.py"))
        self._start_timeout = start_timeout
        self._describe_timeout = describe_timeout
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
        if not self._model or not isinstance(self._model, str):
            raise RuntimeError("VLM: no vision model is in use")
        if not os.path.isfile(self._python) or not os.path.isfile(self._worker_path):
            raise RuntimeError("VLM: worker files missing")
        try:
            proc = subprocess.Popen([self._python, self._worker_path, "--model", self._model],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=None, text=False, bufsize=0)
        except OSError as e:
            raise RuntimeError(f"VLM: spawn failed: {e}") from e
        try:
            doc = json.loads(_readline(proc, self._buf, self._start_timeout))
        except RuntimeError:
            _kill(proc)
            _close(proc)
            raise
        except ValueError:
            _kill(proc)
            _close(proc)
            raise RuntimeError("VLM: malformed ready handshake")
        if not isinstance(doc, dict) or doc.get("ready") is not True:
            _kill(proc)
            _close(proc)
            raise RuntimeError(f"VLM: startup failed: {doc.get('error') if isinstance(doc, dict) else doc}")
        self._proc = proc
        self._warm = True
        atexit.register(_kill, proc)

    def warmup(self):
        """Start the worker and wait for its warm image pass. For Keep loaded."""
        with self._lock:
            self._ensure_locked()
        return True

    def describe_file(self, path, question=None, crop_id=None):
        try:
            if question is not None and not isinstance(question, str):
                raise RuntimeError("VLM: question must be text or null")
            if question is not None and len(question) > MAX_QUESTION_CHARS:
                raise RuntimeError("VLM: question is too long")
            img = str(path)
            if not os.path.isfile(img):
                raise RuntimeError(f"VLM: image not found: {img}")
            cid = crop_id if crop_id is not None else uuid.uuid4().hex
            if not isinstance(cid, str) or not cid:
                raise RuntimeError("VLM: bad crop id")
            with self._lock:
                self._ensure_locked()
                p = self._proc
                try:
                    p.stdin.write((json.dumps(
                        {"crop_id": cid, "path": img, "question": question}) + "\n").encode())
                except OSError as e:
                    self._cleanup_locked()
                    raise RuntimeError(f"VLM: worker died: {e}") from e
                try:
                    line = _readline(p, self._buf, self._describe_timeout)
                except RuntimeError:
                    self._cleanup_locked()
                    raise
                try:
                    doc = json.loads(line)
                except ValueError:
                    self._cleanup_locked()
                    raise RuntimeError("VLM: malformed response")
                if not isinstance(doc, dict) or "ok" not in doc:
                    self._cleanup_locked()
                    raise RuntimeError("VLM: malformed response")
                if doc.get("ok") is True:
                    try:
                        return validate_facts(doc, cid)
                    except RuntimeError:
                        self._cleanup_locked()
                        raise
                raise RuntimeError(f"VLM: {doc.get('error') or 'vision failed'}")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"VLM: failed: {e}") from e

    def describe_png(self, png_bytes, question=None, crop_id=None):
        if not isinstance(png_bytes, (bytes, bytearray)) or not png_bytes:
            raise RuntimeError("VLM: image must be bytes")
        if len(png_bytes) > MAX_PNG_BYTES:
            raise RuntimeError("VLM: image is too large")
        with tempfile.TemporaryDirectory(prefix="afterglow-vlm-") as folder:
            img = Path(folder) / "crop.png"
            img.write_bytes(bytes(png_bytes))
            return self.describe_file(str(img), question, crop_id)

    def status(self):
        try:
            p = self._proc
            alive = p is not None and p.poll() is None
            return {"warm": bool(alive and self._warm), "pid": p.pid if alive else None,
                    "model": self._model}
        except Exception:
            return {"warm": False, "pid": None, "model": self._model}

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


# --- explicit runtime install (Download only) ---------------------------------------------------------

def _runtime_version():
    try:
        if not VLM_PYTHON.is_file():
            return None
        r = subprocess.run([str(VLM_PYTHON), "-c",
                            "from importlib.metadata import version; print(version('mlx-vlm'))"],
                           capture_output=True, text=True, timeout=180)
        v = (r.stdout or "").strip()
        return v if r.returncode == 0 and v else None
    except (OSError, subprocess.SubprocessError):
        return None


def _find_uv():
    found = shutil.which("uv")
    if found:
        return found
    for c in (Path.home() / ".local" / "bin" / "uv", Path.home() / ".cargo" / "bin" / "uv",
              Path("/opt/homebrew/bin/uv")):
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def ensure_runtime(timeout=1800):
    """Build the vision venv (mlx-vlm pin) when missing. The backend calls this
    only from an explicit Download press. Never touches the ASR venv. Never
    raises: {"ready": bool, "line": str}."""
    try:
        if _runtime_version() == MLX_VLM_VERSION:
            return {"ready": True, "line": "The vision runtime is ready."}
        uv = _find_uv()
        if not uv:
            return {"ready": False,
                    "line": "Install uv first (https://docs.astral.sh/uv/), then press Download again."}
        VLM_VENV.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([uv, "venv", "--python", VLM_UV_PYTHON, str(VLM_VENV)],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            tail = ((r.stderr or r.stdout) or "").strip().splitlines()
            return {"ready": False,
                    "line": f"The vision environment did not install: {tail[-1][:200] if tail else r.returncode}."}
        r = subprocess.run([uv, "pip", "install", "--python", str(VLM_PYTHON), MLX_VLM_PIN],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0 or _runtime_version() != MLX_VLM_VERSION:
            tail = ((r.stderr or r.stdout) or "").strip().splitlines()
            return {"ready": False,
                    "line": f"The vision runtime did not install: {tail[-1][:200] if tail else r.returncode}."}
        return {"ready": True, "line": "The vision runtime is ready."}
    except subprocess.TimeoutExpired:
        return {"ready": False, "line": "The vision runtime took too long to install. Press Download again."}
    except Exception as e:
        return {"ready": False, "line": f"The vision runtime did not install: {e}."[:300]}


# --- one shared worker, following models.json ----------------------------------------------------------

_lock = threading.RLock()
_worker = None
_configured_model = None
_keep_loaded = False
_starting = False
_last_error = None
_generation = 0


def _invalidate_warmup_locked():
    global _generation, _starting
    _generation += 1
    _starting = False


def worker_for(model):
    """The shared worker for a model ref, created (not started) as needed."""
    global _worker, _configured_model
    if not model or not isinstance(model, str):
        raise RuntimeError("VLM: no vision model is in use")
    with _lock:
        if _worker is not None and _worker.status()["model"] == model:
            return _worker
        if _worker is not None:
            try:
                _worker.stop()
            except Exception:
                pass
        _worker = VlmWorker(model=model)
        if model != _configured_model:
            _invalidate_warmup_locked()
        _configured_model = model
        return _worker


def _stop_locked():
    global _worker
    _invalidate_warmup_locked()
    w, _worker = _worker, None
    if w is not None:
        try:
            w.stop()
        except Exception:
            pass


def _prewarm_async():
    global _starting, _last_error
    with _lock:
        model = _configured_model
        if not model or not _keep_loaded or _starting:
            return {"starting": _starting}
        try:
            warm = _worker is not None and _worker.status()["warm"]
        except Exception:
            warm = False
        if warm:
            return {"starting": False}
        generation = _generation
        _starting = True
        _last_error = None

    def _run():
        global _starting, _last_error
        worker = None
        try:
            with _lock:
                # Check and select atomically: a queued task must not restore an old model.
                if generation != _generation or not _keep_loaded:
                    return
                worker = worker_for(model)
            worker.warmup()
        except Exception as e:
            with _lock:
                if generation == _generation:
                    _last_error = str(e)[:300]
        finally:
            with _lock:
                stale = generation != _generation
                if not stale:
                    _starting = False
            if stale and worker is not None:
                # A selection change may have stopped it just before warmup started.
                worker.stop()

    threading.Thread(target=_run, daemon=True).start()
    return {"starting": True}


def local_model_path(choice):
    """The checked local weights for a vision choice, or None. Always goes
    through models.model_path, so the loader can never bypass the app's own
    checked download with a bare repo id into a new HF cache. Missing or
    unloadable choices answer None and vision answers None."""
    try:
        if not isinstance(choice, dict) or choice.get("runtime") != "mlx-vlm":
            return None
        import importlib
        mod = importlib.import_module(".models", __package__)
        fn = getattr(mod, "model_path", None)
        if not callable(fn):
            return None
        p = fn(choice)
        s = str(p).strip() if p else ""
        return s if s and os.path.exists(s) else None
    except Exception:
        return None


def _saved_vision():
    try:
        import importlib
        mod = importlib.import_module(".models", __package__)
        choice_fn = getattr(mod, "vision_choice", None)
        keep_fn = getattr(mod, "keep_loaded", None)
        choice = choice_fn() if callable(choice_fn) else None
        keep = bool(keep_fn("vision")) if callable(keep_fn) else False
        return choice, keep
    except Exception:
        return None, False


def configure(slot):
    """Follow a vision change: re-read the saved choice and keep flag, stop a
    stale worker, and warm the Mac choice when kept. The backend calls
    configure("vision") after Use, Remove, keep toggles, and downloads.
    Never raises, never blocks on a model load."""
    global _configured_model, _keep_loaded
    try:
        if slot != "vision":
            return {"slot": slot}
        choice, keep = _saved_vision()
        resolved = local_model_path(choice)
        with _lock:
            if resolved != _configured_model or keep != _keep_loaded:
                _stop_locked()
                _configured_model = resolved
            _keep_loaded = keep
        if resolved and keep:
            _prewarm_async()
        else:
            with _lock:
                _stop_locked()
        st = status()
        return {"slot": slot, "model": resolved, "warm": st["warm"], "starting": st["starting"]}
    except Exception as e:
        return {"slot": slot, "model": active_model(), "warm": False, "error": str(e)[:300]}


def set_keep_loaded(slot, on):
    """Follow the vision Keep loaded switch at once. The backend calls
    set_keep_loaded("vision", on); warming runs in the background and
    stopping is immediate. Never raises."""
    try:
        if slot != "vision":
            return {"slot": slot}
        return _set_vision_keep_loaded(bool(on))
    except Exception as e:
        return {"slot": slot, "keep_loaded": is_keep_loaded(), "error": str(e)[:300]}


def _set_vision_keep_loaded(on):
    global _keep_loaded
    with _lock:
        if bool(on) != _keep_loaded:
            _invalidate_warmup_locked()
        _keep_loaded = bool(on)
    if on:
        if active_model():
            _prewarm_async()
    else:
        with _lock:
            _stop_locked()
    st = status()
    return {"slot": "vision", "keep_loaded": is_keep_loaded(),
            "warm": st["warm"], "starting": st["starting"]}


def active_model():
    try:
        return _configured_model
    except Exception:
        return None


def is_keep_loaded():
    try:
        return bool(_keep_loaded)
    except Exception:
        return False


def last_error():
    try:
        return _last_error
    except Exception:
        return None


def status():
    """The shared worker: never raises, spawns, or blocks."""
    try:
        w = _worker
        alive = w is not None and w.status()["warm"]
        return {"model": _configured_model, "warm": bool(alive),
                "pid": w.status()["pid"] if w is not None else None,
                "keep_loaded": is_keep_loaded(), "starting": bool(_starting),
                "error": _last_error}
    except Exception:
        return {"model": None, "warm": False, "pid": None,
                "keep_loaded": False, "starting": False, "error": None}


def stop():
    """Stop the shared worker process; the configured model stays set."""
    try:
        with _lock:
            _stop_locked()
    except Exception:
        pass
