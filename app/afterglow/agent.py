"""The day agent: one user, one world, one day. Glimmer answers with tool calls; every turn goes to the day log.

runs/<world>/<day>/daylog.jsonl  one record per turn: {"ts", "turn", "role", "content", ...}
                                 assistant records add "messages" (every assistant and tool message of the
                                 turn, as sent to the model), "tool_calls", "notes_written", timings.
runs/<world>/<day>/notes.json    the notes (notes.py), seeded from the previous day's notes.
runs/<world>/<day>/world_state.json  calendar and tasks.

The system prompt is fixed for the whole day: it holds the notes from earlier days (when notes are in
context), so its KV can be prefilled once and restored from a snapshot (snapshot.py).
"""
import datetime as dt
import json
import time
from pathlib import Path

from . import paths
from .llm import GlimmerClient
from .notes import LocalJsonNotes
from .tools import TOOL_SCHEMAS, ToolBox, WorldState

JST = dt.timezone(dt.timedelta(hours=9))
MAX_ROUNDS = 6

SYSTEM_PROMPT = """You are Afterglow, a personal assistant that runs on the user's own computer.
Today is {date_line}.

How you work:
- When the user tells you a fact about their work, projects, people, places, schedule or a procedure, or corrects one, save it with remember: one fact per call, a short key, the value in a few words. To correct a fact, reuse its key.
- Before you answer a question about something the user told you before, use recall.
- Use the calendar and tasks tools for the user's schedule and to-dos.
- Answer in one to three short sentences, in plain words.
- You keep the user's facts and procedures. You do not give advice about the user's health, mood or personality."""


def now_iso():
    return dt.datetime.now(JST).isoformat(timespec="milliseconds")


def load_world(world):
    p = paths.worlds_root() / world / "world.json"
    return json.loads(p.read_text()) if p.exists() else {"world": world}


def previous_day_dir(world, day):
    root = paths.runs_root() / world
    days = sorted((int(p.name) for p in root.glob("*") if p.name.isdigit() and int(p.name) < int(day)), reverse=True)
    return root / str(days[0]) if days else None


def render_notes_block(notes):
    if not notes:
        return ""
    lines = "\n".join(f"- {n['key']}: {n['value']}" for n in notes)
    return f"\n\nYour notes from earlier days (the latest value of each fact):\n{lines}"


class DaySession:
    def __init__(self, world, day, client=None, *, date=None, reasoning="low", notes_in_context=True):
        self.world, self.day = world, int(day)
        self.client = client or GlimmerClient()
        self.reasoning = reasoning
        self.notes_in_context = notes_in_context
        self.world_doc = load_world(world)
        self.dir = paths.day_dir(world, self.day)
        prev = previous_day_dir(world, self.day)
        self.notes = LocalJsonNotes(self.dir / "notes.json", seed_from=prev / "notes.json" if prev else None)
        seed = None
        if prev and (prev / "world_state.json").exists():
            seed = json.loads((prev / "world_state.json").read_text())
        self.world_state = WorldState(self.dir / "world_state.json", seed=seed or self.world_doc)
        self.tools = ToolBox(self.notes, self.world_state, day=self.day)
        self.daylog_path = self.dir / "daylog.jsonl"
        self.session_path = self.dir / "session.json"
        self._init_system(date)
        self.turn = 0
        self.messages = [self.system_message]
        self._replay()

    # --- the fixed prefix of the day ---
    def _init_system(self, date):
        """The system prompt is written once per day (session.json) so a restarted app sends the same prefix."""
        if self.session_path.exists():
            s = json.loads(self.session_path.read_text())
            if s.get("notes_in_context") == self.notes_in_context:
                self.system_message = s["system_message"]
                self.date = s["date"]
                return
        d = dt.date.fromisoformat(date) if date else dt.datetime.now(JST).date()
        self.date = d.isoformat()
        notes = [n for n in self.notes.all() if n.get("day") != self.day] if self.notes_in_context else []
        text = SYSTEM_PROMPT.format(date_line=f"{d.strftime('%A')} {d.isoformat()}") + render_notes_block(notes)
        self.system_message = {"role": "system", "content": text}
        self.session_path.write_text(json.dumps({"world": self.world, "day": self.day, "date": self.date,
                                                 "notes_in_context": self.notes_in_context,
                                                 "notes_in_prefix": len(notes),
                                                 "system_message": self.system_message}, indent=1))

    def prefix_messages(self):
        """The persona context: what a fresh day's KV snapshot holds."""
        return [self.system_message]

    def _replay(self):
        if not self.daylog_path.exists():
            return
        for line in self.daylog_path.read_text().splitlines():
            rec = json.loads(line)
            if rec["role"] == "user":
                self.messages.append({"role": "user", "content": rec["content"]})
                self.turn = max(self.turn, rec["turn"])
            elif rec["role"] == "assistant":
                self.messages.extend(rec.get("messages") or [])

    def _log(self, rec):
        with open(self.daylog_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def history(self):
        """User-visible turns for the UI, rebuilt from the day log."""
        out = []
        if self.daylog_path.exists():
            for line in self.daylog_path.read_text().splitlines():
                out.append(json.loads(line))
        return out

    # --- one turn ---
    def run_turn(self, text, source="user"):
        """Yield UI events for one user turn; the last is {"type": "turn_done", ...}."""
        self.turn += 1
        turn = self.turn
        self._log({"ts": now_iso(), "turn": turn, "role": "user", "content": text, "source": source})
        self.messages.append({"role": "user", "content": text})
        new_msgs, tool_calls, notes_written, rounds = [], [], [], []
        final = ""
        t0 = time.time()
        error = None
        for r in range(MAX_ROUNDS):
            done = None
            try:
                for ev in self.client.stream_chat(self.messages, TOOL_SCHEMAS, reasoning=self.reasoning):
                    if ev["type"] == "done":
                        done = ev
                    else:
                        yield dict(ev, round=r)
            except Exception as e:  # the turn is logged even when the server fails
                error = f"{type(e).__name__}: {e}"
                yield {"type": "error", "message": error}
                break
            msg = done["message"]
            self.messages.append(msg)
            new_msgs.append(msg)
            rounds.append({"finish_reason": done["finish_reason"], "ttft_s": done["ttft_s"],
                           "total_s": done["total_s"], "timings": done["timings"]})
            yield {"type": "round_done", "round": r, "ttft_s": done["ttft_s"], "timings": done["timings"]}
            if not msg.get("tool_calls"):
                final = msg.get("content") or ""
                break
            for tc in msg["tool_calls"]:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except ValueError:
                    args = None
                if not isinstance(args, dict):
                    result, written = {"error": "arguments were not a JSON object"}, []
                else:
                    result, written = self.tools.run(name, args, turn=turn)
                tool_calls.append({"name": name, "arguments": args, "result": result})
                notes_written += written
                yield {"type": "tool_result", "name": name, "arguments": args, "result": result}
                for nid in written:
                    yield {"type": "note", "note": next(n for n in self.notes.notes if n["id"] == nid),
                           "replaced": result.get("replaced")}
                tmsg = {"role": "tool", "tool_call_id": tc["id"], "name": name,
                        "content": json.dumps(result, ensure_ascii=False)}
                self.messages.append(tmsg)
                new_msgs.append(tmsg)
        first = rounds[0] if rounds else {}
        last_t = (rounds[-1].get("timings") or {}) if rounds else {}
        rec = {"ts": now_iso(), "turn": turn, "role": "assistant", "content": final, "messages": new_msgs,
               "tool_calls": tool_calls, "notes_written": notes_written, "rounds": rounds,
               "ttft_s": first.get("ttft_s"), "total_s": round(time.time() - t0, 3),
               "tok_s": last_t.get("predicted_per_second"), "error": error}
        self._log(rec)
        yield {"type": "turn_done", "turn": turn, "content": final, "notes_written": notes_written,
               "ttft_s": rec["ttft_s"], "total_s": rec["total_s"], "tok_s": rec["tok_s"], "error": error,
               "cache_n": (first.get("timings") or {}).get("cache_n"),
               "prompt_n": (first.get("timings") or {}).get("prompt_n")}

    def ask(self, text, source="user"):
        """Run a turn to the end; return the final event (for scripts and the simulator)."""
        last = None
        for ev in self.run_turn(text, source=source):
            last = ev
        return last

    def end_day(self):
        """Export the day's notes (runs/<world>/<day>/notes.json); snapshot.py saves the KV."""
        return self.notes.export_day(self.dir / "notes.json", world=self.world, day=self.day)
