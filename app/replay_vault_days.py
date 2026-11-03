#!/usr/bin/env python3
"""Replay recorded days into a fresh Oneiron vault without touching the source runs.

  uv run --extra oneiron python app/replay_vault_days.py --output /tmp/vault-replay --world w2 --day 1 --verify
  uv run --extra oneiron python app/replay_vault_days.py --output /tmp/vault-replay

The agent and person credential paths, vault origin, and Cloudflare Access token come from the environment described
in app/README.md. Source daylogs are copied into OUTPUT/runs; push_day writes its receipts there. replay.jsonl records
every attempted turn's write time and failure. A rerun resumes from receipts; --from-turn is for one selected day.
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from afterglow import paths  # noqa: E402
from afterglow.notes import spread  # noqa: E402
from afterglow.oneiron_vault import (SpeakerVaults, push_day, run_message_ids, speaker_runs,  # noqa: E402
                                     turn_messages)

WORLD_NAMES = ("w1-ines-varga", "w2-solo-developer", "w3-shop-owner", "w4-grad-student")
WORLD_ALIASES = {name.split("-", 1)[0]: name for name in WORLD_NAMES}


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def append_event(path, event):
    line = {"ts": stamp(), **event}
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


def read_events(path):
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def write_report(output):
    """Summarize the whole replay log, so a full run retains the smoke day's timings on a resume."""
    output = Path(output)
    events = read_events(output / "replay.jsonl")
    turns = [e for e in events if e.get("kind") == "turn"]
    latest_turn = {}
    for e in turns:
        latest_turn[(e["world"], e["day"], e["turn"])] = e
    latest_day, latest_readback = {}, {}
    for e in events:
        key = (e.get("world"), e.get("day"))
        if e.get("kind") == "day_result":
            latest_day[key] = e
        elif e.get("kind") == "readback":
            latest_readback[key] = e
    days = sorted(latest_day.values(), key=lambda e: (e["world"], e["day"]))
    verified = 0
    for key, day in latest_day.items():
        readback = latest_readback.get(key) or {}
        if (day["outcome"] == "ok" and
                (day.get("verified") or (day.get("attempted_turns") == 0 and readback.get("outcome") == "ok"))):
            verified += 1
    report = {
        "updated_at": stamp(),
        "receipt_root": str(output / "runs"),
        "log": str(output / "replay.jsonl"),
        "days_complete": sum(e["outcome"] == "ok" for e in days),
        "days_verified": verified,
        "days_failed": sum(e["outcome"] == "error" for e in days),
        "days": days,
        "write_ms": spread([e["ms"] for e in turns if e["outcome"] == "witnessed"]),
        "failure_events": sum(e["outcome"] == "error" for e in turns),
        "unresolved_failed_turns": sum(e["outcome"] == "error" for e in latest_turn.values()),
        "readbacks": [e for e in events if e.get("kind") == "readback"],
    }
    tmp = output / "report.json.tmp"
    tmp.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, output / "report.json")
    return report


def expected_runs(daylog, conversation):
    """The exact (day turn, speaker run) -> stable message ids and text that push_day derives."""
    records = [json.loads(line) for line in Path(daylog).read_text().splitlines() if line.strip()]
    pending, expected = None, {}
    for rec in records:
        if rec["role"] not in ("user", "assistant"):
            continue
        if rec["role"] == "user":
            pending = rec
            continue
        user, pending = (pending if pending is not None and pending["turn"] == rec["turn"] else None), None
        msgs = turn_messages(user, rec)
        for run, (author, texts) in enumerate(speaker_runs(msgs)):
            ids = run_message_ids(conversation, rec["turn"], run, len(texts))
            expected[(rec["turn"], run)] = dict(zip(ids, texts))
    return expected


def transcript_turns(vault, conversation):
    """Read both lanes, with schema checks, bounded paging, and one message map per transcript turn."""
    actual_turns, lanes = {}, {}
    for author in ("companion", "user"):
        after, pages, suppressed, seen = None, 0, 0, set()
        while True:
            if pages >= 100:
                raise RuntimeError(f"{author} transcript exceeded 100 pages")
            if after in seen:
                raise RuntimeError(f"{author} transcript repeated cursor {after!r}")
            if after:
                seen.add(after)
            page = vault.by_author[author].transcript(conversation, limit=200, after=after) or {}
            if not isinstance(page, dict) or not isinstance(page.get("turns"), list):
                raise RuntimeError(f"{author} transcript page has no turns list")
            pages += 1
            suppressed += (page.get("narrowing") or {}).get("suppressed_count") or 0
            for turn in page["turns"]:
                if not isinstance(turn, dict) or not isinstance(turn.get("id"), str) or not isinstance(turn.get("messages"), list):
                    raise RuntimeError(f"{author} transcript has an invalid turn")
                target = actual_turns.setdefault(turn["id"], {})
                for message in turn["messages"]:
                    if (not isinstance(message, dict) or not isinstance(message.get("id"), str)
                            or not isinstance(message.get("text"), str)):
                        raise RuntimeError(f"{author} transcript turn {turn['id']} has an invalid message")
                    mid, text = message["id"], message["text"]
                    if mid in target and target[mid] != text:
                        raise RuntimeError(f"speaker transcript lanes disagree on message {mid}")
                    target[mid] = text
            after = page.get("next")
            if after is not None and not isinstance(after, str):
                raise RuntimeError(f"{author} transcript has an invalid next cursor")
            if not after:
                break
        lanes[author] = {"pages": pages, "suppressed_count": suppressed}
    return actual_turns, lanes


def verify_day(day_dir, summary, vault):
    """Compare every receipt turn and its exact stable message set, resolving committed-before-receipt retries."""
    started = time.perf_counter()
    if summary.get("skipped_before"):
        raise RuntimeError("exact read-back needs complete receipts; --from-turn leaves earlier turn ids unknown")
    conversation = summary["conversation_ref"]
    runs = expected_runs(Path(day_dir) / "daylog.jsonl", conversation)
    receipts = {(receipt["turn"], run): ref for receipt in summary["receipts"]
                for run, ref in enumerate(receipt["receipt_refs"])}
    if receipts.keys() != runs.keys():
        raise RuntimeError("read-back receipt/daylog speaker-run mismatch")
    actual_turns, lanes = transcript_turns(vault, conversation)
    expected = {}
    for messages in runs.values():
        for mid, text in messages.items():
            if mid in expected:
                raise RuntimeError(f"duplicate expected message id {mid}")
            expected[mid] = text
    messages = {}
    for turn_messages_ in actual_turns.values():
        for mid, text in turn_messages_.items():
            if mid in messages and messages[mid] != text:
                raise RuntimeError(f"transcript turns disagree on message {mid}")
            messages[mid] = text

    expected_turn_ids, run_mismatches = set(), []
    for key, expected_run in sorted(runs.items()):
        ref = receipts[key]
        turn_id = None
        if isinstance(ref, str) and ref.startswith("witness:") and len(ref) == 40:
            try:
                int(ref.removeprefix("witness:"), 16)
                turn_id = ref.removeprefix("witness:")
            except ValueError:
                pass
        if turn_id:
            candidates = [turn_id] if set(actual_turns.get(turn_id, {})) == set(expected_run) else []
        elif ref == "already-witnessed":
            candidates = [tid for tid, actual in actual_turns.items() if set(actual) == set(expected_run)]
        else:
            candidates = []
        if len(candidates) != 1:
            run_mismatches.append({"turn": key[0], "run": key[1], "receipt_ref": ref,
                                   "candidate_turn_ids": sorted(candidates)})
        else:
            expected_turn_ids.add(candidates[0])

    missing_messages = sorted(expected.keys() - messages.keys())
    extra_messages = sorted(messages.keys() - expected.keys())
    mismatches = sorted(mid for mid in expected.keys() & messages.keys() if expected[mid] != messages[mid])
    missing_turns = sorted(expected_turn_ids - actual_turns.keys())
    extra_turns = sorted(actual_turns.keys() - expected_turn_ids)
    incomplete_lanes = [author for author, lane in lanes.items() if lane["suppressed_count"]]
    failures = {"missing_turn_ids": missing_turns, "extra_turn_ids": extra_turns,
                "missing_message_ids": missing_messages, "extra_message_ids": extra_messages,
                "text_mismatch_ids": mismatches, "run_mismatches": run_mismatches,
                "incomplete_lanes": incomplete_lanes}
    ok = not any(failures.values())
    event = {"world": summary["world"], "day": summary["day"], "outcome": "ok" if ok else "error",
             "ms": round((time.perf_counter() - started) * 1000), "conversation_ref": conversation,
             "expected_runs": len(runs), "expected_turns": len(expected_turn_ids), "read_turns": len(actual_turns),
             "expected_messages": len(expected), "read_messages": len(messages), "lanes": lanes, **failures}
    if not ok:
        event["error"] = "read-back mismatch: " + ", ".join(f"{k}={len(v)}" for k, v in failures.items() if v)
    return event


def paths_overlap(a, b):
    return a == b or a in b.parents or b in a.parents


def replay_days(worlds, days, runs_root, output, from_turn=None, person_template=None, vault_factory=None,
                verify=False):
    """Push selected days in order. vault_factory(world, day) is the offline test seam."""
    worlds, days = list(worlds), list(days)
    if from_turn is not None and (len(worlds) != 1 or len(days) != 1):
        raise ValueError("--from-turn needs one --world and one --day")
    if verify and from_turn is not None:
        raise ValueError("--verify needs complete receipts; run without --from-turn")
    runs_root, output = Path(runs_root).resolve(), Path(output).resolve()
    if paths_overlap(runs_root, output) or paths_overlap(runs_root, output / "runs"):
        raise ValueError("--output and its runs root must not overlap the source runs root")
    if vault_factory is None and not person_template:
        raise ValueError("set AFTERGLOW_ONEIRON_PERSON_CREDENTIAL_TEMPLATE for person+agent replay")
    output.mkdir(parents=True, exist_ok=True)
    log = output / "replay.jsonl"
    previous_runs = os.environ.get("AFTERGLOW_RUNS")
    os.environ["AFTERGLOW_RUNS"] = str(output / "runs")
    try:
        for world in worlds:
            for day in days:
                print(f"== {world} day {day}", file=sys.stderr, flush=True)
                attempted = [0]

                def on_turn(event):
                    attempted[0] += 1
                    append_event(log, {"kind": "turn", **event})

                try:
                    source = runs_root / world / str(day) / "daylog.jsonl"
                    day_dir = output / "runs" / world / str(day)
                    day_dir.mkdir(parents=True, exist_ok=True)
                    tmp = day_dir / "daylog.jsonl.tmp"
                    shutil.copyfile(source, tmp)
                    if source.read_bytes() != tmp.read_bytes():
                        raise RuntimeError(f"daylog copy differed from {source}")
                    os.replace(tmp, day_dir / "daylog.jsonl")
                    vault = (vault_factory(world, day) if vault_factory else
                             SpeakerVaults(person_template.format(world=world)))
                    summary = push_day(world, day, vault=vault, from_turn=from_turn, on_turn=on_turn)
                    if verify:
                        try:
                            readback = verify_day(day_dir, summary, vault)
                        except Exception as e:
                            append_event(log, {"kind": "readback", "world": world, "day": day, "outcome": "error",
                                               "error": f"{type(e).__name__}: {e}"[:300]})
                            raise
                        append_event(log, {"kind": "readback", **readback})
                        if readback["outcome"] != "ok":
                            raise RuntimeError(readback["error"])
                    append_event(log, {"kind": "day_result", "world": world, "day": day, "outcome": "ok",
                                       "verified": bool(verify), "attempted_turns": attempted[0],
                                       "turns": summary["turns"], "messages": summary["messages"],
                                       "receipt": str(day_dir / "oneiron.json")})
                except (Exception, SystemExit) as e:
                    append_event(log, {"kind": "day_result", "world": world, "day": day, "outcome": "error",
                                       "verified": False, "attempted_turns": attempted[0],
                                       "error": f"{type(e).__name__}: {e}"[:500]})
                    write_report(output)
                    if isinstance(e, SystemExit):
                        raise RuntimeError(str(e)) from e
                    raise
                write_report(output)
    finally:
        if previous_runs is None:
            os.environ.pop("AFTERGLOW_RUNS", None)
        else:
            os.environ["AFTERGLOW_RUNS"] = previous_runs
    return write_report(output)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--world", help="w1, w2, w3, w4 or a full world directory name (default: all four)")
    ap.add_argument("--day", type=int, choices=range(1, 10), help="one day (default: days 1-9)")
    ap.add_argument("--from-turn", type=int, help="skip earlier turns on one explicit --world/--day (receipts were lost)")
    ap.add_argument("--runs-root", default=str(paths.runs_root()), help="source runs root (read-only)")
    ap.add_argument("--output", required=True, help="isolated receipt/log root, outside --runs-root")
    ap.add_argument("--person-credential-template", default=os.environ.get("AFTERGLOW_ONEIRON_PERSON_CREDENTIAL_TEMPLATE"),
                    help="person credential path template containing {world} (default: the environment variable)")
    ap.add_argument("--verify", action="store_true", help="after each day, exact-check transcript turn/message ids and text")
    a = ap.parse_args()
    world = WORLD_ALIASES.get(a.world, a.world)
    if a.world and world not in WORLD_NAMES:
        ap.error(f"unknown world {a.world!r}")
    if a.from_turn is not None and (a.from_turn < 1 or not a.world or not a.day):
        ap.error("--from-turn must be >= 1 and needs one --world and one --day")
    if a.verify and a.from_turn is not None:
        ap.error("--verify needs complete receipts; run without --from-turn")
    template = a.person_credential_template
    if not template or "{world}" not in template:
        ap.error("set AFTERGLOW_ONEIRON_PERSON_CREDENTIAL_TEMPLATE to a path containing {world}")
    try:
        report = replay_days([world] if world else WORLD_NAMES, [a.day] if a.day else range(1, 10),
                             a.runs_root, a.output, from_turn=a.from_turn, person_template=template, verify=a.verify)
    except Exception as e:
        print(f"ERROR {type(e).__name__}: {e}", file=sys.stderr)
        print(f"replay log: {Path(a.output).resolve() / 'replay.jsonl'}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
