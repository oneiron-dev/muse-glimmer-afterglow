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
               "abstention_gate": "it says it does not know about as often as before",
               "abstention_product_form": "as the day assistant, it still says it does not know when it should"}
HONESTY = ('graded honesty: right minus wrong did not fall, it kept what it knew, and it still says "not in your notes" '
           'to things you never said')
CHECK_NAMES.update(abstention_honesty=HONESTY, abstention_honesty_3=HONESTY, abstention_honesty_3k=HONESTY)
# a decision's abstention_rule -> the serving check its abstention_honesty result counts as
HONESTY_RULES = {"honesty-3": "abstention_honesty_3", "honesty-3k": "abstention_honesty_3k"}

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


def unlinked_corrections(world, day, consolidated):
    """The world's corrections up to `day` that the night trained both ways: no note with the new value links to the
    old one ("corrects" in the night's outcomes), and a kept note still holds the old value. Matched by the facts'
    accept terms (eval/scoring.py). [{"key": the new note's key, "old": old value, "new": new value}]."""
    import sys

    sys.path.insert(0, str(paths.REPO / "eval"))
    import scoring

    world_doc = read_json(paths.worlds_root() / world / "world.json")
    if not world_doc or not consolidated:
        return []
    facts = {f["id"]: f for f in world_doc["facts"]}
    linked = {o["id"]: o.get("corrects") for o in consolidated.get("outcomes") or []}
    items = consolidated.get("items") or []

    def holding(f):
        accept = scoring.usable_accept(f["accept"], f["value"])
        return [i for i in items if scoring.score_fact(i.get("full_value") or i["value"], accept, f.get("require_all"))]

    out = []
    for new in world_doc["facts"]:
        if not new.get("supersedes") or int(new.get("valid_from") or 1) > int(day):
            continue
        new_notes, old_notes = holding(new), holding(facts[new["supersedes"]])
        if new_notes and old_notes and not any(linked.get(n["id"]) for n in new_notes):
            out.append({"key": new_notes[0]["key"], "old": facts[new["supersedes"]]["value"], "new": new["value"]})
    return out


def night_results(world, day, night_dir=None):
    """Every check the night of (world, day) has a result for: {name: True | False}, from decision.json's checks,
    the abstention check, the product-form abstention check (decision-product-2s.json) and the two chat checks (the
    night's own, else the files added after it ran). night_dir:
    a night variant's folder relative to the world ("2/night-2b"); default "<day>/night"."""
    night = paths.runs_root() / world / (night_dir or f"{day}/night")
    decision = read_json(night / "decision.json") or {}
    results = dict(decision.get("checks") or {})
    abst = abstention_gate(night)
    if abst and abst.get("passed") is not None:
        results.setdefault("abstention_gate", abst["passed"])
    # the abstention check in the day assistant's own form (owner ruling B, 29 Oct), written after the night ran
    product = (read_json(night / "decision-product-2s.json") or {}).get("checks") or {}
    if product.get("abstention_product_form") is not None:
        results.setdefault("abstention_product_form", product["abstention_product_form"])
    # the graded honesty check, counted only from a decision made under its rule: honesty-3 (owner, 29 Oct 20:27) and
    # honesty-3k (owner, 30 Oct 07:41: the right floor on the known set, same loss share)
    for d in (decision, *(read_json(night / f"decision-{rule}.json") or {} for rule in HONESTY_RULES)):
        check = HONESTY_RULES.get(d.get("abstention_rule"))
        if check and (d.get("checks") or {}).get("abstention_honesty") is not None:
            results.setdefault(check, d["checks"]["abstention_honesty"])
    for c in chat_checks(night, read_json(night / "eval.json"), decision):
        if c.get("passed") is not None:
            results.setdefault(c["name"], c["passed"])
    return results


def never_told_line(night):
    """"Never told: it said 'not in your notes' 30 of 30 times (the note check stopped 10 answers that would have
    pinned your notes on someone else)." from the night's product-form decision: the update as served (route decision
    on), and the wrong-person answers of the route-off control when it ran; None when not measured."""
    arm = lambda d: (((d.get("abstention_product_form") or {}).get("arms") or {}).get("adapter") or {}).get("never_told")
    # the decision of record first (it carries the route-off control), then the product-form one
    d = next((d for d in (read_json(night / "decision.json"), read_json(night / "decision-product-2s.json"))
              if d and arm(d) and arm(d).get("n") is not None), {})
    served = arm(d)
    if not served:
        return None
    line = f"Never told: it said 'not in your notes' {served['abstain']} of {served['n']} times"
    off = ((d.get("never_told_route_off") or {}).get("adapter") or {}).get("wrong_person")
    # stopped = wrong-person answers with the route off less those as served; when the served count has no
    # wrong-person split, every answer that was not "not in your notes" counts as one, so the line never overstates
    served_wrong = served.get("wrong_person", served["n"] - served["abstain"])
    wrong = (off or 0) - served_wrong
    if wrong > 0:
        line += (f" (the note check stopped {wrong} {'answer that' if wrong == 1 else 'answers that'} would have "
                 f"pinned your notes on someone else)")
    return line + "."


def serving_rule():
    """The checks an update must pass to be served (runs/serving/serving-rule.json); None when no rule is set
    (then the night's own keep decision counts)."""
    rule = read_json(paths.runs_root() / "serving" / "serving-rule.json")
    return rule if rule and isinstance(rule.get("checks"), list) else None


def night_label(e):
    """"night 2", or "night 2 (night-2b)" for a night's variant."""
    return f"night {e['day']}" + (f" ({e['arm']})" if e.get("arm") else "")


def serving_choice(world, entries):
    """Which of a world's loaded updates (serving-manifest entries) the day agent serves, and why in one line:
    {"entry": the entry or None, "why": text, "failed": {night label: [check names not passed]},
    "unmeasured": {night label: [the ones of those with no result yet]}}."""
    rule = serving_rule()
    # Only a day's night of record (runs/<w>/<d>/night) is served; a night variant (night-3b, night-3-idkc, ...) is
    # loaded to be read and is served only once it is promoted, which writes it as that day's night of record.
    # Latest day first.
    ours = [e for e in entries if e.get("world") == world and e.get("day") is not None]
    mine = sorted((e for e in ours if not e.get("arm")), key=lambda e: -e["day"])
    # a night the serving rule holds is never served, whatever its checks say, and the serving line says why
    held = {night_label(e): h for e in mine if (h := held_by(world, e, rule))}
    mine = [e for e in mine if night_label(e) not in held]
    # the reason is plain words that say it is held ("held for review: ..."); the card name stays in "ref" for the logs
    held_note = "".join(f" {label.capitalize()}: {h.get('reason')}." for label, h in held.items())
    failed, unmeasured = {}, {}

    def what(label):  # "failed the 'a' check and has no result yet for the 'b' check"
        missing = unmeasured.get(label) or []
        parts = ([f"failed {name_list([k for k in failed[label] if k not in missing])}"]
                 if len(missing) < len(failed[label]) else [])
        return " and ".join(parts + ([f"has no result yet for {name_list(missing)}"] if missing else []))

    for e in mine:
        results = night_results(world, e["day"], e.get("night_dir"))
        if rule:
            bad = [k for k in rule["checks"] if results.get(k) is not True]  # a check not measured does not pass
        else:
            bad = [] if e.get("decision", "keep") == "keep" else ["the night's own decision"]
        if not bad:
            first = next(iter(failed), None)
            why = (f"{night_label(e).capitalize()}'s update is loaded: it passed every check" +
                   (f"; {first}'s {what(first)}." if first else "."))
            told = never_told_line(paths.runs_root() / world / (e.get("night_dir") or f"{e['day']}/night"))
            return {"entry": e, "why": why + (f" {told}" if told else "") + held_note + variants_note(world, ours, rule),
                    "failed": failed, "unmeasured": unmeasured, "held": list(held)}
        failed[night_label(e)] = bad
        if rule and any(k not in results for k in bad):
            unmeasured[night_label(e)] = [k for k in bad if k not in results]
    if not mine:
        # Nights that ran but are not loaded were left out because they rolled back (the restart loads kept nights).
        ran = any(paths.runs_root().glob(f"{world}/*/night/eval.json"))
        why = ("No update loaded." if held else
               "No update loaded: none of this person's nights passed every check." if ran
               else "No update loaded: no night has run for this person yet.")
        return {"entry": None, "why": why + held_note + variants_note(world, ours, rule), "failed": {},
                "unmeasured": {}, "held": list(held)}
    last = night_label(mine[0])
    return {"entry": None, "failed": failed, "unmeasured": unmeasured, "held": list(held),
            "why": f"No update loaded: {last}'s update {what(last)}" +
                   (", and no other loaded update passes every check." if len(mine) > 1 else ".") +
                   held_note + variants_note(world, ours, rule)}


def held_by(world, e, rule):
    """The serving rule's hold on a loaded update, or None. "held": [{"world", "nights": [{"night": "<day>/<folder>",
    "sha256": "<adapter sha256 or a prefix>"}], "reason" (plain words for the serving line), "ref", "who", "since"}]. A night with a sha256 is matched by its
    adapter, so a held variant stays held once it is promoted to the day's night of record; without one, by folder."""
    night, sha = e.get("night_dir") or f"{e['day']}/night", e.get("sha256") or ""
    for h in (rule or {}).get("held") or []:
        if h.get("world") != world:
            continue
        for n in h.get("nights") or []:
            if (sha.startswith(n["sha256"]) and sha) if n.get("sha256") else n.get("night") == night:
                return h
    return None


def variants_note(world, entries, rule):
    """" Night 3 (night-3-idkc) passes every check but is not served: not promoted (control arm)." for each loaded
    night variant that passes, with its promotion status from its decision files; "" when none does."""
    notes = []
    for e in entries:
        if not e.get("arm") or not rule:
            continue
        results = night_results(world, e["day"], e.get("night_dir"))
        if any(results.get(k) is not True for k in rule["checks"]):
            continue
        h = held_by(world, e, rule)
        if h:
            notes.append(f" {night_label(e).capitalize()} passes every check but is not served: {h.get('reason')}.")
            continue
        night = paths.runs_root() / world / e["night_dir"]
        promo = next((d.get("promotion") for d in (read_json(night / "decision-product-2s.json"),
                                                   read_json(night / "decision.json")) if d and d.get("promotion")), {})
        status = str(promo.get("status") or "not promoted")
        status = status.removeprefix("passes, ")
        notes.append(f" {night_label(e).capitalize()} passes every check but is not served: {status}.")
    return "".join(notes)


def name_list(checks):
    names = [f"the '{CHECK_NAMES.get(k, k)}' check" for k in checks]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
