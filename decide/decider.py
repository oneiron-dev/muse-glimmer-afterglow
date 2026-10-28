"""Afterglow's live decisions on the serving llama-server: route (day), duplicate and supersede (night). Stdlib only, so
the app and the night job can import it.

One pass: the server renders the decision prompt with Glimmer's chat template (/apply-template, reasoning strength
minimal), the forced answer prefix is appended, and one token is read with pre-sampling n_probs. The labels'
probabilities are renormalized over the label set; the top label is the decision, its probability the confidence.
Below a per-kind threshold:
  route     -> "look up": the caller answers with the notes in the prompt (never longer reasoning for this kind);
  duplicate, supersede -> "thought it through": reasoning on, then the same labels read after the reasoning. Reasoning
    that ends at its token cap (or says nothing) returns undecided (pick and p None, reason "reasoning_cap", yes False):
    the caller keeps both notes.
Every request names every loaded adapter's scale (the decision adapter at 1 when one is set, all others at 0) and
sends cache_prompt false. Adapters are found by path, never by id.
"""

import json
import math
import time
import urllib.request

if __package__:  # imported as decide.decider (app, night)
    from .candidates import Bm25
    from .prompts import PROMPT_IDS, note_line, pair_options, pair_prompt, route_options, route_prompt
else:  # run from decide/
    from candidates import Bm25
    from prompts import PROMPT_IDS, note_line, pair_options, pair_prompt, route_options, route_prompt

ANSWER_PREFIX = "<|start|>assistant to=user<|message|>"
BOS = "<|begin_of_text|>"
N_PROBS = 128
# Per-kind thresholds; fit on the calibration split (decide/score_decisions.py) and set here from the results page.
THRESHOLDS = {"route": 0.0, "duplicate": 0.0, "supersede": 0.0}
# BM25 candidates per new note: on the 17 real day-2 corrections, top 5 holds the replaced note 16 times, top 8 all 17.
SUPERSEDE_CANDIDATES_DAY = 5  # remember() at day time: ~0.7 s per new note
SUPERSEDE_CANDIDATES_NIGHT = 8  # night consolidation: latency does not matter
SUPERSEDE_CANDIDATES = SUPERSEDE_CANDIDATES_DAY
SUPERSEDE_ACCEPT_P = 0.9  # link only when the one-pass "replaces" probability reaches this


def norm_key_value(note):
    return (" ".join(str(note.get("key", "")).lower().split()), " ".join(str(note.get("value", "")).lower().split()))


def supersede_candidates(earlier, new_note, k=SUPERSEDE_CANDIDATES):
    """The k earlier notes most likely to be replaced by new_note: BM25 over each note's key and value, queried with
    the new note's "key: value".

    Input contract: earlier = every note that was active before new_note was written, of any kind (fact, preference,
    procedure), as the agent saved it ({"id", "key", "value"}), and taken before any rule marks notes superseded. The
    new note itself (same id, or same key and value) is never a candidate."""
    same = (new_note.get("id"), norm_key_value(new_note))
    earlier = [n for n in earlier if n.get("id") != same[0] and norm_key_value(n) != same[1]]
    if not earlier:
        return []
    return Bm25([{**n, "statement": ""} for n in earlier]).top(note_line(new_note), k)


class Decider:
    def __init__(self, endpoint, adapter_path=None, slot=None, thresholds=None, timeout=600, trace=None):
        """trace: when set, every /completion post carries "X-Afterglow-Trace: <trace>", so the serving trace proxy
        records each decision."""
        self.endpoint = endpoint.rstrip("/").removesuffix("/v1")
        self.trace = trace
        self.adapter_path = adapter_path
        self.slot = slot
        self.thresholds = {**THRESHOLDS, **(thresholds or {})}
        self.timeout = timeout
        self._label_ids = {}

    def _post(self, path, body):
        headers = {"Content-Type": "application/json"}
        if self.trace and path == "/completion":
            headers["X-Afterglow-Trace"] = self.trace
        req = urllib.request.Request(self.endpoint + path, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def lora(self):
        with urllib.request.urlopen(self.endpoint + "/lora-adapters", timeout=30) as resp:
            loaded = json.loads(resp.read())
        scales = [{"id": a["id"], "scale": 1.0 if self.adapter_path and a["path"] == self.adapter_path else 0.0}
                  for a in loaded]
        if self.adapter_path and not any(s["scale"] for s in scales):
            raise RuntimeError(f"decision adapter {self.adapter_path} is not loaded")
        return scales

    def _tokens(self, text):
        return self._post("/tokenize", {"content": text, "add_special": False, "parse_special": True})["tokens"]

    def _label_id(self, label):
        if label not in self._label_ids:
            ids = self._tokens(label)
            if len(ids) != 1:
                raise RuntimeError(f"label {label!r} is {len(ids)} tokens")
            self._label_ids[label] = ids[0]
        return self._label_ids[label]

    def _prompt(self, user_text, reasoning_strength="minimal", answer=True):
        body = {"messages": [{"role": "user", "content": user_text}],
                "chat_template_kwargs": {"reasoning_strength": reasoning_strength}}
        text = self._post("/apply-template", body)["prompt"]
        text = text.removesuffix("<|start|>assistant")  # the server adds the generation prompt; we write our own
        if not text.startswith(BOS):  # /apply-template leaves out the BOS the eval of record sends
            text = BOS + text
        return text + ANSWER_PREFIX if answer else text

    def _read(self, prompt_text, labels, lora):
        body = {"prompt": self._tokens(prompt_text), "n_predict": 1, "temperature": 0.0, "top_k": 1,
                "cache_prompt": False, "lora": lora, "n_probs": max(N_PROBS, len(labels) + 8), "post_sampling_probs": False}
        if self.slot is not None:
            body["id_slot"] = self.slot
        t0 = time.time()
        r = self._post("/completion", body)
        ms = round(1000 * (time.time() - t0), 1)
        top = ((r.get("completion_probabilities") or [{}])[0].get("top_logprobs")) or []
        by_id = {t.get("id"): t["logprob"] for t in top}
        raw = {lab: (math.exp(by_id[self._label_id(lab)]) if self._label_id(lab) in by_id else 0.0) for lab in labels}
        mass = sum(raw.values())
        dist = {k: (v / mass if mass else 0.0) for k, v in raw.items()}
        pick = max(dist, key=dist.get)
        return {"pick": pick, "p": round(dist[pick], 4), "label_mass": round(mass, 4), "ms": ms}

    def _think(self, user_text, labels, lora, strength="high", max_tokens=1024):
        prompt = self._prompt(user_text, strength, answer=False) + "<|start|>assistant to=self<|message|>"
        body = {"prompt": self._tokens(prompt), "n_predict": max_tokens, "temperature": 0.0, "top_k": 1,
                "cache_prompt": False, "lora": lora, "stop": ["<|eom|>", "<|eot|>", "<|start|>"]}
        if self.slot is not None:
            body["id_slot"] = self.slot
        t0 = time.time()
        r = self._post("/completion", body)
        think_ms = 1000 * (time.time() - t0)
        n_tok = r.get("tokens_predicted") or 0
        if r.get("stop_type") == "limit" or n_tok >= max_tokens or not r.get("content", "").strip():
            # Lead ruling 29 Oct 04:40: reasoning that ends at its cap, or says nothing, is undecided, never an answer.
            return {"pick": None, "p": None, "undecided": True, "reason": "reasoning_cap", "reasoning_tokens": n_tok,
                    "ms": round(think_ms, 1)}
        out = self._read(prompt + r.get("content", "") + "<|eom|>" + ANSWER_PREFIX, labels, lora)
        out["ms"] = round(out["ms"] + think_ms, 1)
        out["reasoning_tokens"] = n_tok
        out["undecided"] = False
        return out

    def route(self, first, notes, question):
        """notes: [{"key", "value"}] in store order. Returns the chosen note (or None = ask) and how it was decided."""
        options = route_options(first, notes)
        lora = self.lora()
        res = self._read(self._prompt(route_prompt(first, notes, question)), list(options), lora)
        note = None if res["pick"] == "0" else notes[int(res["pick"]) - 1]
        path = "one_pass" if res["p"] >= self.thresholds["route"] else "look_up"
        return {"kind": "route", "prompt_id": PROMPT_IDS["route"], "note": note, "ask": note is None and path == "one_pass",
                "path": path, **res}

    def pair(self, kind, a, b):
        """kind: duplicate | supersede. a, b: note lines ("key: value") or the user's sentence for the new note."""
        options = pair_options(kind)
        lora = self.lora()
        text = pair_prompt(kind, a, b)
        res = self._read(self._prompt(text), list(options), lora)
        out = {"kind": kind, "path": "one_pass", **res}
        if res["p"] < self.thresholds[kind]:
            ext = self._think(text, list(options), lora)
            out = {"kind": kind, "path": "thought_it_through", "one_pass": res, **ext}
        out["yes"] = out["pick"] == "1" and not out.get("undecided")
        out["prompt_id"] = PROMPT_IDS[kind]
        return out

    def supersede_link(self, earlier, new_note, k=SUPERSEDE_CANDIDATES, accept_p=SUPERSEDE_ACCEPT_P):
        """At remember time: does new_note replace one of the earlier active notes? One one-pass supersede call per
        candidate (earlier note first, new note second, both as "key: value"). Returns the linked note (the candidate
        with the highest p among "replaces" answers at p >= accept_p, else None) and every decision for the log."""
        decisions = []
        for cand in supersede_candidates(earlier, new_note, k):
            d = self.pair("supersede", note_line(cand), note_line(new_note))
            decisions.append({"candidate": cand.get("id"), **d})
        yes = [(d["p"], d["candidate"]) for d in decisions if d["yes"] and d["p"] >= accept_p]
        link = max(yes)[1] if yes else None
        return {"kind": "supersede_link", "prompt_id": PROMPT_IDS["supersede"], "link": link, "accept_p": accept_p,
                "decisions": decisions, "ms": round(sum(d["ms"] for d in decisions), 1)}
