"""Circle vision: describe a crop with the Mac vision model, or nothing.

describe(png_bytes, question) -> {"text", "items", "ms", ...} or None. None
when no vision model is in use, when Glimmer itself sees the image (the
explainer sends the image to Glimmer then), or when the facts fail
validation. No partial JSON is ever returned.

POST /api/vision/describe {png_b64, question} exposes the same for checks.
The backend registers "vision" in routes.FEATURES; this module never edits
routes or the web server.
"""
import base64
import binascii
import importlib
import sys
import threading

from . import vlm_runtime

MAX_PNG_BYTES = 6000000  # the explainer's decoded-PNG limit, preserved
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MODES = ("none", "glimmer", "mac")


def _models():
    try:
        return importlib.import_module(".models", __package__)
    except Exception:
        return None


def mode(app=None):
    """vision_mode(), or "none" when the models module is absent or unsure.
    Pass the running app for the remote-server check, else the saved choice
    alone decides. Never raises or writes; with an app it may do the backend's
    short cached /props read, never spawns."""
    try:
        mod = _models()
        fn = getattr(mod, "vision_mode", None) if mod is not None else None
        if callable(fn):
            try:
                v = fn(app) if app is not None else fn()
            except TypeError:
                v = fn()
            if v in MODES:
                return v
        choice = active_choice()
        if isinstance(choice, dict):
            if choice.get("runtime") == "mlx-vlm":
                return "mac"
            if choice.get("runtime") == "none" and choice.get("id"):
                return "glimmer"
        return "none"
    except Exception:
        return "none"


def active_choice():
    """The in-use vision catalog dict, or None. Never raises."""
    try:
        mod = _models()
        fn = getattr(mod, "vision_choice", None) if mod is not None else None
        choice = fn() if callable(fn) else None
        return choice if isinstance(choice, dict) else None
    except Exception:
        return None


_diag_lock = threading.Lock()
_last_diagnostic = None


def _note_diagnostic(reason):
    """The safe failure reason for the last describe (fixed reason strings and
    metadata only: never image bytes or facts text)."""
    global _last_diagnostic
    try:
        with _diag_lock:
            _last_diagnostic = str(reason)[:300] if reason else None
    except Exception:
        pass


def last_diagnostic():
    try:
        with _diag_lock:
            return _last_diagnostic
    except Exception:
        return None


def _want_keep_loaded():
    try:
        mod = _models()
        fn = getattr(mod, "keep_loaded", None) if mod is not None else None
        if callable(fn):
            return bool(fn("vision"))
    except Exception:
        pass
    return vlm_runtime.is_keep_loaded()


def describe(png_bytes, question=None):
    """Facts about a crop, or None (see module doc). Raises ValueError only
    for bad input; every vision failure answers None."""
    if not isinstance(png_bytes, (bytes, bytearray)) or not png_bytes:
        raise ValueError("Send a PNG crop.")
    if len(png_bytes) > MAX_PNG_BYTES:
        raise ValueError("Send a PNG crop up to 6 MB.")
    if bytes(png_bytes[:8]) != PNG_MAGIC:
        raise ValueError("Send a PNG crop.")
    if question is not None and not isinstance(question, str):
        raise ValueError("The question must be text.")
    if question is not None and len(question) > vlm_runtime.MAX_QUESTION_CHARS:
        raise ValueError("Keep the question under 2,000 characters.")
    if mode() != "mac":
        return None
    model = vlm_runtime.local_model_path(active_choice())
    if not model:
        return None
    try:
        worker = vlm_runtime.worker_for(model)
    except RuntimeError as e:
        _fail(str(e))
        return None
    try:
        facts = worker.describe_png(bytes(png_bytes), question)
    except RuntimeError as e:
        _fail(str(e))
        return None
    finally:
        if not _want_keep_loaded():
            try:
                worker.stop()
            except Exception:
                pass
    items = [{"label": (o["label"] or o["text"] or o["kind"]), "box": o["box"]}
             for o in facts["observations"] if o["box"] is not None]  # a fact without a usable box gets no pin
    # A control's state goes with its text ("送信 Send (disabled)"): the answer often turns on it.
    text = "; ".join(t for t in (((o["text"] or o["label"]) + (f" ({o['state']})" if o.get("state") else ""))
                                 for o in facts["observations"]) if t.strip())
    return {"text": text, "items": items, "ms": facts["ms"],
            "observations": facts["observations"], "unknowns": facts["unknowns"],
            "crop_id": facts["crop_id"]}


def _fail(reason):
    _note_diagnostic(reason)
    try:
        print(f"vision: describe failed: {reason}", file=sys.stderr, flush=True)
    except Exception:
        pass


def vision_describe(app, req):
    doc = req.body
    if not isinstance(doc, dict):
        raise ValueError("Send a JSON object with png_b64.")
    raw = doc.get("png_b64")
    if not isinstance(raw, str) or not raw:
        raise ValueError("Send png_b64 with base64 PNG bytes.")
    if len(raw) > MAX_PNG_BYTES * 4 // 3 + 4:
        raise ValueError("Send a PNG crop up to 6 MB.")
    try:
        png = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as e:
        raise ValueError("png_b64 must be valid base64.") from e
    m = mode(app)
    if m != "mac":
        return {"available": False, "mode": m,
                "line": ("Your agent's server reads the picture itself." if m == "glimmer"
                         else "No vision model is in use. Pick one under Settings, Models.")}
    result = describe(png, doc.get("question"))
    if result is None:
        return {"available": True, "mode": "mac", "ok": False,
                "diagnostic": last_diagnostic(),
                "line": "The vision model did not answer. Keep it loaded and check again."}
    return {"available": True, "mode": "mac", "ok": True, **result}


ROUTES = {("POST", "/api/vision/describe"): vision_describe}
