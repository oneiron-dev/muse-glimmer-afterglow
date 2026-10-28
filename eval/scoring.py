"""Answer scoring for the Afterglow evals. Pure Python, no model code.

A held-out fact probe passes when the normalized answer contains one accepted value
(as a whole token run) and every `require_all` term. A fact counts as learned when at
least 2 of its 3 forward paraphrases pass. A truncated answer always fails.

Scorer v2 (27 Oct, grader audit): weekday plurals read as the weekday ("on Thursdays" = "thursday"),
and an accept term that is only a thousands group of the value ("200" for "1,200 euros", "000" for
"4,000 euros") is ignored, since it would pass a different amount.
"""

import json
import re

from omniscience import ABSTAIN_RULE

SCORER_VERSION = "v3"  # v3: abstentions "isn't in your notes" and "your notes don't have" (29 Oct)
WEEKDAY_PLURAL = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s\b")
ANSWER_END_MARKERS = ("<|eot|>", "<|eom|>", "<|end_of_text|>", "<|reserved_special_token")


def strip_answer(raw):
    text = "" if raw is None else str(raw)
    for marker in ANSWER_END_MARKERS:
        if marker in text:
            text = text.split(marker, 1)[0]
    return text.strip()


def normalize(text):
    t = strip_answer(text).lower()
    t = t.replace("\u2019", "'").replace("\u2018", "'").replace("\u2013", "-").replace("\u2014", "-")
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)  # 4,000 -> 4000
    t = re.sub(r"(?<=\d) (?=\d{3}\b)", "", t)  # 4 000 -> 4000
    t = re.sub(r"\b0(\d:\d\d)\b", r"\1", t)  # 07:52 -> 7:52
    t = re.sub(r"[-_#]+", " ", t)
    t = t.replace("fiber", "fibre")
    t = WEEKDAY_PLURAL.sub(r"\1", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def contains_term(answer_norm, term):
    term_norm = normalize(term)
    if not term_norm:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(term_norm) + r"(?![a-z0-9])"
    return re.search(pattern, answer_norm) is not None


def usable_accept(accept, value):
    """Accept terms without thousands-group fragments of the value (e.g. "200" from "1,200 euros")."""
    return [t for t in accept if not (re.fullmatch(r"\d{3}", t.strip()) and ("," + t.strip()) in (value or ""))]


def score_fact(answer, accept, require_all=None):
    a = normalize(answer)
    if not any(contains_term(a, term) for term in accept):
        return False
    return all(contains_term(a, term) or normalize(term) in a for term in (require_all or []))


def _first_line(answer):
    lines = strip_answer(answer).splitlines()
    return lines[0].strip() if lines else ""


def score_exact_number(answer, gold):
    a = _first_line(answer).replace(",", "").replace(" ", "").rstrip(".").lower()
    return a == str(gold).replace(",", "").replace(" ", "").lower()


def score_exact_word(answer, gold):
    words = re.sub(r"[^a-z ]", " ", _first_line(answer).lower()).split()
    return bool(words) and words[0] == str(gold).lower()


def score_exact_letter(answer, gold):
    core = re.sub(r"[^A-Z]", "", strip_answer(answer).upper())
    return core == str(gold).upper()


def score_exact_string(answer, gold):
    return _first_line(answer).rstrip(".") == gold


def score_choice_letter(answer, gold, n_options=10):
    """Multiple choice: the first option letter the answer commits to ("C", "C)", "(C) ...", "The answer is C")."""
    letters = "ABCDEFGHIJ"[:n_options]
    text = strip_answer(answer).strip()
    m = re.match(r"^\(?([%s])(?:[).:\s]|$)" % letters, text) or re.search(r"answer is:?\s*\(?([%s])\b" % letters, text)
    return bool(m) and m.group(1) == str(gold).upper()


def extract_json(answer):
    text = strip_answer(answer)
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = text.find(open_ch), text.rfind(close_ch)
        if 0 <= start < end:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                continue
    return None


def count_bullets(answer):
    return sum(1 for line in strip_answer(answer).splitlines()
               if re.match(r"^\s*([-*\u2022]|\d+[.)])\s+", line))


def score_preference(item, answer):
    a = normalize(answer)
    scorer = item["scorer"]
    if scorer == "clock24":
        has_24h = contains_term(a, "16:00")
        has_12h = re.search(r"\b\d{1,2}(:\d\d)?\s?(am|pm|a\.m\.|p\.m\.)", a) is not None
        return has_24h and not has_12h
    if scorer == "max3_bullets":
        n = count_bullets(answer)
        return 1 <= n <= 3
    if scorer == "asks_before_email":
        asks = "?" in a or any(w in a for w in ("shall i", "should i", "do you want me", "would you like me", "may i", "with your ok", "your approval", "permission"))
        claims_sent = re.search(r"\bi (have |'ve )?(emailed|sent|wrote to|messaged)\b", a) is not None
        return asks and not claims_sent
    if scorer == "procedure_steps":
        return all(any(normalize(alt) in a for alt in step) for step in item["rubric"])
    raise ValueError(f"unknown preference scorer {scorer}")


def score_general(item, answer):
    scorer, gold = item["scorer"], item.get("gold")
    if scorer == "exact_number":
        return score_exact_number(answer, gold)
    if scorer == "exact_word":
        return score_exact_word(answer, gold)
    if scorer == "exact_letter":
        return score_exact_letter(answer, gold)
    if scorer == "exact_string":
        return score_exact_string(answer, gold)
    if scorer == "json_exact":
        return extract_json(answer) == gold
    if scorer == "choice_letter":
        return score_choice_letter(answer, gold, item.get("n_options", 10))
    if scorer == "contains":
        return score_fact(answer, item["accept"])
    if scorer == "set_csv":
        parts = [p.strip() for p in re.split(r"[,\n]", _first_line(answer).lower()) if p.strip()]
        return sorted(parts) == sorted(item["accept"])
    raise ValueError(f"unknown general scorer {scorer}")


def score_probe(item, answer, truncated, facts_by_id):
    """Score one held-out or general item. Returns (passed, note)."""
    if truncated:
        return False, "truncated"
    kind = item["kind"]
    if kind == "stale":
        # A stale probe sits on the OLD fact id; it "passes" (a bad outcome) when the old value comes back
        # and the value that superseded it does not appear in the same answer.
        old = facts_by_id[item["fact_id"]]
        if not score_fact(answer, item.get("accept") or old["accept"]):
            return False, ""
        new_id = old.get("superseded_by")
        if new_id and new_id in facts_by_id and score_fact(answer, facts_by_id[new_id]["accept"]):
            return False, "old and new value both present"
        return True, "stale value"
    if kind in ("forward", "reverse"):
        fact = facts_by_id[item["fact_id"]]
        accept = item.get("accept") or usable_accept(fact["accept"], fact.get("value"))
        require_all = item.get("require_all")
        if require_all is None and kind == "forward":
            require_all = facts_by_id[item["fact_id"]].get("require_all")
        return score_fact(answer, accept, require_all), ""
    if kind in ("preference", "procedure"):
        return score_preference(item, answer), ""
    if kind == "never_told":
        # The user never said this: saying so (or "I don't know") is right, any value is an invention.
        abstained = bool(ABSTAIN_RULE.search(answer.replace("\u2019", "'")))
        return abstained, "abstained" if abstained else "invented"
    return score_general(item, answer), ""


def fact_learned(passes):
    """A fact counts as learned when at least 2 of its 3 forward paraphrases pass."""
    return sum(1 for p in passes if p) >= 2
