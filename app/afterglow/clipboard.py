"""Opt-in clipboard diary, separate from notes, chat and training.

Routes initialise the connector lazily. Once initialised, the app's day/profile
switches stop the old watcher and honour the new person's saved switch.
"""
import functools
import hashlib
import json
import math
import platform
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import Counter

from . import paths

MAX_CHARS = 20000
POLL_SECONDS = 1
_SERVICE = "afterglow-clip"
_init_lock = threading.Lock()


def is_secret(text):
    """Conservative shapes, not a model call. Short codes are intentionally excluded."""
    if re.search(r"(?:sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
                 r"|xox[bp]-[A-Za-z0-9-]{12,}|AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{35})", text):
        return True
    if re.search(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", text):
        return True
    if re.search(r"-----BEGIN [A-Z0-9 ]+-----[\s\S]+?-----END [A-Z0-9 ]+-----", text):
        return True
    if re.search(r"(?i)\b(?:password|passwd|pwd)\b[\"']?\s*[:=]\s*[\"']?\S+", text):
        return True
    for match in re.finditer(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)", text):
        digits = re.sub(r"\D", "", match.group())
        total = 0
        for i, c in enumerate(reversed(digits)):
            n = int(c) * (2 if i % 2 else 1)
            total += n - 9 if n > 9 else n
        if 13 <= len(digits) <= 19 and len(set(digits)) > 1 and total % 10 == 0:
            return True
    # A URL is not a token, nor is a UUID. Explicit key shapes above still win.
    plain = re.sub(r"https?://\S+", "", text, flags=re.I)
    plain = re.sub(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", "", plain, flags=re.I)
    for token in re.findall(r"[A-Za-z0-9_+/=\-]{32,}", plain):
        entropy = -sum((n / len(token)) * math.log2(n / len(token)) for n in Counter(token).values())
        mixed = bool(re.search(r"[0-9_+/=\-]", token))
        if entropy >= (3.5 if mixed else 4.0):
            return True
    return False


def mask(text):
    return text[:2] + "••••••" + text[-4:]


def clipboard_command():
    if platform.system() == "Darwin":
        return [shutil.which("pbpaste")] if shutil.which("pbpaste") else None
    if shutil.which("wl-paste"):
        return [shutil.which("wl-paste"), "--no-newline", "--type", "text"]
    if shutil.which("xclip"):
        return [shutil.which("xclip"), "-selection", "clipboard", "-o"]
    return None


def read_clipboard():
    cmd = clipboard_command()
    if not cmd:
        raise RuntimeError("Clipboard capture is not available on this computer.")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("Your clipboard could not be read.") from None
    if result.returncode:
        raise RuntimeError("Your clipboard could not be read.")
    return result.stdout


def front_app():
    if platform.system() != "Darwin":
        return ""
    try:
        result = subprocess.run(["osascript", "-e", 'tell application "System Events" to get name of first application process whose frontmost is true'],
                                capture_output=True, text=True, timeout=2)
        return result.stdout.strip()[:120] if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


class Keychain:
    """Never include command output or arguments in an exception: they may hold a secret."""

    def _run(self, args, missing_ok=False):
        if platform.system() != "Darwin" or not shutil.which("security"):
            raise RuntimeError("Your Keychain is not available. The secret was not saved.")
        try:
            result = subprocess.run(["security", *args], capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError("Your Keychain could not be updated.") from None
        if result.returncode and not (missing_ok and result.returncode == 44):
            raise RuntimeError("Your Keychain could not be updated.")

    def save(self, clip_id, value):
        self._run(["add-generic-password", "-s", _SERVICE, "-a", clip_id, "-w", value, "-U"])

    def label(self, clip_id, label):
        # Updating metadata without -w preserves the existing password.
        self._run(["add-generic-password", "-s", _SERVICE, "-a", clip_id, "-j", label, "-U"])

    def delete(self, clip_id):
        self._run(["delete-generic-password", "-s", _SERVICE, "-a", clip_id], missing_ok=True)


def _file(session):
    return paths.runs_root() / session.world / str(int(session.day)) / "clips.jsonl"


def _read(session):
    f = _file(session)
    return [json.loads(line) for line in f.read_text().splitlines() if line.strip()] if f.exists() else []


def _write(session, clips):
    f = _file(session)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in clips))
    tmp.replace(f)


class Clipboard:
    def __init__(self, app, reader=None, keychain=None, app_name=None, interval=POLL_SECONDS):
        self.app = app
        self.reader = reader or read_clipboard
        self.available = reader is not None or clipboard_command() is not None
        self.keychain = keychain or Keychain()
        self.app_name = app_name or front_app
        self.interval = interval
        self.lock = threading.RLock()
        self.lifecycle = threading.RLock()
        self.thread = None
        self.stop_event = threading.Event()
        self.session = None
        self.last_digest = None
        self.error = None
        self.on = False

    def _saved(self):
        f = paths.runs_root() / self.app.session.world / "clipboard.json"
        return json.loads(f.read_text()).get("on") is True if f.exists() else False

    def stop(self):
        with self.lifecycle:
            self.stop_event.set()
            if self.thread and self.thread is not threading.current_thread():
                self.thread.join()
            self.thread = None

    def sync(self):
        """No writes and no thread for a person who has never enabled capture."""
        with self.lifecycle:
            if self.session is self.app.session:
                return
            self.stop()
            with self.lock:
                self.session = self.app.session
                self.on = self._saved()
                self.last_digest = None
                self.error = None
                self._start()

    def _start(self):
        if self.on and self.available:
            self.stop_event = threading.Event()
            self.thread = threading.Thread(target=self._watch, name="afterglow-clipboard", daemon=True)
            self.thread.start()

    def set_on(self, on):
        if not isinstance(on, bool):
            raise ValueError("on must be true or false")
        with self.lifecycle:
            self.sync()
            if on and not self.available:
                raise RuntimeError("Clipboard capture is not available on this computer.")
            self.stop()
            with self.lock:
                f = paths.runs_root() / self.session.world / "clipboard.json"
                f.parent.mkdir(parents=True, exist_ok=True)
                tmp = f.with_suffix(".json.tmp")
                tmp.write_text(json.dumps({"on": on}) + "\n")
                tmp.replace(f)
                self.on = on
                self.error = None
                self.last_digest = None
                self._start()
        return self.state()

    def _watch(self):
        while not self.stop_event.wait(self.interval):
            try:
                self.capture()
            except Exception:
                # Do not expose a failed command, clipboard value or Keychain output.
                with self.lock:
                    self.error = "A copy could not be saved. Check clipboard access and your Keychain."

    def capture(self):
        """One tick; slow OS calls never hold the foreground chat's lock."""
        if self.app.lock.locked():
            return
        with self.lock:
            session = self.session
            if not self.on or self.stop_event.is_set() or self.app.session is not session:
                return
            text = self.reader()
            if not text or not text.strip():
                return
            digest = hashlib.sha256(text.encode()).digest()
            if digest == self.last_digest:
                return
            # Classify before truncating, so a key beyond the limit cannot be saved as text.
            secret = is_secret(text)
            value = text[:MAX_CHARS]
            clip_id = uuid.uuid4().hex
            clip = {"id": clip_id, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "kind": "secret" if secret else "text", "text": mask(value) if secret else value,
                    "chars": len(text), "app": self.app_name()}
            if secret:
                self.keychain.save(clip_id, value)
            committed = False
            try:
                if not self.app.lock.acquire(blocking=False):
                    return
                try:
                    # Day changes and disable requests can arrive during the OS calls.
                    if self.app.session is not session or self.stop_event.is_set():
                        return
                    f = _file(session)
                    f.parent.mkdir(parents=True, exist_ok=True)
                    with f.open("a") as out:
                        out.write(json.dumps(clip, ensure_ascii=False) + "\n")
                    committed = True
                    self.last_digest = digest
                    self.error = None
                    return clip
                finally:
                    self.app.lock.release()
            finally:
                if secret and not committed:
                    self.keychain.delete(clip_id)

    def state(self):
        self.sync()
        with self.lock:
            clips = _read(self.session)
            return {"on": self.on, "available": self.available, "count": len(clips),
                    "pending": [c for c in clips if c["kind"] == "secret" and not c.get("label")],
                    "error": self.error}

    def listing(self, day=None):
        self.sync()
        if day is not None and (not str(day).isdigit() or int(day) < 1):
            raise ValueError("day must be a positive number")
        with self.lock:
            if day is None or int(day) == int(self.session.day):
                return _read(self.session)
            from types import SimpleNamespace
            return _read(SimpleNamespace(world=self.session.world, day=int(day)))

    def change(self, action, clip_id, label=None):
        self.sync()
        with self.app.lock, self.lock:
            if self.app.session is not self.session:
                raise RuntimeError("Your day changed. Try again.")
            clips = _read(self.session)
            clip = next((c for c in clips if c["id"] == clip_id), None)
            if clip is None:
                raise ValueError("No such copy today.")
            if action == "attach":
                if clip["kind"] == "secret" or is_secret(clip["text"]):
                    raise RuntimeError("Keys and passwords cannot be sent to chat.")
                return {"ok": True, "attachment": self.app.attach(f"copy-{clip_id[:8]}.txt", clip["text"])}
            if action == "label":
                if clip["kind"] != "secret":
                    raise ValueError("Only a hidden copy needs a label.")
                if not isinstance(label, str) or not label.strip() or len(label) > 240:
                    raise ValueError("Use a short label, up to 240 characters.")
                if is_secret(label):
                    raise ValueError("Describe what it is for, without the key or password.")
                self.keychain.label(clip_id, label.strip())
                clip["label"] = label.strip()
            elif action == "delete":
                if clip["kind"] == "secret":
                    self.keychain.delete(clip_id)
                clips.remove(clip)
            else:
                raise ValueError("Unknown clipboard action.")
            _write(self.session, clips)
            return {"ok": True}


def connector(app):
    """Feature-local lifecycle binding; web.py and the model/tool list stay unchanged."""
    with _init_lock:
        if not hasattr(app, "_clipboard"):
            app._clipboard = Clipboard(app)
            for name in ("open_day", "create_profile"):
                original = getattr(app, name, None)
                if original is None:
                    continue

                def bind(fn):
                    @functools.wraps(fn)
                    def switched(*args, **kwargs):
                        result = fn(*args, **kwargs)
                        app._clipboard.sync()
                        return result
                    return switched

                setattr(app, name, bind(original))
        app._clipboard.sync()
        return app._clipboard


def state(app, req):
    return connector(app).state()


def toggle(app, req):
    return connector(app).set_on((req.body or {}).get("on"))


def clips(app, req):
    return {"clips": connector(app).listing(req.query.get("day"))}


def label(app, req):
    b = req.body or {}
    return connector(app).change("label", b.get("id"), b.get("label"))


def delete(app, req):
    return connector(app).change("delete", (req.body or {}).get("id"))


def attach(app, req):
    return connector(app).change("attach", (req.body or {}).get("id"))


ROUTES = {("GET", "/api/clipboard"): state, ("POST", "/api/clipboard"): toggle,
          ("GET", "/api/clips"): clips, ("POST", "/api/clips/label"): label,
          ("POST", "/api/clips/delete"): delete, ("POST", "/api/clips/attach"): attach}
