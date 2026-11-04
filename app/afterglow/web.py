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

from . import dials, modelsetup, morning, paths, routes, snapshot, thoughts
from .night_checks import (abstention_gate, chat_checks, never_told_line, serving_choice, serving_rule,
                           unlinked_corrections)
from .agent import NOTES_BACKENDS, DaySession, load_world, now_iso
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
                 decide_adapter=None, decide_route=None, web_search=None, slot=0, notes_backend="local"):
        self.client = GlimmerClient(endpoint, slot=slot)
        # every day this app opens (the start day, then the day switcher's) is built with the same options
        self.session_options = dict(reasoning=reasoning, decide_supersede=decide_supersede,
                                    decide_adapter=decide_adapter, decide_route=decide_route, web_search=web_search,
                                    notes_backend=notes_backend)
        self.session = DaySession(world, day, self.client, date=date, **self.session_options)
        self.lock = threading.Lock()
        self.snapshot_status = {"state": "idle"}
        self.restore = restore
        # Settings -> Model: where the model comes from (a server, a file this app serves, or the official download)
        self.model_source = {"source": "server", "url": self.client.endpoint, "kind": "llama.cpp", "from": "start"}
        self.local_server = modelsetup.LocalServer()
        self.download = modelsetup.Download()
        self._hardware = (0.0, None)
        if restore:
            threading.Thread(target=self.restore_snapshot, daemon=True).start()

    def recorded_days(self):
        """The day switcher's list: every person with the days already opened once (a day folder with session.json),
        [{"world", "name", "days": [n, ...]}]."""
        out = []
        for root in sorted(p for p in paths.runs_root().iterdir() if p.is_dir() and not p.name.startswith(("_", "."))
                           and p.name != "serving"):
            days = sorted(int(d.name) for d in root.iterdir() if d.name.isdigit() and (d / "session.json").exists())
            if days:
                out.append({"world": root.name, "name": persona_name(root.name), "days": days})
        return out

    def open_day(self, world, day):
        """The day switcher: this app now serves another recorded day (its chat, notes, dials and update), as a new
        app process started on that day would; only a day with session.json, so no new day is made here. The
        snapshot restore runs again when this app restores at start."""
        day = int(day)
        # only a person and day the list holds: the names come from runs/, never straight from the request
        if not any(p["world"] == world and day in p["days"] for p in self.recorded_days()):
            raise ValueError(f"no recorded day {day} for {world}")
        with self.lock:
            self.session = DaySession(world, day, self.client, **self.session_options)
            self.snapshot_status = {"state": "idle"}
        if self.restore:
            threading.Thread(target=self.restore_snapshot, daemon=True).start()
        return {"world": world, "day": day, "date": self.session.date}

    def change_note(self, note_id, value=None, forget=False):
        """The Memory screen's edit or forget on one note (local notes; the night reads notes.json), logged in
        runs/<world>/<day>/note-edits.jsonl."""
        with self.lock:
            s = self.session
            notes = getattr(s.notes, "local", s.notes)
            if forget:
                n = notes.forget_by_user(note_id, day=s.day)
                rec = {"at": now_iso(), "id": note_id, "action": "forget", "key": n["key"], "value": n["value"]}
            else:
                if not str(value or "").strip():
                    raise ValueError("a note needs a value; use forget to remove it")
                r = notes.edit_by_user(note_id, value, day=s.day)
                n = r["note"]
                rec = {"at": now_iso(), "id": note_id, "action": "edit", "key": n["key"], "new_id": n["id"],
                       "value": n["value"], "old_value": (r["superseded"] or {}).get("value")}
            with open(s.dir / "note-edits.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    ATTACH_MAX_CHARS = 12000

    def attach(self, name, text):
        """A file attached in the chat: its text is kept in runs/<world>/<day>/attachments/<id>.txt with an index
        (attachments.json), and goes into the next message for the agent to read. Text only; at most
        ATTACH_MAX_CHARS characters reach the message."""
        name = Path(str(name or "file")).name[:120] or "file"
        text = str(text or "")
        if not text.strip():
            raise ValueError(f"{name} has no text to read")
        s = self.session
        d = s.dir / "attachments"
        d.mkdir(parents=True, exist_ok=True)
        index_path = d / "attachments.json"
        index = json.loads(index_path.read_text()) if index_path.exists() else []
        att = {"id": f"A{len(index) + 1:03d}", "name": name, "chars": len(text), "day": s.day, "at": now_iso(),
               "truncated": len(text) > self.ATTACH_MAX_CHARS}
        (d / f"{att['id']}.txt").write_text(text, encoding="utf-8")
        index.append(att)
        index_path.write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n")
        return att

    def attachments(self):
        """Every file attached in the chat for this person, newest first (the Files screen)."""
        out = []
        for f in sorted((paths.runs_root() / self.session.world).glob("*/attachments/attachments.json")):
            out.extend(json.loads(f.read_text()))
        return sorted(out, key=lambda a: a.get("at", ""), reverse=True)

    def message_with_attachments(self, text, ids):
        """The message the agent reads: what the user typed, then each attached file's text under its name."""
        if not ids:
            return text, None
        d = self.session.dir / "attachments"
        index = {a["id"]: a for a in json.loads((d / "attachments.json").read_text())} if (d / "attachments.json").exists() else {}
        parts, shown = [text] if text else [], []
        for i in ids:
            a = index.get(i)
            if not a:
                raise ValueError(f"no attachment {i} today")
            body = (d / f"{i}.txt").read_text(encoding="utf-8")[: self.ATTACH_MAX_CHARS]
            parts.append(f"[Attached file: {a['name']}]\n{body}" + ("\n[The file continues; only the start is shown.]" if a["truncated"] else ""))
            shown.append({"id": a["id"], "name": a["name"], "chars": a["chars"]})
        return "\n\n".join(parts), {"typed": text, "attachments": shown}

    def create_profile(self, name, about=""):
        """Add a profile made in the app: runs/_profiles/<id>/world.json (the person's name and one line about their
        work, nothing else), then open its first day. A new person starts with no notes and the base weights."""
        name = " ".join(str(name or "").split())[:60]
        if not name:
            raise ValueError("a profile needs a name")
        slug = "p-" + ("-".join("".join(c if c.isalnum() else " " for c in name.lower()).split()) or "profile")
        root = paths.runs_root() / "_profiles"
        world, n = slug, 2
        while (root / world).exists() or (paths.runs_root() / world).exists():
            world, n = f"{slug}-{n}", n + 1
        (root / world).mkdir(parents=True)
        doc = {"world": world, "synthetic": False, "made_in_app": now_iso(),
               "persona": {"name": name, "summary": " ".join(str(about or "").split())[:240]}}
        (root / world / "world.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
        with self.lock:
            self.session = DaySession(world, 1, self.client, **self.session_options)
            self.snapshot_status = {"state": "idle"}
        return {"world": world, "day": 1, "date": self.session.date, "name": name}

    def restore_snapshot(self):
        if self.client.kind != "llama.cpp":  # slot files are llama-server's; another server has none
            self.snapshot_status = {"state": "skipped", "at": now_iso(), "message": "the server is not llama.cpp"}
            return
        with self.lock:
            self.snapshot_status = {"state": "restoring", "at": now_iso()}
            try:
                report = snapshot.start(self.session)
                self.snapshot_status = {"state": report["kind"], "at": now_iso(), **report}
            except Exception as e:
                self.snapshot_status = {"state": "error", "at": now_iso(), "message": f"{type(e).__name__}: {e}"}

    def undo_night_link(self, old_id):
        """The morning report's Undo on one of the night's links: the old note is active again in today's notes, and
        the undo is logged (runs/<w>/<day>/night-link-undos.jsonl) beside the note's own record, for the next night.
        The restored note is in recall at once and in the notes block from the next day (today's block is fixed)."""
        with self.lock:
            s = self.session
            notes = getattr(s.notes, "local", s.notes)
            link = notes.undo_night_link(old_id, day=s.day)
            with open(s.dir / "night-link-undos.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": now_iso(), "world": s.world, "day": s.day, "old_id": old_id, **link},
                                   ensure_ascii=False) + "\n")
        return {"undone": old_id, "link": link}

    def answer_night_proposal(self, old_id, new_id, answer):
        """The morning report's Link / Keep both on a link the night proposed but did not apply: the answer goes on
        today's notes (notes.answer_night_proposal) and into runs/<w>/<day>/night-link-answers.jsonl for the night."""
        with self.lock:
            s = self.session
            prev = paths.runs_root() / s.world / str(s.day - 1) / "night" / "night-links.json"
            doc = json.loads(prev.read_text()) if prev.exists() else {}
            proposal = next((p for p in doc.get("proposed") or [] if p.get("old_id") == old_id
                             and p.get("new_id") == new_id), None)
            if proposal is None:
                return {"error": f"no proposed link {old_id} -> {new_id} for day {s.day}"}
            notes = getattr(s.notes, "local", s.notes)
            rec = notes.answer_night_proposal(proposal, answer, doc.get("day"), day=s.day)
            with open(s.dir / "night-link-answers.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"world": s.world, "old_id": old_id, "new_id": new_id, "question":
                                    proposal.get("question"), **rec}, ensure_ascii=False) + "\n")
            s._log({"ts": rec.get("at"), "turn": s.turn, "role": "night_link_answer", "old_id": old_id,
                    "new_id": new_id, "answer": answer})  # the day log keeps the user's answer too
        return {"answered": answer, "old_id": old_id, "new_id": new_id}

    def night_links(self, world, day):
        """The night's links (night-links.json) with what today's notes did with each: applied (with an undo),
        undone, or not applied; None when the night wrote none."""
        f = paths.runs_root() / world / str(day) / "night" / "night-links.json"
        if not f.exists():
            return None
        doc = json.loads(f.read_text())
        s = self.session
        today = {n["id"]: n for n in getattr(s.notes, "local", s.notes).notes} \
            if s.world == world and s.day == doc.get("applies_from_day") else {}
        for link in doc.get("links") or []:
            old = today.get(link.get("old_id")) or {}
            link["state"] = ("applied" if (old.get("superseded_by_night") or {}).get("by") == link.get("new_id")
                             else "undone" if any(u.get("by") == link.get("new_id") for u in old.get("night_links_undone") or [])
                             else "not applied")
        for prop in doc.get("proposed") or []:  # refused by the night's guard: the user answers Link / Keep both
            old = today.get(prop.get("old_id")) or {}
            linked = (old.get("superseded_by_night") or {}).get("by") == prop.get("new_id")
            kept = any(k.get("by") == prop.get("new_id") for k in old.get("night_links_kept_both") or [])
            prop["state"] = "linked by you" if linked else "kept both" if kept else "asked" if today else "proposed"
        return doc

    def voices(self):
        """The voice panel: presets, custom voices, the current choice and the dials every request carries now."""
        s = self.session
        return dials.voices_state(s.world, s.day, s.dial_choice)

    def set_voice(self, preset=None, voice=None):
        """The user's voice for today (dials.choose_voice); every request from the next message carries it."""
        with self.lock:
            s = self.session
            choice = dials.choose_voice(s.world, s.day, preset=preset, voice=voice)
            s.dial_choice = dials.accepted_dials(s.world, s.day)
            s.client.dials = s.dial_choice["dials"]  # each turn's record keeps the dials it sent (agent.py)
        return {"chosen": choice, **self.voices()}

    def report(self, world=None, day=None):
        """The morning report: the night of record's {eval,decision,receipt}.json (runs/<world>/<day>/night, or the kept
        arm the serving manifest names for that day, morning.night_dir), with the probe questions, every night of the
        person (morning.nights), the fresh-200 confirmation and the drafter's speed. Default day: for the person of
        this session, the latest night before today's day; for another person, the night their chat is served with;
        else the latest one with a night eval."""
        world = world or self.session.world
        root = paths.runs_root() / world
        if day is None:
            days = sorted((int(p.name) for p in root.glob("*") if p.name.isdigit()
                           and (root / morning.night_dir(world, p.name) / "eval.json").exists()), reverse=True)
            if not days:
                return {"world": world, "missing": "no night has run for this world yet"}
            kept = morning.served(world)
            before_today = [d for d in days if d < self.session.day] if world == self.session.world else []
            day = before_today[0] if before_today else kept["day"] if kept and kept["day"] in days else days[0]
        rel = morning.night_dir(world, day)
        night = root / rel
        if not (night / "eval.json").exists() and rel != f"{day}/night":
            rel, night = f"{day}/night", root / str(day) / "night"
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
                "never_told": never_told_line(night),
                # checks the serving rule shows on the report but does not require (runs/serving/serving-rule.json)
                "shown_not_required": (serving_rule() or {}).get("shown_not_required") or [],
                "unlinked_corrections": unlinked_corrections(world, day, load("consolidated.json")),
                "night_links": self.night_links(world, day),
                "steering": steering(world, day, night, load("eval.json")),
                "chat_checks": chat_checks(night, load("eval.json"), load("decision.json")),
                "questions": questions, "night_dir": str(night), "night_of_record": rel,
                "served": morning.served(world), "nights": [n for n in morning.nights(world) if n["day"] <= int(day)],
                "fresh": morning.fresh(load("decision.json")), "speed": morning.speed(),
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
            "server": {"endpoint": self.client.endpoint, "healthy": healthy, "adapter": adapter,
                       "kind": self.client.kind},
            "notes": s.notes.all(include_superseded=True),
            "notes_backend": s.notes_backend,
            "history": history,
            "snapshot": self.snapshot_status,
            "night": self.night_summary(),
            "busy": self.lock.locked(),
        }

    DEFAULT_SCHEDULE = {"ends_at": "23:00", "auto": False}  # never starts a night by itself unless the user turns it on

    def schedule(self, ends_at=None, auto=None):
        """When the person's day ends and the night starts (runs/<world>/schedule.json): a time, and whether the app
        ends the day by itself then. Read by the app; the night job can read the same file."""
        f = paths.runs_root() / self.session.world / "schedule.json"
        doc = {**self.DEFAULT_SCHEDULE, **(json.loads(f.read_text()) if f.exists() else {})}
        if ends_at is not None:
            hh, _, mm = str(ends_at).partition(":")
            if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
                raise ValueError("ends_at must be HH:MM")
            doc["ends_at"] = f"{int(hh):02d}:{int(mm):02d}"
        if auto is not None:
            doc["auto"] = bool(auto)
        if ends_at is not None or auto is not None:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({**doc, "set_at": now_iso()}, indent=1) + "\n")
        return doc

    # --- Settings -> Model ---
    def hardware(self):
        if time.time() - self._hardware[0] > 60:
            self._hardware = (time.time(), modelsetup.hardware())
        return self._hardware[1]

    def model_state(self):
        """What Settings -> Model shows: the model in use now, this machine, the saved choice, the server this app
        started for a model file, the download, and where model files go."""
        return {"current": {**self.model_source, "url": self.client.endpoint, "kind": self.client.kind,
                            "model": self.client.model_id, "healthy": self.client.health()},
                "hardware": self.hardware(), "saved": modelsetup.saved(), "local_server": self.local_server.status(),
                "llama_server": modelsetup.llama_server_binary(), "download": self.download.state,
                "models_dir": str(modelsetup.models_dir()), "hf_repo": modelsetup.HF_REPO}

    def use_model(self, doc, persist=True):
        """Switch the agent's model: {"source": "server", "url"} after a probe, or {"source": "gguf", "path"}, which
        starts llama-server on the file here and switches when it is healthy. Saved to runs/_settings/model.json."""
        source = doc.get("source")
        if source == "server":
            pr = modelsetup.probe(doc.get("url"))
            if not pr["chat"]:
                raise ValueError(" ".join(pr["lines"]))
            with self.lock:
                self.client.use_server(pr["url"], pr["kind"], None if pr["kind"] == "llama.cpp" else pr["model"])
                self.model_source = {"source": "server", "url": pr["url"], "kind": pr["kind"], "from": "settings"}
            if persist:
                modelsetup.save({"source": "server", "url": pr["url"], "kind": pr["kind"], "model": pr["model"]})
            return {**self.model_state(), "probe": pr}
        if source == "gguf":
            info = modelsetup.gguf_info(doc.get("path"))
            if not info["ok"]:
                raise ValueError(" ".join(info["lines"]))
            started = self.local_server.start(info["path"])
            self.model_source = {"source": "gguf", "path": info["path"], "url": started["url"], "kind": "llama.cpp",
                                 "from": "settings", "starting": True}
            if persist:
                modelsetup.save({"source": "gguf", "path": info["path"], "url": started["url"]})
            threading.Thread(target=self._switch_when_healthy, args=(started["url"],), daemon=True).start()
            return {**self.model_state(), "file": info, "started": started}
        raise ValueError("source must be server or gguf")

    def _switch_when_healthy(self, url, wait_s=600):
        probe = GlimmerClient(url)
        t0 = time.time()
        while time.time() - t0 < wait_s and self.local_server.running():
            if probe.health():
                with self.lock:
                    self.client.use_server(url, "llama.cpp")
                    self.model_source = {**self.model_source, "starting": False, "ready_at": now_iso()}
                return
            time.sleep(2)
        self.model_source = {**self.model_source, "starting": False,
                             "error": "llama-server stopped before it was ready; see its log in runs/_settings"}

    def apply_saved_model(self):
        """At start without --endpoint: the model chosen in Settings, if any."""
        doc = modelsetup.saved()
        if not doc:
            return
        try:
            self.use_model(doc, persist=False)
        except (ValueError, RuntimeError, OSError) as e:
            self.model_source = {**self.model_source, "error": f"the saved model did not start: {e}"}

    def settings(self):
        """What the Settings screen shows, read only: the agent's tools, its reasoning setting, the notes backend, the
        model server and slot, and the serving rule the nights are judged by (runs/serving/serving-rule.json)."""
        s = self.session
        return {"tools": [t["function"]["name"] for t in s.tool_schemas], "reasoning": s.reasoning,
                "notes_backend": s.notes_backend, "endpoint": self.client.endpoint, "slot": self.client.slot,
                "serving_rule": serving_rule(), "serving": s.adapter_choice, "dials": s.dial_choice,
                "schedule": self.schedule(), "web_search": self.web_search_state()}

    def web_search_state(self):
        """Settings -> Connectors: the web search connector as set in the serving notes, and how much of its cap is
        used (the key's name stays here; the key itself is never read for this)."""
        from .web_search import searches_so_far, web_search_setting
        st = web_search_setting()
        return {"on": bool(st.get("on")), "provider": st.get("provider"), "max_results": st.get("max_results"),
                "cap": st.get("cap"), "used": searches_so_far(), "key_set": bool(st.get("key_name"))}

    def workspace(self):
        """What the agent's world tools hold for the open day (world_state.json): calendar events, tasks and email
        drafts, for the app's side panel. Read only; drafts are never sent."""
        st = self.session.world_state.state
        return {"world": self.session.world, "day": self.session.day, "date": self.session.date,
                "calendar": sorted(st.get("calendar") or [], key=lambda e: (e.get("date", ""), e.get("time", ""))),
                "tasks": st.get("tasks") or [], "emails": st.get("emails") or []}

    def raw_turn(self, turn, mode="reply"):
        """One turn as the model saw it: the messages before the turn's reply and the chat template rendered from them
        (the same tools and reasoning setting as the chat). mode "reply": the prompt ends where the reply starts;
        "continue": the turn's final answer is put after the answer channel's header, so a run continues it. Read only."""
        s = self.session
        turn = int(turn)
        msgs, reply = [s.system_message, s.context_message], ""
        for rec in s._records():
            if rec.get("role") == "user" and rec.get("turn", 0) <= turn:
                msgs.append({"role": "user", "content": rec["content"]})
            elif rec.get("role") == "assistant" and rec.get("turn", 0) < turn:
                msgs.extend(rec.get("messages") or [])
            elif rec.get("role") == "assistant" and rec.get("turn") == turn:
                reply = rec.get("content") or ""
        body = {"messages": msgs, "chat_template_kwargs": {"reasoning_strength": s.reasoning}, "tools": s.tool_schemas}
        prompt = self.client.post("/apply-template", body)["prompt"]
        if mode == "continue":
            prompt = prompt.removesuffix("<|start|>assistant") + "<|start|>assistant to=user<|message|>" + reply
        return {"turn": turn, "mode": mode, "messages": msgs, "prompt": prompt, "reply": reply}

    def raw_run(self, prompt, n_predict=400):
        """A completion from an edited prompt, on slot 1 with no prompt cache and the chat's adapter and dials. Nothing
        is written to the day log or the notes: the night never trains on it."""
        req = {"prompt": prompt, "n_predict": max(1, min(int(n_predict), 1200)), "temperature": 0.3, "id_slot": 1,
               "cache_prompt": False}
        req.update(self.client.scale_fields())
        t0 = time.time()
        r = self.client.post("/completion", req, timeout=180, headers={"X-Afterglow-Trace": "raw_view"})
        t = r.get("timings") or {}
        return {"content": r.get("content") or "", "stop": r.get("stop_type"), "total_s": round(time.time() - t0, 2),
                "tok_s": t.get("predicted_per_second"), "tokens": t.get("predicted_n")}

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

    def _feature(self, method):
        """A feature module's route (routes.py), if one claims this path; True when it answered."""
        u = urllib.parse.urlparse(self.path)
        fn, rest = routes.find(method, u.path)
        if not fn:
            return False
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if method == "POST" else None
        try:
            req = routes.Request(method, u.path, u.query, self.headers, raw)
            req.rest = rest
            out = fn(self.app, req)
        except ValueError as e:
            return self._json({"error": str(e)}, 400) or True
        except RuntimeError as e:
            return self._json({"error": str(e)}, 409) or True
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500) or True
        if isinstance(out, routes.Stream):
            self._stream(out)
        elif isinstance(out, tuple):
            self._json(out[0], out[1])
        else:
            self._json(out)
        return True

    def _stream(self, st):
        if st.lock and not self.app.lock.acquire(blocking=False):
            return self._json({"error": "Afterglow is still answering the previous message"}, 409)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            for ev in st.events:
                self.wfile.write(b"data: " + json.dumps(ev, ensure_ascii=False).encode() + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if st.lock:
                self.app.lock.release()
        self.close_connection = True

    def do_GET(self):
        if self._feature("GET"):
            return
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/state":
            return self._json(self.app.state())
        if path == "/api/voices":
            return self._json(self.app.voices())
        if path == "/api/raw":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            turn = q.get("turn", [""])[0]
            if not turn.isdigit():
                return self._json({"error": "turn must be a number"}, 400)
            try:
                return self._json(self.app.raw_turn(int(turn), q.get("mode", ["reply"])[0]))
            except GlimmerError as e:
                return self._json({"error": f"the model server did not render the template: {e}"}, 502)
        if path == "/api/attachments":
            return self._json({"attachments": self.app.attachments()})
        if path == "/api/settings":
            return self._json(self.app.settings())
        if path == "/api/model":
            return self._json(self.app.model_state())
        if path == "/api/schedule":
            return self._json(self.app.schedule())
        if path == "/api/model/local":
            return self._json({"files": modelsetup.find_local_ggufs(), "models_dir": str(modelsetup.models_dir())})
        if path == "/api/model/hf":
            try:
                return self._json({"repo": modelsetup.HF_REPO, "files": modelsetup.hf_files()})
            except (OSError, ValueError) as e:
                return self._json({"error": f"Hugging Face did not answer: {e}"}, 502)
        if path == "/api/model/download":
            return self._json(self.app.download.state)
        if path == "/api/workspace":
            return self._json(self.app.workspace())
        if path == "/api/days":
            return self._json({"current": {"world": self.app.session.world, "day": self.app.session.day},
                               "people": self.app.recorded_days()})
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
        elif path == "/classic":  # the day page from before the app (web/classic.html, app.js, app.css): the fallback
            path = "/classic.html"
        elif "." not in path.rsplit("/", 1)[-1]:  # the app's own screens (/memory, /nights, ...): one page
            path = "/index.html"
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
        if self._feature("POST"):
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/chat":
                b = self._body()
                try:
                    text, extra = self.app.message_with_attachments((b.get("text") or "").strip(), b.get("attachments") or [])
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
                return self._chat(text, extra)
            if path.startswith("/api/model/"):
                b = self._body()
                try:
                    if path == "/api/model/probe":
                        return self._json(modelsetup.probe(b.get("url")))
                    if path == "/api/model/file":
                        return self._json({**modelsetup.gguf_info(b.get("path")), "hardware": self.app.hardware()})
                    if path == "/api/model/use":
                        return self._json(self.app.use_model(b))
                    if path == "/api/model/download":
                        return self._json(self.app.download.start(b.get("file")))
                    if path == "/api/model/download/pause":
                        return self._json(self.app.download.pause())
                    if path == "/api/model/stop":
                        self.app.local_server.stop()
                        return self._json(self.app.model_state())
                except (ValueError, RuntimeError) as e:
                    return self._json({"error": str(e)}, 400)
                except OSError as e:
                    return self._json({"error": f"{type(e).__name__}: {e}"}, 502)
                return self._json({"error": "not found"}, 404)
            if path == "/api/schedule":
                b = self._body()
                try:
                    return self._json(self.app.schedule(b.get("ends_at"), b.get("auto")))
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
            if path == "/api/attachments":
                b = self._body()
                try:
                    return self._json(self.app.attach(b.get("name"), b.get("text")))
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
            if path == "/api/ask":
                q = (self._body().get("question") or "").strip()
                if not q:
                    return self._json({"error": "empty question"}, 400)
                return self._json(self.app.ask_notes_off(q))
            if path == "/api/raw/run":
                b = self._body()
                if not (b.get("prompt") or "").strip():
                    return self._json({"error": "empty prompt"}, 400)
                return self._json(self.app.raw_run(b["prompt"], b.get("n_predict") or 400))
            if path == "/api/proposal":
                b = self._body()
                return self._json(record_proposal(b["world"], b["day"], b["axis"], b["toward"], b["answer"]))
            if path == "/api/voice":
                b = self._body()
                try:
                    return self._json(self.app.set_voice(preset=b.get("preset"), voice=b.get("voice")))
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
            if path == "/api/voice/describe":
                s = self.app.session
                rec = dials.request_voice(s.world, s.day, self._body().get("text", ""))
                return self._json(rec, 200 if rec["status"] == "queued" else 400)
            if path == "/api/snapshot/restore":
                threading.Thread(target=self.app.restore_snapshot, daemon=True).start()
                return self._json({"started": True})
            if path == "/api/night-link/answer":
                b = self._body()
                return self._json(self.app.answer_night_proposal(b.get("old_id", ""), b.get("new_id", ""),
                                                                 b.get("answer", "")))
            if path == "/api/day/open":
                b = self._body()
                if self.app.lock.locked():
                    return self._json({"error": "Afterglow is busy; open the day again in a moment"}, 409)
                try:
                    return self._json(self.app.open_day(b.get("world") or self.app.session.world, b.get("day")))
                except (ValueError, TypeError) as e:
                    return self._json({"error": str(e)}, 400)
            if path in ("/api/notes/edit", "/api/notes/forget"):
                b = self._body()
                try:
                    return self._json(self.app.change_note(b.get("id", ""), b.get("value"), forget=path.endswith("forget")))
                except (KeyError, ValueError) as e:
                    return self._json({"error": str(e).strip("'")}, 400)
            if path == "/api/profile":
                if self.app.lock.locked():
                    return self._json({"error": "Afterglow is busy; try again in a moment"}, 409)
                b = self._body()
                try:
                    return self._json(self.app.create_profile(b.get("name"), b.get("about")))
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
            if path == "/api/night-link/undo":
                return self._json(self.app.undo_night_link(self._body().get("old_id", "")))
            if path == "/api/day/end":
                with self.app.lock:
                    notes = self.app.session.end_day(flush_timeout=30)  # a slow vault never holds the page
                return self._json({"notes": notes})
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        return self._json({"error": "not found"}, 404)

    def _chat(self, text, extra=None):
        if not text:
            return self._json({"error": "empty message"}, 400)
        if not self.app.lock.acquire(blocking=False):
            return self._json({"error": "Afterglow is still answering the previous message"}, 409)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            for ev in self.app.session.run_turn(text, extra=extra):
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
    ap.add_argument("--endpoint", default=None,
                    help="the model server (default: the model chosen in Settings, runs/_settings/model.json, else "
                         "AFTERGLOW_ENDPOINT or the project's default)")
    ap.add_argument("--port", type=int, default=8650)
    ap.add_argument("--slot", type=int, default=0,
                    help="the server slot the chat runs on (default 0, the app's; 1 for a second instance, so the "
                         "first one's cached slot 0 stays warm)")
    ap.add_argument("--reasoning", default="low", choices=["minimal", "low", "medium", "high"])
    ap.add_argument("--no-restore", action="store_true", help="skip the KV snapshot restore and prefill at startup")
    ap.add_argument("--decide-supersede", action=argparse.BooleanOptionalAction, default=None,
                    help="force the supersede decision at remember time on or off (default: the serving notes, "
                         "runs/serving/serving-rule.json supersede_decision)")
    ap.add_argument("--decide-adapter", help="the decision adapter's path on the serving box (default: base weights)")
    ap.add_argument("--web-search", action=argparse.BooleanOptionalAction, default=None,
                    help="force the search_web tool on or off (default: the serving notes, runs/serving/"
                         "serving-rule.json web_search; off unless set)")
    ap.add_argument("--decide-route", action=argparse.BooleanOptionalAction, default=None,
                    help="force the route decision at recall time on or off (default: the serving notes, "
                         "runs/serving/serving-rule.json route_decision)")
    ap.add_argument("--notes-backend", default="local", choices=NOTES_BACKENDS,
                    help="local (default): notes.json only; vault: notes.json plus the Oneiron vault, which answers "
                         "the recall tool (needs the oneiron extra and the vault credentials, app/README.md)")
    a = ap.parse_args()
    saved_model = None if a.endpoint else modelsetup.saved()
    a.endpoint = a.endpoint or GlimmerClient().endpoint
    Handler.app = App(a.world, a.day, a.endpoint, date=a.date, reasoning=a.reasoning, restore=not a.no_restore,
                      decide_supersede=a.decide_supersede, decide_adapter=a.decide_adapter,
                      decide_route=a.decide_route, web_search=a.web_search, slot=a.slot, notes_backend=a.notes_backend)
    if saved_model:
        Handler.app.apply_saved_model()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    srv.daemon_threads = True
    print(f"Afterglow: http://127.0.0.1:{a.port}  (world {a.world}, day {a.day}, Glimmer at {a.endpoint}, "
          f"notes {a.notes_backend})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
