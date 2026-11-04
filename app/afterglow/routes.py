"""Feature modules add HTTP routes here, so a feature never edits web.py.

A feature module in this package exposes ROUTES = {("GET" | "POST", "/api/..."): handler}. A handler is
handler(app, req) -> a dict (sent as JSON, 200), a (dict, status) pair, or Stream(events) (sent as server-sent events,
one JSON object per event, like /api/chat). `app` is the running web.App (its .session is the open day); `req` is a
Request. A path that ends in "/*" matches every path under it (req.rest holds the remainder).
Raise ValueError for a bad request (400) and RuntimeError for a refused one (409); anything else is a 500.

Modules are listed in FEATURES and imported when present, so a feature branch adds its module and one name here.
"""
import importlib
import json
import traceback
import urllib.parse

FAILED = {}  # feature name -> why it did not load (shown in the server log)
FEATURES = ["threads_api", "history_api", "connectors", "dictation", "clipboard", "explainer", "loom_api"]


class Request:
    def __init__(self, method, path, query, headers, raw):
        self.method, self.path, self.headers, self.raw = method, path, headers, raw
        self.query = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
        self.rest = ""
        ctype = (headers.get("Content-Type") or "").split(";")[0].strip()
        self.body = json.loads(raw or b"{}") if ctype in ("", "application/json") and raw is not None else None


class Stream:
    """Server-sent events: an iterator of JSON-able dicts. `lock`: hold app.lock while it runs (a chat turn)."""

    def __init__(self, events, lock=False):
        self.events, self.lock = events, lock


_table = None


def table():
    global _table
    if _table is None:
        out = {}
        for name in FEATURES:
            try:
                mod = importlib.import_module(f".{name}", __package__)
            except ModuleNotFoundError as e:
                if e.name == f"{__package__}.{name}":
                    continue  # that feature is not on this branch
                traceback.print_exc()
                FAILED[name] = f"{type(e).__name__}: {e}"
                continue
            except Exception as e:  # a broken feature never takes the chat and the other features down with it
                traceback.print_exc()
                FAILED[name] = f"{type(e).__name__}: {e}"
                continue
            for key, fn in getattr(mod, "ROUTES", {}).items():
                if key in out:
                    raise RuntimeError(f"route {key} is claimed twice ({name})")
                out[key] = fn
        _table = out
    return _table


def find(method, path):
    """The handler for a request and the part of the path a "/*" route left over, or (None, "")."""
    t = table()
    if (method, path) in t:
        return t[(method, path)], ""
    for (m, p), fn in t.items():
        if m == method and p.endswith("/*") and path.startswith(p[:-1]):
            return fn, path[len(p) - 1:]
    return None, ""
