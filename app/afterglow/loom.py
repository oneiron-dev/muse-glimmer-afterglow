"""The loom: a day's conversation as a tree of branches, and the one line of it the night trains on.

A day log with no branch records is one line, "main", exactly as before. Editing a message or asking again forks:
  {"role": "fork", "branch": "b2", "parent": "main", "at_turn": 7}   b2 replaces turn 7 of main and what follows
User and assistant records on a branch carry "branch" (absent = "main"). Turns are numbered across the whole day, so
a branch's turns come after every earlier turn. Switching the line logs {"role": "line", "branch": "b2"}.
The day's line is the last line record's branch, else the branch of the latest user turn, else "main".
"""

MAIN = "main"


def branch_of(rec):
    return rec.get("branch") or MAIN


def forks(records):
    """Every branch but main: {branch: {"parent", "at_turn"}}."""
    return {r["branch"]: {"parent": r["parent"], "at_turn": r["at_turn"]} for r in records if r.get("role") == "fork"}


def current_line(records):
    """The branch the day continues on: an explicit switch, else where the latest user turn was said."""
    line = MAIN
    for r in records:
        if r.get("role") == "line":
            line = r["branch"]
        elif r.get("role") == "user":
            line = branch_of(r)
        elif r.get("role") == "fork":
            line = r["branch"]
    return line


def line_records(records, branch=None):
    """The user and assistant records on one branch's path from the start of the day, in order."""
    branch = branch or current_line(records)
    fk = forks(records)
    chain = []  # (branch, turns before which the parent is cut), from the branch up to main
    b = branch
    while b != MAIN and b in fk:
        chain.append(b)
        b = fk[b]["parent"]
    chain.append(MAIN)
    chain.reverse()  # main first
    out = []
    for i, b in enumerate(chain):
        cut = fk[chain[i + 1]]["at_turn"] if i + 1 < len(chain) else None
        for r in records:
            if r.get("role") in ("user", "assistant") and branch_of(r) == b and (cut is None or r.get("turn", 0) < cut):
                out.append(r)
    return out


def day_line(records):
    """What the night trains on: the day's line. A day log with no forks gives every turn, as before."""
    return line_records(records, current_line(records))


def alternatives(records, line=None):
    """For each user turn on the line, the other versions of that message: {turn: [{"branch", "text", "turn"}, ...]},
    every version in the order it was said, the line's own included. Only turns that have more than one version."""
    line = line or current_line(records)
    fk = forks(records)
    on_line = [r for r in line_records(records, line) if r.get("role") == "user"]
    first_user = {}  # branch -> its first user record
    for r in records:
        if r.get("role") == "user" and branch_of(r) not in first_user:
            first_user[branch_of(r)] = r
    out = {}
    for r in on_line:
        b = branch_of(r)
        # the slot this message fills: (parent branch, turn it replaced) for a branch's first message, else its own
        slot = (fk[b]["parent"], fk[b]["at_turn"]) if b != MAIN and first_user.get(b) is r else (b, r["turn"])
        versions = []
        # the original at the slot: the parent's record with that turn
        orig = next((x for x in records if x.get("role") == "user" and branch_of(x) == slot[0] and x.get("turn") == slot[1]), None)
        if orig is not None:
            versions.append({"branch": slot[0], "turn": orig["turn"], "text": orig["content"]})
        for nb, f in fk.items():
            if (f["parent"], f["at_turn"]) == slot and nb in first_user:
                versions.append({"branch": nb, "turn": first_user[nb]["turn"], "text": first_user[nb]["content"]})
        if len(versions) > 1:
            out[r["turn"]] = versions
    return out


def _last_on(records):
    """When each branch was last the one you were on: the index of its latest fork, switch or user record."""
    last = {}
    for i, r in enumerate(records):
        if r.get("role") in ("fork", "line"):
            last[r["branch"]] = i
        elif r.get("role") == "user":
            last[branch_of(r)] = i
    return last


def versions(records, line=None):
    """The two steppers the thread shows, keyed by the turn of each user message on the line:
    {"messages": {turn: [...]}, "replies": {turn: [...]}}, each version {"branch", "turn", "text"}.

    A version of a message is a user record at the same place in the day after the same earlier messages, on any
    branch, however deep (an edit of an edit). "messages" holds one version per distinct wording, so an edit is one
    step away even after asking again; "replies" holds the times the same words were asked again. Each version names
    the branch to switch to: the line itself for its own record, else the branch through that record you were on
    last. Only turns with more than one version appear."""
    line = line or current_line(records)
    names = [MAIN] + list(forks(records))
    said = {b: [r for r in line_records(records, b) if r.get("role") == "user"] for b in names}
    last = _last_on(records)
    mine = said.get(line) or []
    messages, replies = {}, {}
    for k, rec in enumerate(mine):
        before = [x.get("turn") for x in mine[:k]]
        found = {}  # the turn of each record said here -> (record, the branch to switch to)
        for b, path in said.items():
            if len(path) <= k or [x.get("turn") for x in path[:k]] != before:
                continue
            r = path[k]
            here = found.get(r.get("turn"))
            if r is rec:
                found[r.get("turn")] = (r, line)
            elif here is None or (here[1] != line and last.get(b, -1) > last.get(here[1], -1)):
                found[r.get("turn")] = (r, b)
        ordered = sorted(found.values(), key=lambda v: v[0].get("turn", 0))
        same = [{"branch": b, "turn": r.get("turn"), "text": r.get("content")} for r, b in ordered
                if r.get("content") == rec.get("content")]
        wordings = {}
        for r, b in ordered:
            text = r.get("content")
            if text == rec.get("content"):
                wordings[text] = {"branch": line, "turn": rec.get("turn"), "text": text}
            elif text not in wordings or last.get(b, -1) > last.get(wordings[text]["branch"], -1):
                wordings[text] = {"branch": b, "turn": r.get("turn"), "text": text}
        first = {}
        for r, _ in ordered:
            first.setdefault(r.get("content"), r.get("turn", 0))
        by_wording = sorted(wordings.values(), key=lambda v: first[v["text"]])
        if len(by_wording) > 1:
            messages[rec["turn"]] = by_wording
        if len(same) > 1:
            replies[rec["turn"]] = same
    return {"messages": messages, "replies": replies}


def first_words(records):
    """The first thing said on each side branch, to name it in plain words: {branch: text}."""
    out = {}
    for r in records:
        if r.get("role") == "user" and branch_of(r) != MAIN:
            out.setdefault(branch_of(r), r.get("content"))
    return out


OTHER_VERSION = "other_version"  # a note status: written on a version of the day you are not on


def line_notes(records, notes, day, line=None):
    """The notes as one line of the day has them, so the agent's recall and the night see only what was said on it.

    A note written today on a turn off the line gets status "other_version" (kept, so switching back restores it).
    A note it had superseded is active again, or superseded by the next note of the line (supersedes pointers are
    walked through hidden notes). A note hidden before and on the line now comes back. Forgotten notes and night
    links stay as they are. A day log without forks returns `notes` itself, unchanged. Returns a new list of copies
    otherwise; `day` is the day the log belongs to (note["day"])."""
    if not forks(records):
        return notes
    line = line or current_line(records)
    said = {r.get("turn") for r in records if r.get("role") == "user"}
    on = {r.get("turn") for r in line_records(records, line) if r.get("role") == "user"}
    hidden = {n["id"] for n in notes if n.get("status") != "forgotten" and n.get("day") == day
              and n.get("turn") in said and n.get("turn") not in on}
    by_id = {n["id"]: n for n in notes}
    succ = {}  # a note -> the visible note that supersedes it, through hidden ones
    for n in notes:
        if n["id"] in hidden or n.get("status") == "forgotten":
            continue
        s = n.get("supersedes")
        while s in hidden:
            s = by_id[s].get("supersedes")
        if s:
            succ[s] = n["id"]
    out = []
    for n in notes:
        m = dict(n)
        if n["id"] in hidden:
            m.update(status=OTHER_VERSION, superseded_by=None)
        elif n.get("status") != "forgotten" and not n.get("superseded_by_night"):
            cur = n.get("superseded_by")
            if not (n.get("status") == "superseded" and cur and cur not in hidden):
                if succ.get(n["id"]):
                    m.update(status="superseded", superseded_by=succ[n["id"]])
                elif n.get("status") == OTHER_VERSION or cur in hidden:
                    m.update(status="active", superseded_by=None)
        out.append(m)
    return out


def contradictions(records, notes, norm=lambda k: " ".join(str(k).lower().split())):
    """Facts the side branches saved that disagree with the day's line: a note written off the line under the key of a
    note on the line (or of the note it replaced) with a different value. Also the side facts that disagree with
    nothing. notes: every note (notes.json "notes", superseded included)."""
    line = current_line(records)
    on = {id_ for r in line_records(records, line) if r.get("role") == "assistant" for id_ in r.get("notes_written") or []}
    on_line = {id(x) for x in line_records(records, line)}
    side = {}
    for r in records:
        if r.get("role") == "assistant" and id(r) not in on_line:
            for id_ in r.get("notes_written") or []:
                if id_ not in on:
                    side[id_] = branch_of(r)
    by_id = {n["id"]: n for n in notes}
    line_by_key = {}
    for n in notes:  # the line's view of a key: a note written on the line, or one from before the day's forks
        if n["id"] in side:
            continue
        line_by_key.setdefault(norm(n["key"]), []).append(n)
    conflicts, extra = [], []
    for id_, b in side.items():
        n = by_id.get(id_)
        if not n:
            continue
        mine = [m for m in line_by_key.get(norm(n["key"]), []) if m["value"].strip().lower() != n["value"].strip().lower()]
        if mine:
            m = mine[-1]
            conflicts.append({"key": n["key"], "line": {"id": m["id"], "value": m["value"]},
                              "side": {"id": n["id"], "value": n["value"], "branch": b}})
        else:
            extra.append({"key": n["key"], "value": n["value"], "id": n["id"], "branch": b})
    return {"line": line, "conflicts": conflicts, "extra": extra}
