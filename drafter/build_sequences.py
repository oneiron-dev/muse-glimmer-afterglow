#!/usr/bin/env python3
"""Rebuild each world's served day transcripts as token sequences for DFlash drafter training and evaluation.

For each world and day, the day agent's conversation is rebuilt the way app/afterglow/agent.py sent it: the system
message (SYSTEM_PROMPT with the persona), the day's context message (session.json), then every user turn and every
assistant round from daylog.jsonl, with the day's tool list. The whole day renders as one sequence with the served
chat template (the template keeps every round's reasoning, so each round's prompt is a prefix of the day's
transcript). Each assistant round is one span of generated tokens: everything the model wrote after the generation
prompt "<|start|>assistant", up to and including its end token.

Output, one JSON file per world and day in OUT/<world>/day<NN>.json:
  {"world", "day", "ids": [...], "spans": [[start, end), ...], "rounds": [{"turn", "round", "start", "end",
   "retry"}], "n_tokens", "n_generated", "tools": [names], "reasoning"}
A round's prompt is ids[:start] (it ends with "<|start|>assistant"); its generated tokens are ids[start:end].

usage: uv run --no-project --with tokenizers==0.23.3 --with jinja2 python drafter/build_sequences.py \
           --tok DIR_WITH_tokenizer.json_AND_chat_template.jinja --out OUT [--worlds w1-ines-varga,...] [--days 1-10]
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from jinja2.sandbox import ImmutableSandboxedEnvironment
from tokenizers import Tokenizer

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "app"))
from afterglow import paths  # noqa: E402
from afterglow.agent import DAY_CONTEXT, SYSTEM_PROMPT, load_world, system_message  # noqa: E402,F401
from afterglow.tools import TOOL_SCHEMAS  # noqa: E402
from afterglow.web_search import SEARCH_WEB_SCHEMA  # noqa: E402

GEN_PROMPT = "<|start|>assistant"
BOS = "<|begin_of_text|>"
TEMPLATE_SHA256 = "114f55ebdc1804c1af371197b9fdf2d6bb925966c9dfe46b73782a71bc07965e"  # NF4 repo chat_template.jinja


def make_template(tok_dir):
    text = (Path(tok_dir) / "chat_template.jinja").read_text()
    assert hashlib.sha256(text.encode()).hexdigest() == TEMPLATE_SHA256, "chat template is not the served one"
    env = ImmutableSandboxedEnvironment(trim_blocks=False, lstrip_blocks=False)

    def _raise(m):
        raise ValueError(m)

    env.globals["raise_exception"] = _raise
    return env.from_string(text)


def as_template_message(m):
    """The day log's message in the form the HF template takes (tool-call arguments as a mapping)."""
    m = dict(m)
    if m.get("tool_calls"):
        calls = []
        for tc in m["tool_calls"]:
            fn = dict(tc["function"])
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args or "{}")
                except ValueError:
                    args = {"_raw": args}
                if not isinstance(args, dict):
                    args = {"_raw": args}
            fn["arguments"] = args
            calls.append({**tc, "function": fn})
        m["tool_calls"] = calls
    return m


def day_messages(world, day):
    """(messages, assistant message indices with their turn/round/retry, tools, reasoning) for one served day."""
    d = paths.runs_root() / world / str(day)
    session = json.loads((d / "session.json").read_text())
    msgs = [system_message(load_world(world)), session["context_message"]]
    rounds = []
    web = False
    for line in (d / "daylog.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec["role"] == "user":
            msgs.append({"role": "user", "content": rec["content"]})
        elif rec["role"] == "assistant":
            web = web or bool(rec.get("web_searches"))
            r = 0
            new = rec.get("messages") or []
            for i, m in enumerate(new):
                if m["role"] == "assistant":
                    retry = bool(rec.get("answer_channel_retry")) and i == len(new) - 1 and not m.get("tool_calls")
                    rounds.append({"index": len(msgs), "turn": rec["turn"], "round": r, "retry": retry})
                    r += 1
                msgs.append(as_template_message(m))
    tools = TOOL_SCHEMAS + [SEARCH_WEB_SCHEMA] if web else TOOL_SCHEMAS
    return msgs, rounds, tools


def render(tmpl, messages, tools, reasoning, gen=False):
    return tmpl.render(messages=messages, tools=tools, bos_token=BOS, add_generation_prompt=gen,
                       reasoning_strength=reasoning)


def build_day(tmpl, tok, world, day, reasoning):
    msgs, rounds, tools = day_messages(world, day)
    full = render(tmpl, msgs, tools, reasoning)
    enc = tok.encode(full, add_special_tokens=False)
    ids, offs = enc.ids, enc.offsets
    char_spans = []
    for r in rounds:
        k = r["index"]
        before = render(tmpl, msgs[:k], tools, reasoning, gen=True)   # the round's prompt, as the server rendered it
        upto = render(tmpl, msgs[:k + 1], tools, reasoning)           # the prompt plus the round's message
        assert before.endswith(GEN_PROMPT) and full.startswith(before[:-len(GEN_PROMPT)]), (world, day, k)
        # <|eom|> and <|eot|> have the same length, so the end offset holds whichever end token follows
        char_spans.append((len(before), len(upto)))
        assert full[len(before) - len(GEN_PROMPT):len(before)] == GEN_PROMPT, (world, day, k)
    spans, out_rounds = [], []
    starts = [o[0] for o in offs]
    import bisect
    for r, (c0, c1) in zip(rounds, char_spans):
        t0 = bisect.bisect_left(starts, c0)
        t1 = bisect.bisect_left(starts, c1)
        assert starts[t0] == c0 and (t1 == len(starts) or starts[t1] == c1), ("span not on a token boundary", world, day, r)
        spans.append([t0, t1])
        out_rounds.append({"turn": r["turn"], "round": r["round"], "start": t0, "end": t1, "retry": r["retry"]})
    # the prompt of each round must tokenize to the same ids as the day's prefix (prefix stability)
    for s in out_rounds[:3]:
        p = tok.encode(full[:offs[s["start"]][0]], add_special_tokens=False).ids
        assert p == ids[:s["start"]], ("prompt tokenization differs from the day prefix", world, day, s)
    return {"world": world, "day": day, "ids": ids, "spans": spans, "rounds": out_rounds, "n_tokens": len(ids),
            "n_generated": sum(e - s for s, e in spans), "tools": [t["function"]["name"] for t in tools],
            "reasoning": reasoning}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tok", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--worlds", default="w1-ines-varga,w2-solo-developer,w3-shop-owner,w4-grad-student")
    ap.add_argument("--days", default="1-10")
    ap.add_argument("--reasoning", default="low", help="the day agent's reasoning_strength (simulate_day.py default)")
    a = ap.parse_args()
    tmpl = make_template(a.tok)
    tok = Tokenizer.from_file(str(Path(a.tok) / "tokenizer.json"))
    lo, hi = (int(x) for x in a.days.split("-"))
    summary = []
    for world in a.worlds.split(","):
        out = Path(a.out) / world
        out.mkdir(parents=True, exist_ok=True)
        for day in range(lo, hi + 1):
            if not (paths.runs_root() / world / str(day) / "daylog.jsonl").exists():
                continue
            rec = build_day(tmpl, tok, world, day, a.reasoning)
            (out / f"day{day:02d}.json").write_text(json.dumps(rec, separators=(",", ":")))
            row = {k: rec[k] for k in ("world", "day", "n_tokens", "n_generated")}
            row.update(rounds=len(rec["rounds"]), first_gen=rec["spans"][0][0] if rec["spans"] else None)
            summary.append(row)
            print(json.dumps(row), flush=True)
    (Path(a.out) / "summary.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
