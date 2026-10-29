#!/usr/bin/env python3
"""Answers in the product's own form, for eval/product_abstention.py: questions asked through the day agent itself
(its real system prompt, the day context with the world's notes, the tool loop so it can call recall, reasoning
"low", greedy), on the live server, one request at a time on slot 1.

Sets (frozen): general = the SimpleQA gate slice questions, asked bare (no "say that you do not know" line);
never_told = the world's never-told probes. Arms: base (every adapter at 0) and each named adapter of the world
from the serving manifest (R2 = its day-1 adapter, night2 = its day-2 adapter), at scale 1.
Each question runs in a fresh session under a scratch runs root (AFTERGLOW_RUNS) seeded with the serving manifest
and the world's notes from --notes-day, so no real day record is touched.

Writes <out>/<world>/answers-<adapter arm>.jsonl in the scorer's shape ({"arm": "base"|"adapter", "set", "id",
"answer", "truncated", "web_search_on", "tools_sha256"}; the base rows are shared by every adapter arm's file) and
<out>/<world>/progress.json. Web search stays off unless --web-search: a product-form read compares arms that saw the
same tools, so every row records web_search_on and the tool list's hash, and two arms whose values differ are never
paired (a row without them is from before web search existed: off). --web-search offered offers search_web with the
live schema (the same tool list as the app with search on) but never sends a query: the first search_web call ends
the turn, and the row records "looked_it_up": true and the query (0 searches leave the machine). The server is
deterministic only within one run, so every row also records the llama-server run that answered it and the hash of
its loaded adapter set (server_run, adapters_loaded_sha256; --server-host), and arms from different runs are never
paired either.
Order: never_told before general, and within a set base then each adapter arm, so a deadline cuts general last.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "night"))
from paths import runs_root  # noqa: E402


def manifest_adapter(real_runs, world, day, path_part=None):
    """The world's adapter for that day in the serving manifest (kept or rolled back: a loaded adapter is measured
    either way); path_part picks one by a piece of its path (e.g. "2b" for night-2b)."""
    doc = json.loads((real_runs / "serving" / "adapters.json").read_text())
    hits = [a for a in doc.get("adapters", []) if a.get("world") == world and a.get("day") is not None
            and int(a["day"]) == day and (path_part is None or path_part in a.get("path", ""))]
    return hits[-1] if hits else None


def compose_answer_files(wdir):
    """answers-<arm>.jsonl for every adapter arm in raw.jsonl (all runs so far): the base rows (latest per set and
    id) plus that arm's rows, in the scorer's shape. Refuses (SystemExit) when an arm's row and the base row it would
    pair with saw different tools (web search on in one of them, or a different tool list), or was answered by another
server run."""
    sys.path.insert(0, str(REPO / "eval"))
    from product_abstention import SERVER_FIELDS, refuse_mixed_server, refuse_mixed_tools, tools_seen

    raw = [json.loads(line) for line in (wdir / "raw.jsonl").read_text().splitlines() if line.strip()]
    base = {(r["set"], r["id"]): r for r in raw if r["arm_name"] == "base"}
    for name in {r["arm_name"] for r in raw} - {"base"}:
        mine = {(r["set"], r["id"]): r for r in raw if r["arm_name"] == name}
        pairs = [(base[k], r) for k, r in mine.items() if k in base]
        refuse_mixed_tools(pairs, f"{wdir.name} arm {name}")
        refuse_mixed_server(pairs, f"{wdir.name} arm {name}")
        rows = [dict(r, arm="base") for k, r in base.items() if k in mine] + [dict(r, arm="adapter") for r in mine.values()]
        (wdir / f"answers-{name}.jsonl").write_text("".join(json.dumps(
            {**{k: r[k] for k in ("arm", "set", "id", "answer", "truncated")},
             **dict(zip(("web_search_on", "tools_sha256"), tools_seen(r))),
             **{k: r[k] for k in ("looked_it_up", "search_query", *SERVER_FIELDS) if k in r}}) + "\n" for r in rows))


def server_identity(endpoint, host):
    """{"server_run", "adapters_loaded_sha256"} of the server that answered: its llama-server process on the host
    ("host:pid start time", over ssh; None when it cannot be read as one process) and the sha256 of the adapter set it
    has loaded (GET /lora-adapters: ids and paths). Read after each answer: a restart kills the process, so an answer
    that came back belongs to the run read right after it."""
    try:
        with urllib.request.urlopen(endpoint + "/lora-adapters", timeout=10) as r:
            loaded = [{"id": a["id"], "path": a["path"]} for a in json.loads(r.read())]
        adapters = hashlib.sha256(json.dumps(loaded, sort_keys=True).encode()).hexdigest()
    except (OSError, ValueError, KeyError):
        adapters = None
    run = None
    try:
        p = subprocess.run(["ssh", "-o", "ConnectTimeout=5", host, "ps -o pid=,lstart= -C llama-server"],
                           capture_output=True, text=True, timeout=20)
        procs = [" ".join(line.split()) for line in p.stdout.splitlines() if line.strip()]
        run = f"{host}:{procs[0]}" if p.returncode == 0 and len(procs) == 1 else None
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"server_run": run, "adapters_loaded_sha256": adapters}


class SearchOffered(BaseException):
    """search_web was called in the offered mode: ends the turn (a BaseException, so the tool box's error handling
    does not turn it into a tool result)."""

    def __init__(self, query):
        super().__init__(query)
        self.query = query


def offered_search(query, max_results=None):
    raise SearchOffered(query)


def ask(question, world, notes_file, manifest, endpoint, adapter_path, scale, date, tmp, slot, web_search="off"):
    """One fresh day-agent session; returns (answer, truncated, error, {"web_search_on", "tools_sha256"}, search)
    where search is None, or in the offered mode {"looked_it_up", "search_query"}."""
    scratch = Path(tempfile.mkdtemp(dir=tmp))
    (scratch / "serving").mkdir(parents=True)
    shutil.copy(manifest, scratch / "serving" / "adapters.json")
    rule = manifest.parent / "serving-rule.json"
    if rule.exists():  # the live product's serving rule; the fixed arms below still set their scales explicitly
        shutil.copy(rule, scratch / "serving" / "serving-rule.json")
    if notes_file:
        (scratch / world / "0").mkdir(parents=True)
        shutil.copy(notes_file, scratch / world / "0" / "notes.json")
    os.environ["AFTERGLOW_RUNS"] = str(scratch)
    from afterglow.agent import DaySession  # paths are read per call, so the scratch root applies
    from afterglow.llm import GlimmerClient
    client = GlimmerClient(endpoint, slot=slot, lora_scale=scale)
    session = DaySession(world, 1, client, date=date, reasoning="low", temperature=0.0, web_search=web_search != "off")
    tools = {"web_search_on": bool(session.web_search_on),
             "tools_sha256": hashlib.sha256(json.dumps(session.tool_schemas, sort_keys=True).encode()).hexdigest()}
    if adapter_path:
        session.client.adapter_path = adapter_path
    if web_search == "offered":  # the live tool list, but no query is ever sent and no cap applies
        session.tools.web_search, session.tools.web_cap = offered_search, None
        try:
            ev = session.ask(question)
        except SearchOffered as e:
            shutil.rmtree(scratch, ignore_errors=True)
            return "", False, None, tools, {"looked_it_up": True, "search_query": e.query}
        search = {"looked_it_up": False, "search_query": None}
    else:
        ev = session.ask(question)
        search = None
    rec = [json.loads(line) for line in (scratch / world / "1" / "daylog.jsonl").read_text().splitlines()
           if line.strip()][-1]
    rounds = rec.get("rounds") or [{}]
    truncated = rounds[-1].get("finish_reason") in ("length", "limit")
    shutil.rmtree(scratch, ignore_errors=True)
    return ev.get("content") or "", truncated, ev.get("error"), tools, search


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-root", default=str(REPO), help="repo root whose eval/ holds the frozen sets")
    ap.add_argument("--endpoint", default="http://100.88.57.107:18610")
    ap.add_argument("--world", action="append", required=True)
    ap.add_argument("--adapters", default="R2:1,night2:2",
                    help="ARM:DAY[:PATH_PART] list from the serving manifest (e.g. night2b:2:2b); empty for none")
    ap.add_argument("--general-adapters", default="night2",
                    help="adapter arms that also answer the general set; 'none' = the base only")
    ap.add_argument("--skip-base", action="store_true", help="do not ask the base again (its answers are in raw.jsonl)")
    ap.add_argument("--slot", type=int, default=1, help="server slot; run one process per slot (one call per slot)")
    ap.add_argument("--notes-day", type=int, default=2)
    ap.add_argument("--sets", default="never_told,general")
    ap.add_argument("--limit-general", type=int, default=100,
                    help="first N gate-slice questions in the frozen hash order (100 = a direction screen)")
    ap.add_argument("--general-from", type=int, default=0,
                    help="skip the first N of those (e.g. 100 with --limit-general 200 asks only questions 101-200)")
    ap.add_argument("--ids", help="file of question ids (one per line): ask only those, in both sets")
    ap.add_argument("--date", default="2026-10-30")
    ap.add_argument("--runs", help="the real runs root (default: the main checkout's runs/)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--deadline-s", type=float, default=7200)
    ap.add_argument("--server-host", default="mel", help="ssh host running the llama-server (its run id is recorded)")
    ap.add_argument("--web-search", nargs="?", const="live", default="off", choices=("off", "offered", "live"),
                    help="the day agent's search_web tool: off (default; product-form reads compare arms without it), "
                         "offered (the live schema, calls recorded, nothing sent) or live (bare --web-search: real searches)")
    args = ap.parse_args()
    t0 = time.time()
    sys.path.insert(0, str(Path(args.eval_root) / "eval"))
    sys.path.insert(0, str(REPO / "app"))
    import never_told
    import simpleqa_gate

    real_runs = runs_root(args.runs)
    manifest = real_runs / "serving" / "adapters.json"
    general = [{"id": q["id"], "question": q["question"]} for q in simpleqa_gate.gate_items()[args.general_from:args.limit_general]]
    only = set(Path(args.ids).read_text().split()) if args.ids else None
    out_note = {"general_questions": f"{args.general_from + 1}-{args.general_from + len(general)}",
                "note": "a direction screen on the first questions of the frozen slice"
                if args.limit_general < 200 else "the full gate slice"}
    out = Path(args.out)
    tmp = tempfile.mkdtemp(prefix="product-answers-")
    plan = []
    for s in args.sets.split(","):
        for w in args.world:
            arms = [] if args.skip_base else [("base", None, 0.0)]
            for spec in [x for x in args.adapters.split(",") if x]:
                name, day, *part = spec.split(":")
                entry = manifest_adapter(real_runs, w, int(day), part[0] if part else None)
                if entry:
                    arms.append((name, entry["path"], 1.0))
                else:
                    print(json.dumps({"world": w, "arm": name, "skipped": f"no day-{day} adapter in the manifest"}), flush=True)
            items = [{"id": it["id"], "question": it["question"]} for it in never_told.load(w)] if s == "never_told" else general
            if only:
                items = [it for it in items if it["id"] in only]
            if s == "general" and args.general_adapters != "none" and not any(
                    a[0] in args.general_adapters.split(",") for a in arms):
                print(json.dumps({"world": w, "set": s, "skipped": "no adapter arm to compare with the base"}), flush=True)
                continue
            for arm in arms:
                if s == "general" and arm[0] != "base" and (args.general_adapters == "none"
                                                            or arm[0] not in args.general_adapters.split(",")):
                    continue
                plan.append((w, s, arm, items))
    rows = {}
    stopped = None
    for w, s, (arm, path, scale), items in plan:
        notes_file = real_runs / w / str(args.notes_day) / "notes.json"
        notes_file = notes_file if notes_file.exists() else None
        for it in items:
            if time.time() - t0 > args.deadline_s:
                stopped = f"deadline in {w}/{s}/{arm}"
                break
            while (out / "PAUSE").exists():  # the serving owner asked for a pause between calls (a restart)
                time.sleep(5)
            tq = time.time()
            answer, truncated, error, tools, search = ask(it["question"], w, notes_file, manifest, args.endpoint, path,
                                                          scale, args.date, tmp, args.slot, web_search=args.web_search)
            row = {"arm_name": arm, "set": s, "id": it["id"], "answer": answer, "truncated": truncated, "error": error,
                   "seconds": round(time.time() - tq, 2), **tools, **(search or {}),
                   **server_identity(args.endpoint, args.server_host)}
            rows.setdefault(w, []).append(row)
            (out / w).mkdir(parents=True, exist_ok=True)
            with open(out / w / "raw.jsonl", "a") as f:  # every answer as it lands, so a cut run keeps its work
                f.write(json.dumps(row) + "\n")
        if stopped:
            break
        wdir = out / w
        wdir.mkdir(parents=True, exist_ok=True)
        done = rows.get(w, [])
        if (wdir / "raw.jsonl").exists():
            compose_answer_files(wdir)
        (wdir / "progress.json").write_text(json.dumps({**out_note,
            "counts": {f"{a}/{st}": sum(1 for r in done if r["arm_name"] == a and r["set"] == st)
                       for a in {r["arm_name"] for r in done} for st in {r["set"] for r in done}},
            "errors": sum(bool(r["error"]) for r in done), "elapsed_s": round(time.time() - t0, 1), "stopped": stopped},
            indent=1) + "\n")
        print(json.dumps({"world": w, "set": s, "arm": arm, "n": len(items), "elapsed_s": round(time.time() - t0, 1)}), flush=True)
    shutil.rmtree(tmp, ignore_errors=True)
    os.environ.pop("AFTERGLOW_RUNS", None)
    print(json.dumps({"stopped": stopped, "elapsed_s": round(time.time() - t0, 1)}))


if __name__ == "__main__":
    main()
