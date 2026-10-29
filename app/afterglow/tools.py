"""The agent's tools: notes (remember, recall) and two world tools from the synthetic world (calendar, tasks).

World state lives in runs/<world>/<day>/world_state.json. A new day starts from the previous day's
state, or from the "calendar" / "tasks" seed in world.json when there is none.
"""
import json
import os
from pathlib import Path

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "remember",
        "description": "Save a durable fact about the user's work, projects, people, places or procedures, also "
                       "one mentioned inside a request. To correct a fact, pass the key of the note it changes as "
                       "replaces; the old value is marked stale.",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string", "description": "what the fact is to the user, in plain words, e.g. 'label printer name'"},
            "value": {"type": "string", "description": "the fact, with names, codes, numbers, paths and dates exactly as "
                                                       "the user wrote them"},
            "replaces": {"type": "string", "description": "only for a correction: the key of the note this fact "
                                                          "changes, copied from your notes"}},
            "required": ["key", "value"]}}},
    {"type": "function", "function": {
        "name": "recall",
        "description": "Search your saved notes. Use it before answering a question about something the user "
                       "told you earlier.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "words to look for"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "calendar.list_events",
        "description": "List the user's calendar events on one date.",
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD"}},
            "required": ["date"]}}},
    {"type": "function", "function": {
        "name": "calendar.add_event",
        "description": "Add an event to the user's calendar.",
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "time": {"type": "string", "description": "HH:MM, 24-hour"},
            "title": {"type": "string"},
            "place": {"type": "string"}},
            "required": ["date", "time", "title"]}}},
    {"type": "function", "function": {
        "name": "tasks.list",
        "description": "List the user's open tasks.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "tasks.add",
        "description": "Add a task to the user's task list.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"},
            "due": {"type": "string", "description": "YYYY-MM-DD, optional"}},
            "required": ["title"]}}},
    {"type": "function", "function": {
        "name": "tasks.complete",
        "description": "Mark a task done.",
        "parameters": {"type": "object", "properties": {
            "task_id": {"type": "string"}},
            "required": ["task_id"]}}},
]


class WorldState:
    def __init__(self, path, seed=None):
        self.path = Path(path)
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
        else:
            seed = seed or {}
            self.state = {"calendar": list(seed.get("calendar") or []), "tasks": list(seed.get("tasks") or [])}
            self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, ensure_ascii=False))
        os.replace(tmp, self.path)


def next_id(prefix, items):
    nums = [int(x["id"][len(prefix):]) for x in items if str(x.get("id", "")).startswith(prefix)
            and x["id"][len(prefix):].isdigit()]
    return f"{prefix}{max(nums, default=0) + 1:03d}"


class ToolBox:
    """Runs one tool call. Returns (result_dict, notes_written)."""

    def __init__(self, notes, world_state, day=None, decide_supersede=None, decide_route=None, web_search=None):
        self.notes = notes
        self.world = world_state
        self.day = day
        # decide_supersede(new_note) -> {"note": the earlier note it replaces or None, "decided": {...}, "decisions"}:
        # the model's decision on whether a fact saved under a new key replaces an earlier note (agent.py applies the
        # bars). With no note, both stay and the night's consolidation sees the pair.
        self.decide_supersede = decide_supersede
        self.decisions = []  # every supersede decision this session, for the day log
        # decide_route(question) -> {"note": the active note that answers it or None, "ask", "look_up", "decided"}:
        # at recall time, which note answers the user's question (agent.py). ask = none does: the agent asks the user.
        self.decide_route = decide_route
        self.question = None  # the user's words this turn (agent.py sets it); the route decision reads them
        self.route_decisions = []  # one per turn that recalls, for the day log
        self._routed = None  # (turn, decision): a second recall in the same turn reuses the decision
        # web_search(query) -> [{"title", "snippet", "url"}]: the search_web tool (web_search.py), None when it is off
        self.web_search = web_search
        self.web_searches = []  # every search this session, query and results, for the day log

    def route(self, turn):
        """The turn's route decision, made once per turn; a failed call is logged and the recall search answers."""
        if self._routed and self._routed[0] == turn:
            return self._routed[1]
        try:
            d = self.decide_route(self.question)
        except Exception as e:  # the server or the adapter is missing: never fails the turn
            d = {"note": None, "ask": False, "look_up": True, "error": f"{type(e).__name__}: {e}"}
        self._routed = (turn, d)
        self.route_decisions.append({"turn": turn, "question": self.question, "note": d["note"] and d["note"]["id"],
                                     "ask": d["ask"], **(d.get("decided") or {}),
                                     **({"error": d["error"]} if d.get("error") else {})})
        return d

    def run(self, name, args, turn=None):
        try:
            if name == "remember":
                r = self.notes.remember(str(args["key"]), str(args["value"]), day=self.day, turn=turn,
                                        replaces=str(args.get("replaces") or "") or None)
                out = {"saved": r["note"]["id"], "key": r["note"]["key"], "value": r["note"]["value"]}
                if r["superseded"]:
                    out["replaced"] = {"id": r["superseded"]["id"], "old_value": r["superseded"]["value"]}
                elif not r["unchanged"] and self.decide_supersede:  # the model decides; linked only when sure
                    d = self.decide_supersede(r["note"])
                    decided = d["decided"]
                    if d.get("note") is not None:
                        self.notes.link(r["note"], d["note"], decided)
                        out["replaced"] = {"id": d["note"]["id"], "old_value": d["note"]["value"], "decided": decided}
                    elif d.get("decisions"):
                        out["decision"] = dict(decided, linked=False)  # both notes stay; the night sees the pair
                        self.notes.record_decision(r["note"], dict(decided, linked=False))  # for the day page
                    self.decisions.append({"note": r["note"]["id"], **decided, "linked": "replaced" in out,
                                           "calls": d.get("decisions")})
                elif not r["unchanged"] and hasattr(self.notes, "correction_candidates"):
                    maybe = self.notes.correction_candidates(r["note"])
                    if maybe:  # the agent decides; a second remember with replaces links the old note
                        out["maybe_replaces"] = [{"key": n["key"], "value": n["value"]} for n in maybe]
                        out["note"] = ("saved as a new fact. If it corrects one of maybe_replaces, call remember "
                                       "again with the same key and value and replaces set to that note's key.")
                if r["unchanged"]:
                    out["note"] = "already saved"
                return out, ([] if r["unchanged"] else [r["note"]["id"]])
            if name == "recall":
                shown = lambda hits: [{"key": n["key"], "value": n["value"], "saved": n["updated_at"][:10]} for n in hits]
                d = self.route(turn) if self.decide_route and self.question else None
                if d and not d["look_up"]:
                    if d["note"] is not None:
                        return {"notes": shown([d["note"]]), "decided": d["decided"]}, []
                    return {"notes": [], "not_in_notes": "None of your notes answers this. Ask the user; do not guess.",
                            "decided": d["decided"]}, []
                out = {"notes": shown(self.notes.recall(str(args.get("query", ""))))}
                if d:  # below the threshold, or the decision failed: the search answers
                    out["decided"] = d.get("decided") or {"error": d.get("error")}
                return out, []
            if name == "search_web":
                query = str(args.get("query", "")).strip()
                if self.web_search is None:
                    return {"error": "web search is off"}, []
                if not query:
                    return {"error": "an empty query"}, []
                try:
                    results = self.web_search(query)
                except Exception as e:  # never the key: web_search.py errors carry the status only
                    self.web_searches.append({"turn": turn, "query": query, "error": str(e)})
                    return {"error": str(e)}, []
                self.web_searches.append({"turn": turn, "query": query, "results": results})
                return {"query": query, "results": results}, []
            st = self.world.state
            if name == "calendar.list_events":
                ev = sorted((e for e in st["calendar"] if e.get("date") == args.get("date")),
                            key=lambda e: e.get("time", ""))
                return {"date": args.get("date"), "events": ev}, []
            if name == "calendar.add_event":
                e = {"id": next_id("E", st["calendar"]), "date": args["date"], "time": args["time"],
                     "title": args["title"]}
                if args.get("place"):
                    e["place"] = args["place"]
                st["calendar"].append(e)
                self.world.save()
                return {"added": e}, []
            if name == "tasks.list":
                return {"tasks": [t for t in st["tasks"] if not t.get("done")]}, []
            if name == "tasks.add":
                t = {"id": next_id("T", st["tasks"]), "title": args["title"], "done": False}
                if args.get("due"):
                    t["due"] = args["due"]
                st["tasks"].append(t)
                self.world.save()
                return {"added": t}, []
            if name == "tasks.complete":
                for t in st["tasks"]:
                    if t["id"] == args.get("task_id"):
                        t["done"] = True
                        self.world.save()
                        return {"done": t}, []
                return {"error": f"no task {args.get('task_id')}"}, []
            return {"error": f"unknown tool {name}"}, []
        except (KeyError, TypeError) as e:
            return {"error": f"bad arguments for {name}: {e}"}, []
