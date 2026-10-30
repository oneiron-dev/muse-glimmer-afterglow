#!/usr/bin/env python3
"""On-policy context distillation rows (night-8 candidate; lead 30 Oct 10:13; research round 2 #2 + #3, and the
sleep framework's "short-term memory in context -> weights").

The student is an adapter with the user's notes NOT in context; it samples its own reply (temperature 1, no
truncation) to each prompt. The teacher is plain Glimmer (adapter off) with the user's notes in context; training
pulls the student's distribution at every one of its own tokens toward the teacher's top-20 (per-token KL). This
module only samples and writes rows; the teacher pass and the loss are night/train_adapter.py's (Meta night): for a
row with "teacher", the teacher reads teacher.prompt + raw.completion and its top-20 is taken at raw.completion_ids.

Forms (agreed with Meta night 30 Oct 10:18):
  cd_fact        the night's user-fact questions (rewrites, form qa_forward), in the eval's form: the student as arm B
                 (plain chat, reasoning minimal, question + FACT_SUFFIX, answer header forced), the teacher as arm C
                 (NOTES_HEADER + the active notes + the same question). raw.forced_header: the prompt runs to the first
                 generated token, the completion is the generated text only.
  cd_fact_agent  the same questions in the served agent form: student [system, question], teacher [system, the
                 product's day-context message with the notes block, question].
  cd_obscure, cd_known, cd_people
                 the soft-anchor pool prompts (night/kl_pool_rows.py's per-world fixed cache), in the agent form pair.
  No swapped pool: plain Glimmer with the notes in context would put wrong-person mass on "<Name>'s <key>" questions;
  those stay on the no-notes soft anchors.
When the student's first action is recall calls only, the product runs them; the student has no notes, so each finds
nothing: the calls and one empty result per call go into both prompts, and the row is the second turn (as in
kl_pool_rows). A sample that is cut, does not re-tokenize to its own ids, calls another tool, or whose chat parse does
not match the sampled calls is dropped and counted.

v1 student (lead 10:19, option 1): the adapter served for the previous day, sampled before the night trains. Nights
train from base, so the sampler is not the model being trained; the report says which adapter sampled.

  python3 night/context_distill.py --world w3-shop-owner --day 6 --server http://100.88.57.107:18610 \\
      --student-adapter /data/afterglow/adapters/w3-shop-owner-d5.gguf \\
      --pools runs/w3-shop-owner/_kl_pools/w3-shop-owner-kl-fixed-v2-20fe37ab2db3.jsonl \\
      --out-dir /path/distill-w3-6 [--per-form 4] [--stop-at 14:20]

Writes <out-dir>/cd-rows.jsonl (one row per kept sample, appended as it goes), cd-asked.jsonl (every prompt asked,
so a rerun resumes) and cd-report.json.
"""

import argparse
import datetime as dt
import hashlib
import json
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "night"))
from afterglow import paths  # noqa: E402
from afterglow.agent import DAY_CONTEXT, render_notes_block, system_message  # noqa: E402
from afterglow.tools import TOOL_SCHEMAS  # noqa: E402
from agent_anchor_rows import first_action  # noqa: E402
from score_peft import ANSWER_PREFIX, EVAL_DATE, FACT_SUFFIX, KNOWLEDGE_CUTOFF, NOTES_HEADER, load_notes  # noqa: E402

FORMS = ("cd_fact", "cd_fact_agent", "cd_obscure", "cd_known", "cd_people")
POOL_FORMS = {"kl_obscure": "cd_obscure", "kl_known": "cd_known", "kl_people": "cd_people"}
HEADER_START = "<|start|>assistant"
EOT_ID = 200008
NOTHING_RECALLED = {"notes": []}  # the product's recall result when no note matches
# on-policy: the student's own distribution, so temperature 1 and every truncation and penalty off
SAMPLER = {"temperature": 1.0, "top_k": 0, "top_p": 1.0, "min_p": 0.0, "typical_p": 1.0, "repeat_penalty": 1.0,
           "presence_penalty": 0.0, "frequency_penalty": 0.0, "dry_multiplier": 0.0, "xtc_probability": 0.0}
SLOT = {"id_slot": 1, "cache_prompt": False}  # slot 0 is the user's
N_PREDICT = {"cd_fact": 400, "agent": 800}
FACT_TEMPLATE = {"reasoning_strength": "minimal", "knowledge_cutoff": KNOWLEDGE_CUTOFF, "current_date": EVAL_DATE}
AGENT_TEMPLATE = {"reasoning_strength": "low"}  # the served form, as night/kl_pool_rows.py renders it


def post(url, body, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def student_lora(root, adapter_path):
    """The lora field with the student adapter (matched by its full path; ids change at server restarts) at 1 and every
    other loaded adapter at 0."""
    loaded = get(f"{root}/lora-adapters")
    found = [a for a in loaded if a.get("path") == adapter_path]
    if len(found) != 1:
        raise SystemExit(f"student adapter {adapter_path}: {len(found)} loaded on {root} (paths: "
                         f"{[a.get('path') for a in loaded]})")
    return [{"id": a["id"], "scale": 1.0 if a is found[0] else 0.0} for a in loaded]


class Sampler:
    def __init__(self, root, lora):
        self.root, self.lora = root, lora

    def render(self, messages, tools, kwargs):
        prompt = post(f"{self.root}/apply-template", {"messages": messages, **({"tools": tools} if tools else {}),
                                                      "chat_template_kwargs": kwargs})["prompt"]
        if not prompt.endswith(HEADER_START):
            raise SystemExit(f"unexpected rendered prompt end {prompt[-40:]!r}")
        return prompt

    def complete(self, prompt, n_predict, seed):
        """One sample: (text, token ids, ok). ok = it ended on <|eot|> and the text re-tokenizes to the same ids."""
        r = post(f"{self.root}/completion", {"prompt": prompt, "n_predict": n_predict, "lora": self.lora,
                                             "return_tokens": True, "seed": seed, **SAMPLER, **SLOT})
        tokens = r.get("tokens") or []
        ended = bool(tokens) and tokens[-1] == EOT_ID and r.get("stop_type") == "eos"
        text = post(f"{self.root}/detokenize", {"tokens": tokens, "with_pieces": False})["content"] if tokens else ""
        if ended and not text.endswith("<|eot|>"):
            text += "<|eot|>"
        exact = bool(tokens) and post(f"{self.root}/tokenize", {"content": text, "add_special": False,
                                                                "parse_special": True})["tokens"] == tokens
        why = None if ended and exact else ("cut" if not ended else "inexact tokens")
        return text, tokens, why

    def chat(self, messages, n_predict, seed):
        return post(f"{self.root}/v1/chat/completions", {"messages": messages, "tools": TOOL_SCHEMAS, "max_tokens": n_predict,
                                                         "chat_template_kwargs": AGENT_TEMPLATE, "lora": self.lora,
                                                         "seed": seed, **SAMPLER, **SLOT})["choices"][0]["message"]


def fact_row(s, q, notes_lines, first, seed):
    """cd_fact: arm B (student) and arm C (teacher) prompts, the answer header forced in both."""
    ask = q + FACT_SUFFIX
    # with no notes the teacher's prompt is the student's, as in the eval's arm C (score_peft.prompt_text)
    taught = NOTES_HEADER.format(first=first) + "\n".join(f"- {n}" for n in notes_lines) + "\n\n" + ask if notes_lines else ask
    student = s.render([{"role": "user", "content": ask}], None, FACT_TEMPLATE).removesuffix(HEADER_START) + ANSWER_PREFIX
    teacher = s.render([{"role": "user", "content": taught}], None, FACT_TEMPLATE).removesuffix(HEADER_START) + ANSWER_PREFIX
    text, tokens, why = s.complete(student, N_PREDICT["cd_fact"], seed)
    if why:
        return None, why, False
    return {"raw": {"prompt": student, "completion": text, "completion_ids": tokens, "forced_header": True},
            "teacher_prompt": teacher}, None, False


def agent_row(s, system, context, q, seed):
    """An agent-form pair: student [system, q], teacher [system, day context with the notes, q]; a first action of
    recall calls only is answered with empty results in both, and the row is the second turn."""
    student_msgs = [system, {"role": "user", "content": q}]
    teacher_msgs = [system, context, {"role": "user", "content": q}]
    prompt = s.render(student_msgs, TOOL_SCHEMAS, AGENT_TEMPLATE)
    text, tokens, why = s.complete(prompt, N_PREDICT["agent"], seed)
    if why:
        return None, why, False
    invokes = re.findall(r'<atem:invoke name="([\w.]+)">', text)
    target, first_invoke, _ = first_action(text)
    if target == "user" and "<atem:function_calls>" not in text:  # a reply (it may quote call markup; it calls nothing)
        teacher = s.render(teacher_msgs, TOOL_SCHEMAS, AGENT_TEMPLATE)
        return {"raw": {"prompt": prompt[:-len(HEADER_START)], "completion": HEADER_START + text, "completion_ids": tokens},
                "teacher_prompt": teacher[:-len(HEADER_START)]}, None, False
    if not invokes:
        return None, "no reply to the user", False
    if first_invoke != "recall" or set(invokes) != {"recall"}:
        return None, "another tool", False
    msg = s.chat(student_msgs, N_PREDICT["agent"], seed)  # the product's parse of the same sample (same seed)
    calls = msg.get("tool_calls") or []
    queries = [call_query(c) for c in calls]
    # the parse must be the sample's own calls, in order (a raw sample can run on into repeated calls after them)
    sampled = re.findall(r'<atem:invoke name="recall">\s*<atem:parameter name="query">(.*?)</atem:parameter>', text, re.S)
    if not calls or None in queries or queries != sampled[:len(queries)]:
        return None, "chat parse differs from the sample", True
    msg = {"role": "assistant", "content": msg.get("content") or "", **({"reasoning_content": msg["reasoning_content"]}
           if msg.get("reasoning_content") else {}), "tool_calls": calls}  # the fields the product keeps (llm.py)
    tools = [{"role": "tool", "tool_call_id": c.get("id") or f"call_{i}", "name": "recall",
              "content": json.dumps(NOTHING_RECALLED)} for i, c in enumerate(calls)]
    prompt2 = s.render(student_msgs + [msg] + tools, TOOL_SCHEMAS, AGENT_TEMPLATE)
    text2, tokens2, why2 = s.complete(prompt2, N_PREDICT["agent"], seed + 1)
    if why2:
        return None, why2, True
    if set(re.findall(r'<atem:invoke name="([\w.]+)">', text2)) or first_action(text2)[0] != "user":
        return None, "no reply to the user after recall", True
    teacher2 = s.render(teacher_msgs + [msg] + tools, TOOL_SCHEMAS, AGENT_TEMPLATE)
    return {"raw": {"prompt": prompt2[:-len(HEADER_START)], "completion": HEADER_START + text2, "completion_ids": tokens2},
            "teacher_prompt": teacher2[:-len(HEADER_START)]}, None, True


def call_query(call):
    """A parsed recall call's query, or None when the call is not a recall with a non-empty string query."""
    if (call.get("function") or {}).get("name") != "recall":
        return None
    try:
        args = json.loads(call["function"].get("arguments") or "{}")
    except ValueError:
        return None
    q = args.get("query") if isinstance(args, dict) else None
    return q if isinstance(q, str) and q.strip() else None


def fact_questions(rewrites):
    """One user-fact question per item: the night's qa_forward rewrites, first one per item_id."""
    out, seen = [], set()
    for line in Path(rewrites).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("form") != "qa_forward" or r.get("item_id") in seen or r.get("valid") is False:
            continue
        q = r["messages"][0]["content"] if r.get("messages") else r.get("user")
        if q:
            seen.add(r.get("item_id"))
            out.append({"id": r.get("item_id"), "question": q})
    return out


def pool_questions(pools):
    out = []
    for line in Path(pools).read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("form") in POOL_FORMS:
                out.append({"id": r["item_id"], "form": POOL_FORMS[r["form"]], "question": r["user"]})
    return out


def items_for(forms, facts, pools, per_form, rng):
    items = []
    for form in forms:
        if form in ("cd_fact", "cd_fact_agent"):
            chosen = [dict(f, form=form) for f in facts]
        else:
            chosen = [p for p in pools if p["form"] == form]
        rng.shuffle(chosen)
        items += chosen[:per_form] if per_form else chosen
    return items


def seed_for(world, day, form, item_id):
    return int(sha(f"cd:{world}:{day}:{form}:{item_id}")[:8], 16)


def day_context(world, day, notes):
    """The product's day-context message for the night's day, with every active note in its notes block."""
    s = paths.runs_root() / world / str(day) / "session.json"
    date = json.loads(s.read_text())["date"] if s.exists() else (dt.date(2026, 10, 27) + dt.timedelta(days=int(day) - 1)).isoformat()
    d = dt.date.fromisoformat(date)
    return {"role": "user", "content": DAY_CONTEXT.format(date_line=f"{d.strftime('%A')} {d.isoformat()}")
            + render_notes_block(notes)}


def stop_time(text):
    """--stop-at HH:MM (JST, today) as a Unix time, or None."""
    if not text:
        return None
    jst = dt.timezone(dt.timedelta(hours=9))
    h, m = map(int, text.split(":"))
    return dt.datetime.now(jst).replace(hour=h, minute=m, second=0, microsecond=0).timestamp()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--world", required=True)
    ap.add_argument("--day", type=int, required=True, help="the night's day: its notes.json and rewrites")
    ap.add_argument("--server", required=True, help="root URL of a llama-server with the student adapter loaded")
    ap.add_argument("--student-adapter", required=True, help="the student adapter's path as the server lists it")
    ap.add_argument("--student-sha256", help="its sha256, for the report (default: from runs/serving/adapters.json)")
    ap.add_argument("--pools", help="the world's soft-anchor fixed-pool cache (night/kl_pool_rows.py --cache-dir)")
    ap.add_argument("--rewrites", help="default: runs/<world>/<day>/night/rewrites/rewrites.jsonl")
    ap.add_argument("--forms", default=",".join(FORMS))
    ap.add_argument("--per-form", type=int, default=0, help="at most this many prompts per form (0 = all)")
    ap.add_argument("--stop-at", help="HH:MM JST: stop asking at this time and write the report (a slot window's end)")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args(argv)
    forms = list(dict.fromkeys(f for f in a.forms.split(",") if f))
    if not set(forms) <= set(FORMS):
        ap.error(f"--forms: {sorted(set(forms) - set(FORMS))} not in {FORMS}")
    if set(forms) - {"cd_fact", "cd_fact_agent"} and not a.pools:
        ap.error("the pool forms need --pools")
    root = a.server.rstrip("/").removesuffix("/v1")
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows_path, asked_path, report_path = out / "cd-rows.jsonl", out / "cd-asked.jsonl", out / "cd-report.json"

    world = json.loads((paths.worlds_root() / a.world / "world.json").read_text())
    first = world["persona"]["name"].split()[0]
    notes_path = paths.runs_root() / a.world / str(a.day) / "notes.json"
    notes = [n for n in json.loads(notes_path.read_text())["notes"] if n.get("status", "active") == "active"]
    notes_lines = load_notes(notes_path)
    context = day_context(a.world, a.day, notes)
    teacher_notes = {"cd_fact": (len(notes_lines), sha("\n".join(notes_lines))),
                     "agent": (len(notes), sha(context["content"]))}
    rewrites = a.rewrites or paths.runs_root() / a.world / str(a.day) / "night" / "rewrites" / "rewrites.jsonl"
    facts = fact_questions(rewrites)
    pools = pool_questions(a.pools) if a.pools else []
    items = items_for(forms, facts, pools, a.per_form, random.Random(f"cd:{a.world}:{a.day}"))

    student_sha = a.student_sha256
    if not student_sha:
        m = paths.runs_root() / "serving" / "adapters.json"
        entries = json.loads(m.read_text()).get("adapters", []) if m.exists() else []
        student_sha = next((e.get("sha256") for e in entries if e.get("path") == a.student_adapter), None)
    s = Sampler(root, student_lora(root, a.student_adapter))
    system = system_message(world)
    asked = [json.loads(x) for x in asked_path.read_text().splitlines() if x.strip()] if asked_path.exists() else []
    written = {json.loads(x)["item_id"] for x in rows_path.read_text().splitlines() if x.strip()} if rows_path.exists() else set()
    done = {(x["form"], x["id"]) for x in asked}
    deadline = stop_time(a.stop_at)
    stopped, error, t0 = False, None, time.time()
    try:
        stopped = ask_all(a, s, items, done, written, asked, deadline, system, context, notes_lines, first,
                          teacher_notes, student_sha, rows_path, asked_path)
    except BaseException as e:  # the report is written whatever stops the run (a cut window, a server error)
        error = f"{type(e).__name__}: {e}"[:300]
        raise
    finally:
        report = write_report(a, forms, items, asked, stopped, error, t0, student_sha, teacher_notes, notes_path,
                              rewrites, facts, pools, rows_path, report_path, root)
    print(json.dumps({k: report[k] for k in ("planned", "asked", "kept", "stopped_at_deadline", "seconds_this_run")}))
    return report


def ask_all(a, s, items, done, written, asked, deadline, system, context, notes_lines, first, teacher_notes,
            student_sha, rows_path, asked_path):
    """Ask every item not yet asked or written, appending rows and asked lines as it goes; True when the deadline
    stopped it."""
    for it in items:
        item_id = f"cd-{it['form']}-{it['id']}"
        if (it["form"], it["id"]) in done or item_id in written:
            continue
        if deadline and time.time() >= deadline:
            return True
        s.lora = student_lora(s.root, a.student_adapter)  # by path every time: a server restart renumbers adapters
        seed = seed_for(a.world, a.day, it["form"], it["id"])
        t = time.time()
        if it["form"] == "cd_fact":
            row, why, recalled = fact_row(s, it["question"], notes_lines, first, seed)
        else:
            row, why, recalled = agent_row(s, system, context, it["question"], seed)
        line = {"form": it["form"], "id": it["id"], "kept": row is not None, "why": why, "after_recall": recalled,
                "seed": seed, "s": round(time.time() - t, 2)}
        if row is not None:
            n, digest = teacher_notes["cd_fact" if it["form"] == "cd_fact" else "agent"]
            full = {"item_id": item_id, "kind": "context_distill", "form": it["form"],
                    "user": it["question"], "raw": row["raw"], "after_recall": recalled,
                    "teacher": {"prompt": row["teacher_prompt"], "notes": n, "notes_sha256": digest},
                    "student": {"adapter": a.student_adapter, "adapter_sha256": student_sha, "epoch": None,
                                "source": f"served for day {a.day} (the previous night's adapter)", "seed": seed,
                                "temperature": SAMPLER["temperature"]},
                    "valid": True}
            with open(rows_path, "a") as f:
                f.write(json.dumps(full, ensure_ascii=False) + "\n")
            written.add(item_id)
        with open(asked_path, "a") as f:
            f.write(json.dumps(line) + "\n")
        asked.append(line)
        done.add((it["form"], it["id"]))
    return False


def write_report(a, forms, items, asked, stopped, error, t0, student_sha, teacher_notes, notes_path, rewrites, facts,
                 pools, rows_path, report_path, root):
    by_form = {}
    for x in asked:
        b = by_form.setdefault(x["form"], {"asked": 0, "kept": 0, "after_recall": 0, "dropped": {}})
        b["asked"] += 1
        b["kept"] += x["kept"]
        b["after_recall"] += bool(x["kept"] and x["after_recall"])
        if not x["kept"]:
            b["dropped"][x["why"]] = b["dropped"].get(x["why"], 0) + 1
    report = {"world": a.world, "day": a.day, "server": root, "forms": forms, "per_form": a.per_form,
              "planned": len(items), "asked": len(asked), "kept": sum(x["kept"] for x in asked), "stopped_at_deadline": stopped,
              "error": error,
              "seconds_this_run": round(time.time() - t0, 1), "by_form": by_form,
              "student": {"adapter": a.student_adapter, "adapter_sha256": student_sha,
                          "note": "v1 (lead 30 Oct 10:19): the adapter served for this day samples; nights train from "
                                  "base, so the sampler is not the model being trained"},
              "sampler": SAMPLER, "n_predict": N_PREDICT,
              "teacher": {"cd_fact": {"form": "arm C (NOTES_HEADER + active notes)", "notes": teacher_notes["cd_fact"][0],
                                      "notes_sha256": teacher_notes["cd_fact"][1]},
                          "agent": {"form": "day-context message with the notes block", "notes": teacher_notes["agent"][0],
                                    "context_sha256": teacher_notes["agent"][1]}},
              "inputs": {"notes": str(notes_path), "rewrites": str(rewrites), "pools": a.pools,
                         "fact_questions": len(facts), "pool_questions": len(pools)},
              "rows": str(rows_path)}
    report_path.write_text(json.dumps(report, indent=1) + "\n")
    return report


if __name__ == "__main__":
    main()
