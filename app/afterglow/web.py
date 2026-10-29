"""Local web app: chat with the day agent, see its notes and its context snapshot. Stdlib only.

  uv run --project app afterglow-web --world w1-ines-varga --day 1
  open http://127.0.0.1:8650

Binds 127.0.0.1 only. One user, one session: turns and snapshot actions take the same lock.
"""
import argparse
import datetime as dt
import json
import mimetypes
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import paths, snapshot, thoughts
from .night_checks import abstention_gate, chat_checks, serving_choice, unlinked_corrections
from .agent import DaySession, load_world, now_iso
from .llm import GlimmerClient, GlimmerError

WEB = Path(__file__).resolve().parents[1] / "web"


# Samples for the steering panels until a night writes its own (eval/results/2026-10-29-steering on main):
# the behaviour check measured on w1's night 1 after it ran, and the dial changes proposed from that day.
STEERING_SAMPLES = {("w1-ines-varga", 1): {
    "behaviour_check": "eval/results/2026-10-29-steering/j1/behaviour-check-w1-night1.json",
    "proposals": "eval/results/2026-10-29-steering/proposals/w1-day1-steering-proposals.json"}}


def steering(world, day, night, ev):
    """The night's behaviour check (eval.json "behaviour_check", else behaviour-check.json) and the dial changes
    Glimmer proposes (steering-proposals.json) with the user's answers (steering-decisions.json). Falls back to
    STEERING_SAMPLES, marked as a sample."""
    def read(path):
        return json.loads(path.read_text()) if path.exists() else None

    check = (ev or {}).get("behaviour_check") or (read(night / "behaviour-check.json") or {}).get("behaviour_check")
    proposals = read(night / "steering-proposals.json")
    sample = {}
    repo = paths.worlds_root().parent
    for kind, rel in STEERING_SAMPLES.get((world, int(day)), {}).items():
        if kind == "behaviour_check" and not check and (repo / rel).exists():
            check, sample[kind] = read(repo / rel)["behaviour_check"], rel
        if kind == "proposals" and not proposals and (repo / rel).exists():
            proposals, sample[kind] = read(repo / rel), rel
    return {"behaviour_check": check, "proposals": proposals, "decisions": read(night / "steering-decisions.json") or {},
            "sample": sample}


def record_proposal(world, day, axis, toward, answer):
    """The user's answer to one proposed dial change, in runs/<world>/<day>/night/steering-decisions.json.
    An accepted dial applies from the next day's chat on (dials.py accepted_dials)."""
    if answer not in ("accept", "dismiss"):
        raise ValueError("answer must be accept or dismiss")
    night = paths.runs_root() / world / str(int(day)) / "night"
    night.mkdir(parents=True, exist_ok=True)
    f = night / "steering-decisions.json"
    doc = json.loads(f.read_text()) if f.exists() else {}
    doc[f"{axis}:{toward}"] = {"axis": axis, "toward": toward, "answer": answer,
                               "at": dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec="seconds"),
                               "applies": "tomorrow" if answer == "accept" else None}
    f.write_text(json.dumps(doc, indent=1) + "\n")
    return doc


def persona_name(world):
    return (load_world(world).get("persona") or {}).get("name")


def worlds_summary(day):
    """Every world with a night eval for `day`: facts answered per arm (A before, B after with notes off,
    C notes in the prompt, D both), for the report's all-worlds line and its person switcher."""
    out = []
    for f in sorted(paths.runs_root().glob(f"*/{day}/night/eval.json")):
        world = f.parents[2].name
        ev = json.loads(f.read_text())
        eval_day = ev.get("eval_day") or day
        n = sum(1 for x in ev.get("facts", []) if (x.get("valid_from") or 1) <= eval_day)
        learned = (ev.get("learned") or {}).get("facts_learned") or {}
        decision = f.parent / "decision.json"
        out.append({"world": world, "persona": persona_name(world), "facts": n,
                    **{arm: learned.get(arm) for arm in "ABCD"},
                    "decision": json.loads(decision.read_text()).get("decision") if decision.exists() else None})
    return out


class App:
    def __init__(self, world, day, endpoint, date=None, reasoning="low", restore=True, decide_supersede=None,
                 decide_adapter=None, decide_route=None):
        self.client = GlimmerClient(endpoint)
        self.session = DaySession(world, day, self.client, date=date, reasoning=reasoning,
                                  decide_supersede=decide_supersede, decide_adapter=decide_adapter,
                                  decide_route=decide_route)
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
        return {"world": world, "day": day, "persona": persona_name(world), "eval": load("eval.json"),
                "decision": load("decision.json"), "receipt": load("receipt.json"),
                "consolidated": load("consolidated.json"), "abstention": abstention_gate(night),
                "unlinked_corrections": unlinked_corrections(world, day, load("consolidated.json")),
                "steering": steering(world, day, night, load("eval.json")),
                "chat_checks": chat_checks(night, load("eval.json"), load("decision.json")),
                "questions": questions, "night_dir": str(night),
                "worlds": worlds_summary(day)}

    def state(self):
        s = self.session
        persona = (s.world_doc.get("persona") or {})
        healthy = self.client.health()
        try:
            adapter = self.adapter_label(self.client.world_adapter()) if healthy else None
        except GlimmerError as e:
            adapter = f"error: {e}"
        history = s.history()
        replies = [r for r in history if r.get("role") == "assistant"]
        return {
            "world": s.world, "day": s.day, "date": s.date, "persona": persona.get("name"),
            "serving": s.adapter_choice,  # which update the day agent serves under the serving rule, and why
            "dials": s.dial_choice,  # the steering dials the user accepted, sent with every request today
            # replies that ended empty and were asked again in the answer channel (agent.py); shown, never hidden
            "answer_channel_retries": {"retried": sum(1 for r in replies if r.get("answer_channel_retry")),
                                       "replies": len(replies)},
            "server": {"endpoint": self.client.endpoint, "healthy": healthy, "adapter": adapter},
            "notes": s.notes.all(include_superseded=True),
            "history": history,
            "snapshot": self.snapshot_status,
            "night": self.night_summary(),
            "busy": self.lock.locked(),
        }

    def adapter_label(self, loaded_adapter):
        """How the page names the world's loaded adapter: the night it came from (every file is adapter.gguf)."""
        entry = self.session.adapter
        return f"night {entry.get('day', '?')} update" if loaded_adapter and entry else None

    def ask_notes_off(self, question):
        """One question to Glimmer with no notes and no tools, in the eval's prompt shape (the chat template with
        minimal reasoning, then the forced answer channel), on slot 1 so slot 0 keeps the user's context. With an
        adapter loaded it asks twice, adapter scale 0 (the weights before the night) and 1 (after), each scale sent
        per request with no prompt cache, so the two answers never share KV. DFlash stays on in this server."""
        today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date().isoformat()
        rendered = self.client.post("/apply-template", {
            "messages": [{"role": "user", "content": question.strip() + " Answer in one short sentence."}],
            "chat_template_kwargs": {"reasoning_strength": "minimal", "current_date": today,
                                     "knowledge_cutoff": "2026-01-04"}})["prompt"]
        prompt = rendered.removesuffix("<|start|>assistant") + "<|start|>assistant to=user<|message|>"
        loaded = self.client.adapters(max_age_s=0)  # one adapter list for both arms
        mine = self.client.world_adapter(loaded)
        arms = [("Before the night", 0.0), ("After the night", 1.0)] if mine else [("Base weights", 0.0)]
        out = []
        for label, scale in arms:
            body = {"prompt": prompt, "n_predict": 160, "temperature": 0, "id_slot": 1, "cache_prompt": False}
            lora = self.client.lora_field(scale, loaded)
            if lora:  # every loaded adapter at 0, this world's at the arm's scale
                body["lora"] = lora
            t0 = time.time()
            r = self.client.post("/completion", body, timeout=120)
            out.append({"label": label, "scale": scale, "content": (r.get("content") or "").strip(),
                        "stop": r.get("stop_type"), "total_s": round(time.time() - t0, 2)})
        return {"question": question, "adapter": self.adapter_label(mine),
                "decoding": "greedy, DFlash speculative decoding on", "answers": out}

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
        if path == "/api/thoughts":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            available = thoughts.dates()
            date = q.get("date", [available[0] if available else None])[0]
            return self._json({"dates": available, "date": date, "items": thoughts.load(date) if date else None})
        if path == "/api/report":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            day = q.get("day", [None])[0]
            if day and not day.isdigit():
                return self._json({"error": f"day must be a number, got {day!r}"}, 400)
            return self._json(self.app.report(q.get("world", [None])[0], int(day) if day else None))
        if path in ("/", "/index.html"):
            path = "/index.html"
        elif path in ("/report", "/morning"):
            path = "/report.html"
        elif path == "/night":
            path = "/night.html"
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
            if path == "/api/ask":
                q = (self._body().get("question") or "").strip()
                if not q:
                    return self._json({"error": "empty question"}, 400)
                return self._json(self.app.ask_notes_off(q))
            if path == "/api/proposal":
                b = self._body()
                return self._json(record_proposal(b["world"], b["day"], b["axis"], b["toward"], b["answer"]))
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
    ap.add_argument("--decide-supersede", action=argparse.BooleanOptionalAction, default=None,
                    help="force the supersede decision at remember time on or off (default: the serving notes, "
                         "runs/serving/serving-rule.json supersede_decision)")
    ap.add_argument("--decide-adapter", help="the decision adapter's path on the serving box (default: base weights)")
    ap.add_argument("--decide-route", action=argparse.BooleanOptionalAction, default=None,
                    help="force the route decision at recall time on or off (default: the serving notes, "
                         "runs/serving/serving-rule.json route_decision)")
    a = ap.parse_args()
    Handler.app = App(a.world, a.day, a.endpoint, date=a.date, reasoning=a.reasoning, restore=not a.no_restore,
                      decide_supersede=a.decide_supersede, decide_adapter=a.decide_adapter,
                      decide_route=a.decide_route)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    srv.daemon_threads = True
    print(f"Afterglow: http://127.0.0.1:{a.port}  (world {a.world}, day {a.day}, Glimmer at {a.endpoint})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
