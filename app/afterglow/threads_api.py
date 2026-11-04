"""HTTP routes for subthreads (threads.py), for the right-hand Threads panel: the open day's list, one thread, and keep."""
from . import threads


def _day(app):
    return app.session.world, app.session.day


def list_threads(app, req):
    w, d = _day(app)
    return {"world": w, "day": d, "threads": threads.listing(w, d)}


def get_thread(app, req):
    w, d = _day(app)
    return threads.get(w, d, req.query.get("id"))


def keep_thread(app, req):
    w, d = _day(app)
    b = req.body or {}
    return threads.update(w, d, b.get("id"), kept=bool(b.get("kept", True)))


ROUTES = {
    ("GET", "/api/threads"): list_threads,
    ("GET", "/api/thread"): get_thread,
    ("POST", "/api/thread/keep"): keep_thread,
}
