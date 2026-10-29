"""The day agent: one user, one world, one day. Glimmer answers with tool calls; every turn goes to the day log.

runs/<world>/<day>/daylog.jsonl  one record per turn: {"ts", "turn", "role", "content", ...}
                                 assistant records add "messages" (every assistant and tool message of the
                                 turn, as sent to the model), "tool_calls", "notes_written", timings.
runs/<world>/<day>/notes.json    the notes (notes.py), seeded from the previous day's notes.
runs/<world>/<day>/world_state.json  calendar and tasks.

The prompt has two parts. The system prompt (persona header, rules, tools) is the same for every day
of a world, so its KV is the one snapshot (snapshot.py). The day context (the date, and the notes from
earlier days when notes are in context) is the first user-role message of the day; it is prefilled
after a restore, with the day so far.
"""
import datetime as dt
import json
import sys
import time
from pathlib import Path

from . import paths
from .dials import accepted_dials
from .llm import GlimmerClient, GlimmerError, world_adapter_choice
from .notes import LocalJsonNotes
from .tools import TOOL_SCHEMAS, ToolBox, WorldState

JST = dt.timezone(dt.timedelta(hours=9))
MAX_ROUNDS = 8
LINK_P = 0.9  # a one-pass "replaces" this sure links at once (decide/decider.py SUPERSEDE_ACCEPT_P)
ASK_AGAIN_P = 0.5  # from here up to LINK_P the decision is asked once more with reasoning on (when switched on)


def serving_setting(name, day):
    """One decision's entry in the serving notes (runs/serving/serving-rule.json): ({...} or {}, on for this day)."""
    f = paths.runs_root() / "serving" / "serving-rule.json"
    conf = (json.loads(f.read_text()).get(name) if f.exists() else None) or {}
    return conf, conf.get("from_day") is not None and int(day) >= int(conf["from_day"])


def supersede_setting(day):
    """The serving notes' setting for the supersede decision at remember time ("supersede_decision": {"from_day",
    "link_p", "reasoned", "weights", "evidence"}): (on for this day, link_p, reasoned). Absent: off."""
    conf, on = serving_setting("supersede_decision", day)
    return on, float(conf.get("link_p", LINK_P)), bool(conf.get("reasoned", False))


def route_setting(day):
    """The serving notes' setting for the route decision at recall time ("route_decision": {"from_day", "adapter",
    "threshold", "evidence"}; adapter = the decision adapter's file name, found by path on the server, none = base
    weights): (on for this day, adapter file name or None, threshold). Absent: off."""
    conf, on = serving_setting("route_decision", day)
    return on, conf.get("adapter"), float(conf.get("threshold", 0.0))
REASONING_TOKENS = 1024  # the reasoned call's budget (decide/decider.py); reasoning cut there never links

SYSTEM_PROMPT = """You are Afterglow, {who} personal assistant. You run on {owner} own computer.

How you work:
- When the user tells you facts about their work, projects, people, places, schedule or a procedure, or corrects one, save each fact with remember before you reply: one remember call per fact (a message can hold several). To correct a fact, find it in your notes and pass that note's key as replaces.
- Facts often come inside a request or a question, even one you cannot act on: a name, place, event, code, file path, date or version. Save those too.
- When the user names something (a device, room, channel, project, event), save what the name stands for, not only today's news about it: key "label printer name", value "Puffin".
- The key says what the fact is to the user, in plain words ("build server name", not "afternoon location"). The value copies names, codes, numbers, paths and dates exactly as the user wrote them.
- Before you answer a question about something the user told you before, use recall.
- Use the calendar and tasks tools for the user's schedule and to-dos.
- Answer in one to three short sentences, in plain words.
- You keep the user's facts and procedures. You do not give advice about the user's health, mood or personality."""

DAY_CONTEXT = "Context for today, not a request: today is {date_line}."


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


def system_message(world_doc):
    """The world's stable prefix: the same for every day, so one KV snapshot serves until the weights change."""
    name = (world_doc.get("persona") or {}).get("name")
    who, owner = (f"{name}'s", f"{name.split()[0]}'s") if name else ("the user's", "the user's")
    return {"role": "system", "content": SYSTEM_PROMPT.format(who=who, owner=owner)}


class DaySession:
    def __init__(self, world, day, client=None, *, date=None, reasoning="low", notes_in_context=True,
                 notes_backend="local", temperature=0.3, decide_supersede=None, decide_reasoned=None,
                 decide_adapter=None, decide_route=None, dials=None):
        self.world, self.day = world, int(day)
        self.temperature = temperature
        self.client = client or GlimmerClient()
        # The client serves this world: its adapter (from the serving manifest) is the one it scales.
        self.adapter_choice = world_adapter_choice(world)  # the serving rule: latest update passing every check
        self.adapter = self.adapter_choice["entry"]
        self.client.adapter_path = self.adapter["path"] if self.adapter else None
        # the steering dials the user accepted on an earlier morning report (dials.py); none = no control vectors sent
        self.dial_choice = accepted_dials(world, day) if dials is None else {"dials": list(dials), "skipped": []}
        self.client.dials = self.dial_choice["dials"]
        self.reasoning = reasoning
        self.notes_in_context = notes_in_context
        self.world_doc = load_world(world)
        self.dir = paths.day_dir(world, self.day)
        prev = previous_day_dir(world, self.day)
        self.notes = LocalJsonNotes(self.dir / "notes.json", seed_from=prev / "notes.json" if prev else None)
        if notes_backend == "oneiron":
            from .notes import OneironNotes

            self.notes = OneironNotes(self.notes, world, self.day)
        seed = None
        if prev and (prev / "world_state.json").exists():
            seed = json.loads((prev / "world_state.json").read_text())
        self.world_state = WorldState(self.dir / "world_state.json", seed=seed or self.world_doc)
        # None = the serving notes decide (supersede_setting); True/False force it (tests, experiments)
        on, self.link_p, reasoned = supersede_setting(self.day)
        self.decide_reasoned = reasoned if decide_reasoned is None else decide_reasoned
        on = on if decide_supersede is None else decide_supersede
        route_on, route_adapter, self.route_threshold = route_setting(self.day)
        route_on = route_on if decide_route is None else decide_route
        self.tools = ToolBox(self.notes, self.world_state, day=self.day,
                             decide_supersede=self._supersede_decider(decide_adapter) if on else None,
                             decide_route=self._route_decider(route_adapter) if route_on else None)
        self.daylog_path = self.dir / "daylog.jsonl"
        self.session_path = self.dir / "session.json"
        self.system_message = system_message(self.world_doc)
        self._init_day_context(date)
        self.turn = 0
        self.messages = [self.system_message, self.context_message]
        self._replay()

    def _supersede_decider(self, adapter_path=None):
        """The supersede decision at remember time (decide/decider.py; base weights, or the decision adapter at 1
        with every other adapter at 0), on slot 1, so its uncached calls never overwrite the user's cached
        conversation in slot 0. One pass over the top earlier notes: "replaces" at p >= LINK_P links at once;
        LINK_P > p >= ASK_AGAIN_P asks once more with reasoning on and links when the reasoned answer says
        "replaces"; lower keeps both. Returns the ToolBox hook."""
        sys.path.insert(0, str(paths.REPO))
        from decide.decider import Decider
        from decide.prompts import note_line

        one_pass = Decider(self.client.endpoint, adapter_path=adapter_path, slot=1, trace="supersede_decision")
        # thresholds above 1: pair() always reasons first, then reads the same labels
        reasoned = Decider(self.client.endpoint, adapter_path=adapter_path, slot=1, trace="supersede_decision_reasoned",
                           thresholds={"supersede": 1.01})
        p_replaces = lambda d: d["p"] if d["yes"] else 1.0 - d["p"]

        def decide(new_note):
            earlier = [n for n in self.notes.all() if n["id"] != new_note["id"]]
            r = one_pass.supersede_link(earlier, new_note, accept_p=self.link_p)
            best = max(r["decisions"], key=p_replaces, default=None)
            by_id = {n["id"]: n for n in earlier}
            decided = {"by": "one-pass decision", "p": best and round(p_replaces(best), 4),
                       "latency_s": round(r["ms"] / 1000, 3), "candidate": best and best["candidate"]}
            note = by_id.get(r["link"])
            if note is None and best and ASK_AGAIN_P <= p_replaces(best) < self.link_p and self.decide_reasoned:
                t = reasoned.pair("supersede", note_line(by_id[best["candidate"]]), note_line(new_note))
                # reasoning that ended at its budget did not finish: undecided, never an answer
                cut = t.get("reason") == "reasoning_cap" or (t.get("reasoning_tokens") or 0) >= REASONING_TOKENS
                decided.update(by="thought it through", latency_s=round(decided["latency_s"] + t["ms"] / 1000, 3),
                               reasoned={"pick": None if cut else t["pick"], "p": None if cut else t["p"],
                                         "ms": t["ms"], "reasoning_tokens": t.get("reasoning_tokens")})
                if cut:
                    decided.update(reason="reasoning_cap", outcome="undecided: keep both")
                elif t["yes"]:
                    note = by_id[best["candidate"]]
            return {"note": note, "decided": decided, "decisions": r["decisions"]}

        return decide

    def _route_decider(self, adapter_file=None):
        """The route decision at recall time (decide/decider.py route, prompt route-v1): one pass over every active
        note in store order picks the note that answers the user's question, or none of them (ask the user). The
        decision adapter (adapter_file, found by path on the server; None = base weights) runs at 1 with every other
        adapter at 0, on slot 1, so its uncached call never overwrites the user's cached conversation in slot 0.
        Below the threshold it looks up instead (the recall search). Returns the ToolBox hook."""
        sys.path.insert(0, str(paths.REPO))
        from decide.decider import Decider

        name = (self.world_doc.get("persona") or {}).get("name")
        first = name.split()[0] if name else "the user"  # decide/rows.py: the persona's first name
        made = []

        def decider():
            if not made:
                path = None
                if adapter_file:
                    found = [a["path"] for a in self.client.adapters() if Path(a["path"]).name == adapter_file]
                    if len(found) != 1:
                        raise GlimmerError(f"route decision adapter {adapter_file}: {len(found)} loaded")
                    path = found[0]
                made.append(Decider(self.client.endpoint, adapter_path=path, slot=1, trace="route_decision",
                                    thresholds={"route": self.route_threshold}))
            return made[0]

        def decide(question):
            active = self.notes.all()
            r = decider().route(first, [{"id": n["id"], "key": n["key"], "value": n["value"]} for n in active],
                                question)
            by_id = {n["id"]: n for n in active}
            decided = {"by": "one-pass decision" if r["path"] == "one_pass" else "looked up (below the threshold)",
                       "pick": r["pick"], "p": r["p"], "label_mass": r["label_mass"],
                       "latency_s": round(r["ms"] / 1000, 3), "options": len(active) + 1, "prompt_id": r["prompt_id"],
                       "adapter": adapter_file or "base"}
            return {"note": by_id[r["note"]["id"]] if r["note"] else None, "ask": r["ask"],
                    "look_up": r["path"] != "one_pass", "decided": decided}

        return decide

    # --- the prompt prefix ---
    def _init_day_context(self, date):
        """The day context is written once per day (session.json), so a restarted app sends the same tokens."""
        if self.session_path.exists():
            s = json.loads(self.session_path.read_text())
            if s.get("notes_in_context") == self.notes_in_context:
                self.context_message = s["context_message"]
                self.date = s["date"]
                return
        d = dt.date.fromisoformat(date) if date else dt.datetime.now(JST).date()
        self.date = d.isoformat()
        notes = [n for n in self.notes.all() if n.get("day") != self.day] if self.notes_in_context else []
        text = DAY_CONTEXT.format(date_line=f"{d.strftime('%A')} {d.isoformat()}") + render_notes_block(notes)
        self.context_message = {"role": "user", "content": text}
        self.session_path.write_text(json.dumps({"world": self.world, "day": self.day, "date": self.date,
                                                 "notes_in_context": self.notes_in_context,
                                                 "notes_in_context_count": len(notes),
                                                 "context_message": self.context_message}, indent=1))

    def prefix_messages(self):
        """The world's stable prefix: what the KV snapshot holds."""
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
    def run_turn(self, text, source="user", extra=None):
        """Yield UI events for one user turn; the last is {"type": "turn_done", ...}. `extra` fields go on the day
        log's user record (a scripted day's "feedback" label, for the night's steering proposals)."""
        self.turn += 1
        turn = self.turn
        self._log({"ts": now_iso(), "turn": turn, "role": "user", "content": text, "source": source, **(extra or {})})
        self.messages.append({"role": "user", "content": text})
        new_msgs, tool_calls, notes_written, rounds = [], [], [], []
        final = ""
        retry = None  # why the reply came from the answer channel: empty_reply, repeated_tool_call or max_rounds
        seen_calls = set()
        n_decisions, n_routes = len(self.tools.decisions), len(self.tools.route_decisions)
        self.tools.question = text  # the route decision at recall time reads the user's own words, not the query
        t0 = time.time()
        error = None
        for r in range(MAX_ROUNDS):
            done = None
            try:
                for ev in self.client.stream_chat(self.messages, TOOL_SCHEMAS, reasoning=self.reasoning,
                                                  temperature=self.temperature):
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
                if not final:
                    retry = "empty_reply"
                break
            calls = {(tc["function"]["name"], tc["function"]["arguments"] or "") for tc in msg["tool_calls"]}
            if calls <= seen_calls:  # the same calls again: the work is done, the reply is due
                self.messages.pop()
                new_msgs.pop()
                retry = "repeated_tool_call"
                break
            seen_calls |= calls
            if r == MAX_ROUNDS - 1:
                retry = "max_rounds"
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
        if retry and error is None:
            # No reply in the answer channel (it stayed in the reasoning channel, or the tool calls went round):
            # ask again in the answer channel. Counted per turn in the day log and on the day page, never hidden.
            empty = new_msgs and new_msgs[-1]["role"] == "assistant" and not new_msgs[-1].get("tool_calls")
            try:
                fix = self.client.answer_channel(self.messages[:-1] if empty else self.messages, TOOL_SCHEMAS,
                                                 reasoning=self.reasoning, temperature=self.temperature, why=retry)
                final = fix["content"]
                if empty:
                    new_msgs[-1]["content"] = final
                else:
                    msg = {"role": "assistant", "content": final}
                    self.messages.append(msg)
                    new_msgs.append(msg)
                rounds.append({"finish_reason": fix["stop_type"], "answer_channel_retry": retry,
                               "total_s": fix["total_s"], "timings": fix["timings"]})
                yield {"type": "content", "text": final, "round": len(rounds) - 1, "answer_channel_retry": retry}
            except Exception as e:
                error = f"answer channel retry: {type(e).__name__}: {e}"
                yield {"type": "error", "message": error}
        first = rounds[0] if rounds else {}
        last_t = (rounds[-1].get("timings") or {}) if rounds else {}
        rec = {"ts": now_iso(), "turn": turn, "role": "assistant", "content": final, "messages": new_msgs,
               "tool_calls": tool_calls, "notes_written": notes_written, "rounds": rounds,
               "ttft_s": first.get("ttft_s"), "total_s": round(time.time() - t0, 3),
               "tok_s": last_t.get("predicted_per_second"), "answer_channel_retry": retry, "error": error}
        if len(self.tools.decisions) > n_decisions:  # the one-pass supersede decisions of this turn, with p and ms
            rec["supersede_decisions"] = self.tools.decisions[n_decisions:]
        if self.client.dials:  # the control vectors every request of this turn carried
            rec["dials"] = [{k: d[k] for k in ("id", "axis", "toward", "scale")} for d in self.client.dials]
        if len(self.tools.route_decisions) > n_routes:  # the turn's route decision at recall time, with p and ms
            rec["route_decisions"] = self.tools.route_decisions[n_routes:]
        live = hasattr(self.notes, "witness_turn") and final
        if live:  # push_day skips a turn the live path sent into the same conversation
            rec["witnessed_live"] = self.notes.conversation
        self._log(rec)
        if live:
            self.notes.witness_turn(text, final)
        yield {"type": "turn_done", "turn": turn, "content": final, "notes_written": notes_written,
               "answer_channel_retry": retry,
               "ttft_s": rec["ttft_s"], "total_s": rec["total_s"], "tok_s": rec["tok_s"], "error": error,
               "cache_n": (first.get("timings") or {}).get("cache_n"),
               "prompt_n": (first.get("timings") or {}).get("prompt_n")}

    def ask(self, text, source="user", extra=None):
        """Run a turn to the end; return the final event (for scripts and the simulator)."""
        last = None
        for ev in self.run_turn(text, source=source, extra=extra):
            last = ev
        return last

    def end_day(self):
        """Export the day's notes (runs/<world>/<day>/notes.json) for the night job."""
        if hasattr(self.notes, "flush"):
            self.notes.flush()
        return self.notes.export_day(self.dir / "notes.json", world=self.world, day=self.day)
