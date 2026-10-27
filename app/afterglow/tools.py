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
                       "one mentioned inside a request. Use the same key again to correct a fact; the old value is "
                       "marked stale.",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string", "description": "what the fact is to the user, in plain words, e.g. 'label printer name'"},
            "value": {"type": "string", "description": "the fact, with names, codes, numbers, paths and dates exactly as "
                                                       "the user wrote them"}},
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

    def __init__(self, notes, world_state, day=None):
        self.notes = notes
        self.world = world_state
        self.day = day

    def run(self, name, args, turn=None):
        try:
            if name == "remember":
                r = self.notes.remember(str(args["key"]), str(args["value"]), day=self.day, turn=turn)
                out = {"saved": r["note"]["id"], "key": r["note"]["key"], "value": r["note"]["value"]}
                if r["superseded"]:
                    out["replaced"] = {"id": r["superseded"]["id"], "old_value": r["superseded"]["value"]}
                if r["unchanged"]:
                    out["note"] = "already saved"
                return out, ([] if r["unchanged"] else [r["note"]["id"]])
            if name == "recall":
                hits = self.notes.recall(str(args.get("query", "")))
                return {"notes": [{"key": n["key"], "value": n["value"], "saved": n["updated_at"][:10]}
                                  for n in hits]}, []
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
