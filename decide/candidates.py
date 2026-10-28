"""Candidate notes for the route decision: the k notes most likely to answer a question (BM25, CPU only).

A note's text for retrieval is its key, its full value and its statement. The route decision then reads the
question with these k notes as options, plus "none of these" (ask the user).
"""

import json
import math
import re
from pathlib import Path

WORD = re.compile(r"[a-z0-9]+")
STOP = {"the", "a", "an", "of", "to", "my", "is", "are", "what", "which", "who", "when", "where", "in", "on", "at",
        "for", "and", "or", "do", "does", "i", "me", "our", "we", "it", "its", "with", "by", "from", "that", "this",
        "you", "your", "am", "was", "were", "be", "how", "did", "has", "have", "had", "remind", "tell", "about",
        "answer", "one", "short", "sentence", "said", "says", "she", "he", "they", "her", "his", "their", "s"}


def words(text):
    return [w for w in WORD.findall(text.lower()) if w not in STOP]


def stem(w):
    for suffix in ("ing", "ers", "er", "es", "ed", "s"):
        if len(w) > len(suffix) + 2 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def note_text(item):
    return " ".join([item["key"], item.get("full_value") or item["value"], item.get("statement", "")])


class Bm25:
    def __init__(self, items, k1=1.2, b=0.75):
        self.items = items
        self.docs = [[stem(w) for w in words(note_text(i))] for i in items]
        self.avg = sum(len(d) for d in self.docs) / max(len(self.docs), 1)
        df = {}
        for d in self.docs:
            for w in set(d):
                df[w] = df.get(w, 0) + 1
        n = len(self.docs)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.k1, self.b = k1, b

    def scores(self, query):
        q = [stem(w) for w in words(query)]
        out = []
        for d in self.docs:
            s = 0.0
            for w in set(q):
                tf = d.count(w)
                if tf:
                    s += self.idf[w] * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * len(d) / self.avg))
            out.append(s)
        return out

    def top(self, query, k):
        s = self.scores(query)
        order = sorted(range(len(s)), key=lambda i: (-s[i], i))
        return [self.items[i] for i in order[:k]]


def consolidated_items(night_dir):
    return json.loads((Path(night_dir) / "consolidated.json").read_text())["items"]
