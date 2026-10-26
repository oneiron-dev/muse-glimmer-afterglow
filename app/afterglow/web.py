"""Local web app: chat with the day agent, see its notes and its context snapshot. Stdlib only.

  uv run --project app afterglow-web --world w1-ines-varga --day 1
  open http://127.0.0.1:8650

Binds 127.0.0.1 only. One user, one session: turns and snapshot actions take the same lock.
"""
import argparse
import json
import mimetypes
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import paths, snapshot
from .agent import DaySession, now_iso
from .llm import GlimmerClient

WEB = Path(__file__).resolve().parents[1] / "web"


class App:
    def __init__(self, world, day, endpoint, date=None, reasoning="low", restore=True):
        self.client = GlimmerClient(endpoint)
        self.session = DaySession(world, day, self.client, date=date, reasoning=reasoning)
        self.lock = threading.Lock()
        self.snapshot_status = {"state": "idle"}
        if restore:
            threading.Thread(target=self.restore_snapshot, daemon=True).start()

    def restore_snapshot(self):
        with self.lock:
            self.snapshot_status = {"state": "restoring", "at": now_iso()}
            try:
                report = snapshot.start(self.session)
                self.snapshot_status = {"state": report["kind"], "at": now_iso(), **report}
            except Exception as e:
                self.snapshot_status = {"state": "error", "at": now_iso(), "message": f"{type(e).__name__}: {e}"}

    def report(self, world=None, day=None):
        """The morning report: runs/<world>/<day>/night/{eval,decision,receipt}.json, with the probe questions.
        Default day: the latest one with a night eval."""
        world = world or self.session.world
        root = paths.runs_root() / world
        if day is None:
            days = sorted((int(p.parent.parent.name) for p in root.glob("*/night/eval.json")
                           if p.parent.parent.name.isdigit()), reverse=True)
            if not days:
                return {"world": world, "missing": "no night has run for this world yet"}
            day = days[0]
        night = root / str(day) / "night"
        if not (night / "eval.json").exists():
            return {"world": world, "day": day, "missing": f"no eval.json in {night}"}
        load = lambda name: json.loads((night / name).read_text()) if (night / name).exists() else None
        probes_path = paths.worlds_root() / world / "heldout" / "probes.jsonl"
        questions = {}
        if probes_path.exists():
            for line in probes_path.read_text().splitlines():
                if line.strip():
                    p = json.loads(line)
                    questions[p["id"]] = p["question"]
        persona = (self.session.world_doc.get("persona") or {}) if world == self.session.world else {}
        return {"world": world, "day": day, "persona": persona.get("name"), "eval": load("eval.json"),
                "decision": load("decision.json"), "receipt": load("receipt.json"), "questions": questions,
                "night_dir": str(night)}

    def state(self):
        s = self.session
        persona = (s.world_doc.get("persona") or {})
        adapters = self.client.adapters()
        return {
            "world": s.world, "day": s.day, "date": s.date, "persona": persona.get("name"),
            "server": {"endpoint": self.client.endpoint, "healthy": self.client.health(),
                       "adapter": adapters[0]["path"].rsplit("/", 1)[-1] if adapters else None},
            "notes": s.notes.all(include_superseded=True),
            "history": s.history(),
            "snapshot": self.snapshot_status,
            "night": self.night_summary(),
            "busy": self.lock.locked(),
        }

    def night_summary(self):
        """The latest night's headline for the day page: facts answered with notes off, and the decision."""
        r = self.report()
        if r.get("missing"):
            return None
        ev, decision = r["eval"] or {}, r["decision"] or {}
        facts = [f for f in ev.get("facts", []) if f.get("valid_from", 1) <= (ev.get("eval_day") or r["day"])]
        learned = (ev.get("learned") or {}).get("facts_learned") or {}
        return {"day": r["day"], "facts": len(facts), "learned": learned.get("B"), "before": learned.get("A"),
                "decision": decision.get("decision")}


class Handler(BaseHTTPRequestHandler):
    app: App = None
    server_version = "afterglow"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/state":
            return self._json(self.app.state())
        if path == "/api/report":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            day = q.get("day", [None])[0]
            return self._json(self.app.report(q.get("world", [None])[0], int(day) if day else None))
        if path in ("/", "/index.html"):
            path = "/index.html"
        elif path in ("/report", "/morning"):
            path = "/report.html"
        f = (WEB / path.lstrip("/")).resolve()
        if WEB not in f.parents or not f.is_file():
            return self._json({"error": "not found"}, 404)
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/chat":
                return self._chat(self._body().get("text", "").strip())
            if path == "/api/snapshot/restore":
                threading.Thread(target=self.app.restore_snapshot, daemon=True).start()
                return self._json({"started": True})
            if path == "/api/day/end":
                with self.app.lock:
                    notes = self.app.session.end_day()
                return self._json({"notes": notes})
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        return self._json({"error": "not found"}, 404)

    def _chat(self, text):
        if not text:
            return self._json({"error": "empty message"}, 400)
        if not self.app.lock.acquire(blocking=False):
            return self._json({"error": "Afterglow is still answering the previous message"}, 409)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            for ev in self.app.session.run_turn(text):
                self.wfile.write(b"data: " + json.dumps(ev, ensure_ascii=False).encode() + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.app.lock.release()
        self.close_connection = True


def main():
    ap = argparse.ArgumentParser(description="Afterglow day app")
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--date", help="the day's date, YYYY-MM-DD (default: today, JST)")
    ap.add_argument("--endpoint", default=GlimmerClient().endpoint)
    ap.add_argument("--port", type=int, default=8650)
    ap.add_argument("--reasoning", default="low", choices=["minimal", "low", "medium", "high"])
    ap.add_argument("--no-restore", action="store_true", help="skip the KV snapshot restore and prefill at startup")
    a = ap.parse_args()
    Handler.app = App(a.world, a.day, a.endpoint, date=a.date, reasoning=a.reasoning, restore=not a.no_restore)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    srv.daemon_threads = True
    print(f"Afterglow: http://127.0.0.1:{a.port}  (world {a.world}, day {a.day}, Glimmer at {a.endpoint})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
