#!/usr/bin/env python3
"""Build a Fireworks managed-SFT dataset (OpenAI chat JSONL) from a night's train.jsonl, for hard-label SFT.

Fireworks branch (owner 4 Nov 08:32): the same night rows trained as hard labels on the bf16 base by Fireworks.
- Context-distillation rows (form cd_*, or any row with a teacher prompt) are dropped: they are soft (KL) rows and have
  no hard-label form.
- A plain row (user, assistant) gets an explicit system message holding the default system text the night trainer
  renders (identity, knowledge cutoff, generation date), so the Fireworks render matches ours except the reasoning
  strength line, which the Fireworks renderer always writes as its default ("high").
- A verbatim row (row["raw"]: agent and public-search anchors) becomes messages + tools: the day agent's system text
  (read back from the rendered prompt), the user turn, and the assistant turn parsed from the generated completion
  (to=self reasoning, ATEM tool calls, or a to=user reply). The tools list is the one whose recipients line matches the
  rendered prompt. night/fireworks_render_check.py proves each converted row renders byte-identical to the original
  with the chat template (on a host with the tokenizer).
- --order-seed permutes the rows (Fireworks takes no training seed; a second row order is the second seed).

Usage: night/fireworks_dataset.py --train runs/<w>/<d>/<night>/train.jsonl --out DATASET.jsonl --report REPORT.json
       [--order-seed N]
"""

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "night"))

from afterglow.tools import TOOL_SCHEMAS  # noqa: E402
from afterglow.web_search import SEARCH_WEB_SCHEMA  # noqa: E402
from train_adapter import GENERATION_DATE, KNOWLEDGE_CUTOFF  # noqa: E402

DEFAULT_SYSTEM = f"You are a helpful AI assistant.\nKnowledge cutoff: {KNOWLEDGE_CUTOFF}.\nCurrent date: {GENERATION_DATE}."
SYSTEM_RE = re.compile(r"^(?:<\|begin_of_text\|>)?<\|start\|>system<\|message\|>(.*?)\n\nReasoning strength: [a-z]+\.", re.S)
RECIPIENTS_RE = re.compile(r"# Valid recipients: ([^\n]*?)\.<\|eot\|>")
USER_RE = re.compile(r"<\|start\|>user<\|message\|>(.*?)<\|eot\|>$", re.S)
SEGMENT_RE = re.compile(r"^ to=([^<]+)<\|message\|>(.*?)(<\|eom\|>|<\|eot\|>)$", re.S)
INVOKE_RE = re.compile(r'^<atem:function_calls>\n<atem:invoke name="([^"]+)">\n(.*)</atem:invoke>\n</atem:function_calls>$', re.S)
PARAM_RE = re.compile(r'<atem:parameter name="([^"]+)">(.*?)</atem:parameter>\n', re.S)


def recipients(tools):
    names = []
    for t in tools:
        fn = t.get("function", t)
        ns = fn["name"].split(".")[0]
        if ns not in names:
            names.append(ns)
    return ", ".join(['"self"'] + [f'"{n}.*"' for n in names] + ['"user"'])


TOOL_SETS = {"agent": TOOL_SCHEMAS, "agent+search": TOOL_SCHEMAS + [SEARCH_WEB_SCHEMA]}


def soft_cd(row):
    return row.get("form", "").startswith("cd_") or isinstance(row.get("teacher"), dict)


def parse_tool_call(name, body):
    m = INVOKE_RE.match(body)
    if not m or m.group(1) != name:
        raise ValueError(f"tool call body is not one ATEM invoke of {name}")
    inner = m.group(2)
    args = dict(PARAM_RE.findall(inner))
    if "".join(f'<atem:parameter name="{k}">{v}</atem:parameter>\n' for k, v in args.items()) != inner:
        raise ValueError("ATEM parameters do not round-trip")
    return {"type": "function", "function": {"name": name, "arguments": args}}


def convert_raw(row):
    """messages + tools for a verbatim exchange; raises ValueError when the completion has a shape we cannot express."""
    raw = row["raw"]
    if raw.get("forced_header"):
        raise ValueError("forced-header row")
    prompt, completion = raw["prompt"], raw["completion"]
    sm = SYSTEM_RE.match(prompt)
    rm = RECIPIENTS_RE.search(prompt)
    um = USER_RE.search(prompt)
    if not (sm and rm and um):
        raise ValueError("prompt is not system + user")
    tools = next((t for t in TOOL_SETS.values() if recipients(t) == rm.group(1)), None)
    if tools is None:
        raise ValueError(f"no tool set renders recipients {rm.group(1)}")
    if not completion.startswith("<|start|>assistant"):
        raise ValueError("completion does not start with the assistant header")
    segments = completion[len("<|start|>assistant"):].split("<|start|>assistant")
    msg = {"role": "assistant"}
    calls = []
    for i, seg in enumerate(segments):
        m = SEGMENT_RE.match(seg)
        if not m:
            raise ValueError(f"segment {i} has no recipient/message/end shape")
        to, body, end = m.groups()
        last = i == len(segments) - 1
        if to == "self":
            if i != 0 or end != "<|eom|>" or last:
                raise ValueError("self segment is not a leading <|eom|> segment")
            msg["reasoning_content"] = body
        elif to == "user":
            if not last or end != "<|eot|>" or calls:
                raise ValueError("user segment is not the closing <|eot|> segment")
            msg["content"] = body
        else:
            if (end != "<|eot|>") if last else (end != "<|eom|>"):
                raise ValueError("tool call end token is not the template's")
            calls.append(parse_tool_call(to, body))
    if calls:
        msg["tool_calls"] = calls
    elif "content" not in msg:
        raise ValueError("no user reply and no tool call")
    return {"messages": [{"role": "system", "content": sm.group(1)}, {"role": "user", "content": um.group(1)}, msg],
            "tools": tools}


def convert(row):
    if row.get("raw"):
        return convert_raw(row)
    user, assistant = row["messages"]
    if user["role"] != "user" or assistant["role"] != "assistant":
        raise ValueError("plain row is not user, assistant")
    return {"messages": [{"role": "system", "content": DEFAULT_SYSTEM}, {"role": "user", "content": user["content"]},
                         {"role": "assistant", "content": assistant["content"]}]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--order-seed", type=int, default=0, help="0 keeps the train-set order")
    args = ap.parse_args()
    src = Path(args.train).read_bytes()
    rows = [json.loads(line) for line in src.decode().splitlines() if line.strip()]
    out, dropped_cd, failed, forms = [], 0, [], {}
    for row in rows:
        if soft_cd(row):
            dropped_cd += 1
            continue
        try:
            conv = convert(row)
        except ValueError as e:
            failed.append({"id": row.get("id") or row.get("item_id"), "form": row.get("form"), "why": str(e)})
            continue
        conv["meta_id"] = row.get("id") or row.get("item_id")
        conv["meta_form"] = row.get("form", "")
        out.append(conv)
        forms[row.get("form", "")] = forms.get(row.get("form", ""), 0) + 1
    if args.order_seed:
        random.Random(args.order_seed).shuffle(out)
    # Fireworks reads messages/tools; the meta keys are for the render check and are stripped from the upload copy
    Path(args.out).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out))
    upload = Path(args.out).with_suffix(".upload.jsonl")
    upload.write_text("".join(json.dumps({k: v for k, v in r.items() if not k.startswith("meta_")}, ensure_ascii=False) + "\n"
                              for r in out))
    report = {"train": str(Path(args.train).resolve()), "train_sha256": hashlib.sha256(src).hexdigest(), "rows_in": len(rows),
              "dropped_cd": dropped_cd, "failed": failed, "rows_out": len(out), "forms": dict(sorted(forms.items())),
              "order_seed": args.order_seed, "out": str(Path(args.out).resolve()),
              "upload": str(upload.resolve()), "upload_sha256": hashlib.sha256(upload.read_bytes()).hexdigest(),
              "default_system": DEFAULT_SYSTEM}
    Path(args.report).write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: report[k] for k in ("rows_in", "dropped_cd", "rows_out", "order_seed", "upload_sha256")}),
          f"failed {len(failed)}")


if __name__ == "__main__":
    main()
