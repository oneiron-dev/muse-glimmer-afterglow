#!/usr/bin/env python3
"""Custom voice, step 1 (a night step, CPU): one sentence from the user -> a re:dvx contrast axis for that voice.

The user describes a voice in one sentence ("warm, like a colleague who is glad to help"). One teacher call turns it
into an axis in the shape of axes.json: a short label, the two poles as sentences that finish "You are an assistant
that ...", four pairs of such sentences (voice / its plain opposite), twelve everyday user prompts for extraction
and eight held-out test prompts for the strength sweep. extract_states.py reads the pairs with re:dvx's protocol:
the two sides differ only in that one sentence and share the user prompt and one supplied answer token.

A voice is about how replies sound. Requests about health, diet, fitness or mood, and requests that ask for a voice
that guesses, invents facts or never admits it does not know, are refused before any call; prompts the teacher
writes on those topics are dropped. Writes <out>/<slug>/axis.json with the teacher call's log beside it.
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "night"))

TEMPLATES = ["You are an assistant that {adjective}.", "Reply as an assistant that {adjective}."]
N_PAIRS, N_PROMPTS, N_TEST = 4, 12, 8

# The organizer's avoid list and our own rule: a voice never touches these topics.
BLOCKED_TOPICS = re.compile(
    r"\b(health\w*|medic\w*|doctor|diagnos\w*|symptom\w*|therap\w*|diet\w*|calori\w*|nutrition\w*|weight[- ]?loss|"
    r"fitness|workout\w*|exercis\w*|gym|mood\w*|depress\w*|anxi\w*|mental|emotion\w*|feelings?|sad\w*|happy|"
    r"happiness|cheer\w*|upbeat|stress\w*|grief|lonel\w*)\b", re.IGNORECASE)
# A voice changes how replies sound, never whether the model admits what it does not know.
BLOCKED_HONESTY = re.compile(
    r"(never (says?|admits?)|always (answers?|guess\w*)|make[s]? (things|stuff|facts) up|invent\w*|bluff\w*|"
    r"confident(ly)? even when|don'?t say (i|you) don'?t know)", re.IGNORECASE)

PROMPT = """A user wants their assistant to reply in a new voice. The voice is about how replies sound (tone, word
choice, rhythm, how ideas are explained), never about what facts the assistant claims to know.

The user's description: "{description}"

Write a contrast set for steering a language model toward this voice:
- "label": one or two plain words naming the voice (for a button), lower case.
- "positive_label": a short phrase for the voice (3-6 words).
- "negative_label": a short phrase for its plain opposite on the same dimension (3-6 words). The opposite is an
  ordinary, competent reply style, not a rude or broken one.
- "adjective_pairs": {n_pairs} pairs [voice, opposite]. Each side is a phrase that completes the sentence "You are an
  assistant that ...", for example ["speaks warmly, like a colleague who is glad to help", "speaks in a neutral,
  impersonal tone"]. The two sides of a pair differ only in the voice; keep them the same length and plain.
- "prompts": {n_prompts} everyday user messages where the voice would show: work tasks, how-to questions, planning,
  explanations of everyday or technical things, writing help. No questions about health, diet, fitness, mood or
  feelings, and no questions about the user's private facts.
- "test_prompts": {n_test} more messages of the same kinds, different from the prompts.

Reply with JSON only: {{"label": "...", "positive_label": "...", "negative_label": "...", "adjective_pairs": [["...",
"..."]], "prompts": ["..."], "test_prompts": ["..."]}}"""


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:32] or "voice"


def refusal(description):
    """Why a description cannot become a voice, or None."""
    if not description.strip():
        return "the description is empty"
    if len(description) > 300:
        return "describe the voice in one sentence (300 characters at most)"
    m = BLOCKED_TOPICS.search(description)
    if m:
        return f"a voice stays clear of health, diet, fitness and mood (found {m.group(0)!r})"
    m = BLOCKED_HONESTY.search(description)
    if m:
        return f"a voice changes how replies sound, not whether the assistant admits what it does not know (found {m.group(0)!r})"
    return None


def clean_list(items, n):
    out, seen = [], set()
    for x in items or []:
        x = " ".join(str(x).split())
        if x and x.lower() not in seen and not BLOCKED_TOPICS.search(x):
            seen.add(x.lower())
            out.append(x)
    return out[:n]


def axis_from_reply(text, description):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("the teacher reply holds no JSON")
    v = json.loads(m.group(0))
    pairs = []
    for p in v.get("adjective_pairs") or []:
        if isinstance(p, (list, tuple)) and len(p) == 2 and all(str(s).strip() for s in p):
            a, b = (" ".join(str(s).split()).rstrip(".") for s in p)
            if not BLOCKED_TOPICS.search(a + " " + b) and not BLOCKED_HONESTY.search(a):
                pairs.append([a, b])
    prompts = clean_list(v.get("prompts"), N_PROMPTS)
    tests = [t for t in clean_list(v.get("test_prompts"), N_TEST) if t.lower() not in {p.lower() for p in prompts}]
    label = " ".join(str(v.get("label") or "").split()).lower()[:24] or "custom"
    pos, neg = (" ".join(str(v.get(k) or "").split()) for k in ("positive_label", "negative_label"))
    problems = [f"{n} {k} (need {need})" for k, n, need in (("pairs", len(pairs), 3), ("prompts", len(prompts), 8),
                                                         ("test prompts", len(tests), 6)) if n < need]
    if not pos or not neg:
        problems.append("missing pole labels")
    if problems:
        raise ValueError("the teacher's contrast set is too small: " + ", ".join(problems))
    slug = slugify(label)
    return {"name": f"voice-{slug}", "kind": "voice", "slug": slug, "label": label, "description": description,
            "positive_label": pos, "negative_label": neg, "templates": TEMPLATES, "adjective_pairs": pairs[:N_PAIRS],
            "prompts": prompts, "test_prompts": tests,
            "check_labels": {"positive": f"more {pos}", "negative": f"more {neg}"}}


def write_axis(description, teacher, out_root, slug=None, label=None, temperature=0.0, seed=20261027):
    from teacher import complete
    why = refusal(description)
    if why:
        raise SystemExit(f"refused: {why}")
    out_root = Path(out_root)
    tmp_log = out_root / ".voice-pairs.calls.jsonl"
    out_root.mkdir(parents=True, exist_ok=True)
    text, _, usage = complete(teacher, [{"role": "user", "content": PROMPT.format(
        description=description.strip(), n_pairs=N_PAIRS, n_prompts=N_PROMPTS, n_test=N_TEST)}],
        max_tokens=2400, temperature=temperature, seed=seed, log_path=tmp_log, purpose="voice contrast pairs")
    axis = axis_from_reply(text, description.strip())
    if slug:
        axis.update(slug=slug, name=f"voice-{slug}")
    if label:
        axis["label"] = label
    vdir = out_root / axis["slug"]
    vdir.mkdir(parents=True, exist_ok=True)
    axis["written_by"] = {"teacher": teacher, "usage": usage, "temperature": temperature, "seed": seed}
    (vdir / "axis.json").write_text(json.dumps(axis, indent=1, ensure_ascii=False) + "\n")
    (vdir / "voice-pairs.raw.txt").write_text(text.strip() + "\n")
    tmp_log.replace(vdir / "voice-pairs.calls.jsonl")
    return axis


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--description", required=True, help="the user's one sentence")
    ap.add_argument("--slug", help="folder name (default: from the teacher's label)")
    ap.add_argument("--label", help="the button label (default: the teacher's)")
    ap.add_argument("--teacher", default="glimmer", choices=["glimmer", "spark-sub"])
    ap.add_argument("--out", default=str(HERE / "voices"))
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=20261027, help="a second seed (with temperature > 0) writes a second "
                    "contrast set for the same sentence: is the axis stable?")
    args = ap.parse_args()
    axis = write_axis(args.description, args.teacher, args.out, args.slug, args.label, args.temperature, args.seed)
    print(json.dumps({k: axis[k] for k in ("slug", "label", "positive_label", "negative_label")} |
                     {"pairs": len(axis["adjective_pairs"]), "prompts": len(axis["prompts"]),
                      "test_prompts": len(axis["test_prompts"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
