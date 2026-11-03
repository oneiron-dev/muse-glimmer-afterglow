"""What the morning report reads besides one night's files: which arm of a night is the one of record, every night
of a person (kept or rolled back, and why), the fresh-200 confirmation, and the drafter's speed. All from run files:

- runs/serving/adapters.json: the served adapters; a kept entry with "night_dir" names the arm that was kept that day
  (w4 day 9: "9/night-9e5", while 9/night is a rollback).
- runs/<world>/<day>/night*/decision.json: each night's decision, checks and graded honesty change.
- decision.json "fresh_confirm" + "fresh_confirm_note": the same honesty score on a fresh, disjoint 200
  ("honesty +16.0 [+8,+24] ...; ask 18->10 pass").
- drafter/results/live-summary.json and series-summary.json: tok/s of the drafter trained on the agent's traffic
  against Meta's stock drafter, and its lead over stock night by night.
"""
import json
import re

from . import paths


def read_json(path):
    try:
        return json.loads(path.read_text()) if path.exists() else None
    except (OSError, ValueError):
        return None


def served(world):
    """The kept night this person's chat is served with: {"day", "night_dir", "sha256"} from the serving manifest,
    or None. With several kept entries, the latest day."""
    doc = read_json(paths.runs_root() / "serving" / "adapters.json") or {}
    kept = [a for a in doc.get("adapters") or [] if a.get("world") == world and a.get("decision") == "keep"
            and a.get("day") is not None]
    if not kept:
        return None
    a = max(kept, key=lambda a: int(a["day"]))
    return {"day": int(a["day"]), "night_dir": a.get("night_dir") or f"{a['day']}/night",
            "sha256": a.get("sha256"), "arm": a.get("arm")}


def night_dir(world, day):
    """The folder of record for this person's night `day`, relative to runs/<world>: the kept arm when the serving
    manifest names one for that day, else "<day>/night"."""
    s = served(world)
    return s["night_dir"] if s and s["day"] == int(day) else f"{int(day)}/night"


def failed_checks(decision):
    return [k for k, v in ((decision or {}).get("checks") or {}).items() if v is False]


def nights(world):
    """Every night of this person, in day order: {"day", "decision" ("keep" | "rollback" | None), "honesty_change",
    "failed" [check names], "served", "night_dir", "arms_tried"}. A day whose night of record is missing but whose
    every tried arm rolled back counts as rolled back; the served arm counts as kept. Trailing days with no night
    (today) are left out."""
    root = paths.runs_root() / world
    if not root.is_dir():
        return []
    s = served(world)
    out = []
    for day in sorted(int(p.name) for p in root.iterdir() if p.name.isdigit()):
        arms = sorted(p for p in (root / str(day)).glob("night*") if (p / "decision.json").exists())
        rel = night_dir(world, day)
        record = read_json(root / rel / "decision.json")
        decision = (record or {}).get("decision")
        if record is None and arms:
            tried = [read_json(p / "decision.json") or {} for p in arms]
            if all(d.get("decision") == "rollback" for d in tried):
                record, decision, rel = tried[0], "rollback", f"{day}/{arms[0].name}"
        is_served = bool(s and s["day"] == day)
        if is_served:
            decision = "keep"
        out.append({"day": day, "decision": decision,
                    "honesty_change": ((record or {}).get("abstention_honesty") or {}).get("honesty_change"),
                    "failed": failed_checks(record) if decision == "rollback" else [],
                    "served": is_served, "night_dir": rel, "arms_tried": len(arms)})
    while out and out[-1]["decision"] is None:
        out.pop()
    return out


FRESH = re.compile(r"honesty ([+-]?\d+(?:\.\d+)?) \[([+-]?\d+(?:\.\d+)?),\s*([+-]?\d+(?:\.\d+)?)\]")


def fresh(decision):
    """The honesty change on the fresh, disjoint 200, from decision.json's fresh confirmation: {"change", "ci95",
    "ask_back": [base, adapter] or None, "passed", "note"}; None when the night has none."""
    note = (decision or {}).get("fresh_confirm_note")
    m = FRESH.search(note or "")
    if not m:
        return None
    ask = re.search(r"ask (\d+)->(\d+)", note)
    return {"change": float(m.group(1)), "ci95": [float(m.group(2)), float(m.group(3))],
            "ask_back": [int(ask.group(1)), int(ask.group(2))] if ask else None,
            "passed": decision.get("fresh_confirm") == "pass", "note": note}


def speed():
    """The drafter trained on the agent's own traffic against Meta's stock drafter, live at the serving settings:
    {"ours_tok_s", "stock_tok_s", "gain_pct", "series": [{"night", "gain_pct"}]}; None without the results."""
    res = paths.REPO / "drafter" / "results"
    live = (read_json(res / "live-summary.json") or {}).get("all") or {}
    ours, stock = (live.get("pooled") or {}).get("decode_tok_s"), (live.get("stock-official") or {}).get("decode_tok_s")
    if not ours or not stock:
        return None
    series = []
    ser = read_json(res / "series-summary.json") or {}
    worlds = [w for w in ser if isinstance(ser[w], list) and ser[w]]
    for i in range(min((len(ser[w]) for w in worlds), default=0)):
        gains = [ser[w][i]["tonight"] / ser[w][i]["stock"] - 1 for w in worlds]
        series.append({"night": ser[worlds[0]][i]["night"], "gain_pct": round(100 * sum(gains) / len(gains), 1)})
    return {"ours_tok_s": round(ours, 1), "stock_tok_s": round(stock, 1), "gain_pct": round(100 * (ours / stock - 1), 1),
            "series": series, "source": "drafter/results/live-summary.json (pooled vs stock-official), series-summary.json"}
