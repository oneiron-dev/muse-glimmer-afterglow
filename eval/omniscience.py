"""Omniscience-style score: does the model answer right, answer wrong, or say it does not know?

Per arm and probe set, every scored answer is one of:
  right   - the scorer passes it
  abstain - not right, and the answer says the model does not have, know or find the information, or that
            the notes do not hold it (ABSTAIN_RULE, matched case-insensitively). A denial of the fact itself
            ("he is not attending", "you have no permit") is not an abstention: it asserts something.
  wrong   - anything else (a made-up or mismatched answer, or a truncated one)
score = (right - wrong) / n, abstentions neutral: guessing costs, saying "I don't know" does not.
"""

import re

ABSTAIN_PHRASES = [
    r"\bi (?:do not|don't|cannot|can't|could not|couldn't) (?:have|know|find|see|access|locate|recall|tell|determine|confirm|say|identify|provide|give)\b",
    r"\bi have no (?:record|information|access|data|details|way|knowledge|memory)\b",
    r"\bi (?:was|am|'m) (?:unable|not able) to\b",
    r"\bthere (?:is|'s|are) no (?:record|information|mention|details|data)\b",
    r"\bnot (?:mentioned|specified|provided|stated|given|listed|recorded) in (?:the|your|my) (?:notes|records|information)\b",
    r"\byou (?:haven't|have not|didn't|did not) (?:told|tell|mentioned|mention|shared|share|given|give|said|say)\b",
    r"\bi(?:'m| am) not sure\b",
    r"\bnot\b[^.]{0,60}\b(?:in|from|on) (?:the|your|my) (?:notes|records|file|current conversation)\b",
    r"\b(?:is|are|was|were) not (?:documented|recorded|specified|mentioned|listed|included|on file)\b",
    r"\b(?:does|do|did) not (?:mention|specify|list|record|include|contain|appear in)\b",
    r"\bno [a-z' -]{1,60} (?:is|are) (?:on file|recorded|listed|documented)\b",
    r"\b(?:i'll|i will|let me|i need to|i should) (?:check|look (?:it |that )?up(?: in)?|consult|look at) (?:your|my|the) notes\b",
]
ABSTAIN_RULE = re.compile("|".join(ABSTAIN_PHRASES), re.IGNORECASE)
SETS = {"user_facts": ("forward",), "general": ("general", "forget_mmlu", "forget_tool")}


def outcome(result):
    if result["pass"]:
        return "right"
    if not result.get("truncated") and ABSTAIN_RULE.search((result.get("answer") or "").replace("\u2019", "'")):
        return "abstain"
    return "wrong"


def score(results, kinds):
    counts = {"right": 0, "wrong": 0, "abstain": 0}
    for r in results:
        if r["kind"] in kinds:
            counts[outcome(r)] += 1
    n = sum(counts.values())
    return {**counts, "n": n, "score": round((counts["right"] - counts["wrong"]) / n, 4) if n else None}


def per_arm(recs):
    return {"rule": "right if the scorer passes; abstain if not right and the answer matches one of the phrases "
                    "(case-insensitive); wrong otherwise. score = (right - wrong) / n.",
            "phrases": ABSTAIN_PHRASES,
            "arms": {arm: {name: score(rec["results"], kinds) for name, kinds in SETS.items()} for arm, rec in recs.items()}}
