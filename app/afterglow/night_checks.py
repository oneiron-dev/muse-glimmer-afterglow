"""What a night's update passed: its own checks (decision.json) and the checks added after it ran, and which
update the day agent may serve.

The serving rule (runs/serving/serving-rule.json, set by the lead) lists the checks an update must pass to be served:
  {"checks": ["abstention_gate", "content_channel", ...], "set": "<who, when, why>"}
The day agent serves the latest of a world's loaded updates that passes every listed check; none passing means no
update: the base weights with the notes. The results come from the night's files only, so a changed rule (the
owner's answer on the abstention gate) changes only the check list.
"""
import datetime as dt
import json
import re

from . import paths

# Plain names for the day page and the report.
CHECK_NAMES = {"leak_check": "no held-out question in the training data",
               "gguf_matches_peft": "the served file matches the trained one", "learned_gate": "it learned enough",
               "forgot_gate": "it kept its general skill", "tool_gate": "no new failures on the eval-form tool questions",
               "content_channel": "its chat replies land in the reply", "tool_calls": "it still calls its tools in chat",
               "abstention_gate": "it says it does not know about as often as before"}

ABSTAIN_RATES = re.compile(r"abstain rate ([\d.]+) -> ([\d.]+)")


def abstention_gate(night):
    """The abstention check (does the model still say it does not know?): {"passed", "base", "after",
    "decision"}, or None when the night has none. Sources, first found wins: decision.json's own "abstention"
    field (nights decided with the check); the check added on 27 Oct after the R2 nights ran, in
    night/decision-with-abstention-gate.json; and receipt.json's "added_checks"."""
    def read(name):
        f = night / name
        return json.loads(f.read_text()) if f.exists() else None

    decision = read("decision.json") or {}
    if decision.get("abstention"):
        a = decision["abstention"]
        return {"passed": (decision.get("checks") or {}).get("abstention_gate"), "base": a.get("base"),
                "after": a.get("after"), "rule": a.get("rule"), "decision": decision.get("decision")}
    doc = read("decision-with-abstention-gate.json")
    if doc:
        rates = doc.get("abstention") or {}
        base, after = rates.get("base"), rates.get("after")
        m = ABSTAIN_RATES.search(doc.get("why") or "")
        if (base is None or after is None) and m:
            base, after = float(m.group(1)), float(m.group(2))
        return {"passed": (doc.get("checks") or {}).get("abstention_gate"), "base": base, "after": after,
                "rule": rates.get("rule"), "decision": doc.get("decision")}
    for c in (read("receipt.json") or {}).get("added_checks") or []:
        if c.get("check", "").startswith("abstention"):
            rates = c.get("abstain_rate") or {}
            return {"passed": c.get("passed"), "base": rates.get("base"), "after": rates.get("adapted"),
                    "rule": c.get("rule"), "decision": c.get("would_decide")}
    return None


# The two chat checks (29 Oct), as night 2 on writes them: eval.json fields and decision.json "checks" names.
# For nights decided before them, the check files written afterwards (relative to the night dir); each file
# carries the date it was added.
CHAT_CHECKS = {"content_channel": {"files": ["eval/channel-check-added-20261029.json"]},
               "tool_calls": {"files": ["eval/tools-check-added-20261029.json"]}}


def short_date(iso):
    try:
        return dt.date.fromisoformat(str(iso)[:10]).strftime("%-d %b")
    except ValueError:
        return str(iso)


def chat_checks(night, ev, decision):
    """The answer-channel and chat tool checks: [{"name", "passed", "counts", "added", "rule", "source"}].
    From the night's own eval.json + decision.json when it ran them, else from the check files written after it
    (CHAT_CHECKS), marked with the date they were added. Numbers come from the files only."""
    out = []
    for name, conf in CHAT_CHECKS.items():
        rec, source, added = (ev or {}).get(name), "eval.json", None
        if not rec:
            for rel in conf["files"]:
                f = night / rel
                if f.exists():
                    rec, source = json.loads(f.read_text()), str(f)
                    added = short_date(rec.get("added") or "after the night")
                    break
        if not rec:
            continue
        if name == "content_channel":
            counts = {"right": rec.get("content_ok"), "n": rec.get("n"), "min_rate": rec.get("min_rate")}
        else:
            c = rec.get("counts") or {}
            counts = {"right": (c.get("adapter") or {}).get("right"), "n": (c.get("adapter") or {}).get("n"),
                      "base_right": (c.get("base") or {}).get("right"), "max_drop": rec.get("max_drop")}
        passed = ((decision or {}).get("checks") or {}).get(name, rec.get("passed")) if added is None else rec.get("passed")
        out.append({"name": name, "passed": passed, "counts": counts, "added": added, "rule": rec.get("rule"),
                    "source": source})
    return out


def read_json(path):
    return json.loads(path.read_text()) if path.exists() else None


def night_results(world, day):
    """Every check the night of (world, day) has a result for: {name: True | False}, from decision.json's checks,
    the abstention check and the two chat checks (the night's own, else the files added after it ran)."""
    night = paths.runs_root() / world / str(day) / "night"
    decision = read_json(night / "decision.json") or {}
    results = dict(decision.get("checks") or {})
    abst = abstention_gate(night)
    if abst and abst.get("passed") is not None:
        results.setdefault("abstention_gate", abst["passed"])
    for c in chat_checks(night, read_json(night / "eval.json"), decision):
        if c.get("passed") is not None:
            results.setdefault(c["name"], c["passed"])
    return results


def serving_rule():
    """The checks an update must pass to be served (runs/serving/serving-rule.json); None when no rule is set
    (then the night's own keep decision counts)."""
    rule = read_json(paths.runs_root() / "serving" / "serving-rule.json")
    return rule if rule and isinstance(rule.get("checks"), list) else None


def serving_choice(world, entries):
    """Which of a world's loaded updates (serving-manifest entries) the day agent serves, and why in one line:
    {"entry": the entry or None, "why": text, "failed": {day: [check names]}}."""
    rule = serving_rule()
    mine = sorted((e for e in entries if e.get("world") == world and e.get("day") is not None),
                  key=lambda e: e["day"], reverse=True)
    failed = {}
    for e in mine:
        results = night_results(world, e["day"])
        if rule:
            bad = [k for k in rule["checks"] if results.get(k) is not True]  # a check not measured does not pass
        else:
            bad = [] if e.get("decision", "keep") == "keep" else ["the night's own decision"]
        if not bad:
            older = [d for d in failed]
            why = (f"Night {e['day']}'s update is loaded: it passed every check" +
                   (f"; night {older[0]}'s failed {name_list(failed[older[0]])}." if older else "."))
            return {"entry": e, "why": why, "failed": failed}
        failed[e["day"]] = bad
    if not mine:
        return {"entry": None, "why": "No update loaded: no night has run for this person yet.", "failed": {}}
    last = mine[0]["day"]
    return {"entry": None, "failed": failed,
            "why": f"No update loaded: night {last}'s update failed {name_list(failed[last])}" +
                   (", and no earlier update passes every check." if len(mine) > 1 else ".")}


def name_list(checks):
    names = [f"the '{CHECK_NAMES.get(k, k)}' check" for k in checks]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
