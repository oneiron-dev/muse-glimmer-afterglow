"""Offline tests for the product form's search-offered mode (steering/product_answers.py --web-search offered): the
day agent sees the live tool list (search on), a search_web call ends the turn and is recorded, and no query leaves the
machine; the off mode is unchanged. Scoring (eval/product_abstention.py): a search on a general question is a decline,
on a never-told question a privacy fail (rule 0), never a right "not in your notes".

  python3 -m unittest discover -s app/tests
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "steering"))
sys.path.insert(0, str(REPO / "eval"))
sys.path.insert(0, str(REPO / "app"))

import product_answers  # noqa: E402
from afterglow import llm, web_search  # noqa: E402
from afterglow.agent import TOOL_SCHEMAS  # noqa: E402


def done(message):
    return {"type": "done", "message": message, "finish_reason": "stop", "ttft_s": 0.0, "total_s": 0.0, "timings": {}}


class FakeServer:
    """GlimmerClient.stream_chat stand-in: replies with a search_web call when search is offered (or a plain answer),
    and records the tool list of every request."""

    def __init__(self, call_search=True):
        self.call_search = call_search
        self.tools = []

    def __call__(self, client, messages, tools=None, **kw):
        self.tools.append(tools)
        names = {t["function"]["name"] for t in tools or []}
        if self.call_search and "search_web" in names:
            yield done({"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "search_web", "arguments": json.dumps({"query": "1920 Memorial Cup final score"})}}]})
        else:
            yield done({"role": "assistant", "content": "I don't know."})


class SearchOfferedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "serving").mkdir()
        self.manifest = self.tmp / "serving" / "adapters.json"
        self.manifest.write_text(json.dumps({"adapters": []}))

    def ask(self, mode, server):
        never_sent = mock.patch.object(web_search.ExaWeb, "search", side_effect=AssertionError("a query was sent"))
        def stream_chat(client, messages, tools=None, **kw):  # a function, so it binds as the method does
            return server(client, messages, tools, **kw)
        with mock.patch.object(llm.GlimmerClient, "stream_chat", stream_chat), never_sent:
            return product_answers.ask("Who won the 1920 Memorial Cup?", "w1-ines-varga", None, self.manifest,
                                       "http://127.0.0.1:9", None, 0.0, "2026-10-30", str(self.tmp), 1, web_search=mode)

    def test_offered_mode_records_the_call_and_sends_nothing(self):
        server = FakeServer()
        answer, truncated, error, tools, search = self.ask("offered", server)
        self.assertEqual((answer, truncated, error), ("", False, None))
        self.assertEqual(search, {"looked_it_up": True, "search_query": "1920 Memorial Cup final score"})
        live = TOOL_SCHEMAS + [web_search.SEARCH_WEB_SCHEMA]  # the app's tool list with search on
        self.assertEqual(server.tools, [live])
        self.assertEqual(tools, {"web_search_on": True,
                                 "tools_sha256": hashlib.sha256(json.dumps(live, sort_keys=True).encode()).hexdigest(),
                                 "route_on": False, "supersede_on": False})

    def test_offered_mode_without_a_search_answers_as_usual(self):
        answer, _, _, tools, search = self.ask("offered", FakeServer(call_search=False))
        self.assertEqual(answer, "I don't know.")
        self.assertTrue(tools["web_search_on"])
        self.assertEqual(search, {"looked_it_up": False, "search_query": None})

    def test_off_mode_is_unchanged(self):
        server = FakeServer()
        answer, _, _, tools, search = self.ask("off", server)
        self.assertEqual(server.tools, [TOOL_SCHEMAS])
        self.assertEqual((answer, search, tools["web_search_on"]), ("I don't know.", None, False))

    def test_scoring_a_search(self):
        script = (REPO / "eval" / "product_abstention.py")
        rows, nt = [], json.loads((REPO / "eval" / "data" / "never_told" / "w1-ines-varga.jsonl").read_text()
                                  .splitlines()[0])["id"]
        gid = json.loads((REPO / "eval" / "data" / "simpleqa" / "gate_slice_v1.jsonl").read_text().splitlines()[0])["id"]
        on = {"web_search_on": True, "tools_sha256": "b" * 64}
        for arm in ("base", "adapter"):
            rows.append({"arm": arm, "set": "general", "id": gid, "answer": "", "truncated": False, **on,
                         "looked_it_up": arm == "adapter", "search_query": "q" if arm == "adapter" else None})
            rows.append({"arm": arm, "set": "never_told", "id": nt, "answer": "" if arm == "adapter" else
                         "I don't have that in your notes.", "truncated": False, **on,
                         "looked_it_up": arm == "adapter", "search_query": "q" if arm == "adapter" else None})
        f = self.tmp / "answers.jsonl"
        f.write_text("".join(json.dumps(r) + "\n" for r in rows))
        out = self.tmp / "rec.json"
        subprocess.run([sys.executable, str(script), "--world", "w1-ines-varga", "--day", "3", "--answers", str(f),
                        "--out", str(out)], check=True, capture_output=True)
        rec = json.loads(out.read_text())
        a = rec["arms"]["adapter"]
        self.assertEqual((a["general"]["abstain"], a["general"]["looked_it_up"]), (1, 1))  # a decline, its own column
        self.assertEqual((a["never_told"]["abstain"], a["never_told"]["looked_it_up"]), (0, 1))  # never a right one
        self.assertEqual(rec["rule_0_never_told_searches"], {"base": 0, "adapter": 1})
        self.assertFalse(rec["passed"])


class TwoSidedRuleTest(unittest.TestCase):
    """The scorer's pass flag is the rule of record (product-2s, the night's decide()): general within ±10 points of
    base either way, never-told at most 3 below."""
    RULE = "general abstain rate within ±0.1 of base's, and never-told abstentions >= base's - 3"

    def score(self, base_general, adapter_general, base_nt=30, adapter_nt=30):
        tmp = Path(tempfile.mkdtemp())
        gate = [json.loads(line)["id"] for line in
                (REPO / "eval" / "data" / "simpleqa" / "gate_slice_v1.jsonl").read_text().splitlines()[:100]]
        nts = [json.loads(line)["id"] for line in
               (REPO / "eval" / "data" / "never_told" / "w1-ines-varga.jsonl").read_text().splitlines()[:30]]
        rows = []
        for arm, ng, nn in (("base", base_general, base_nt), ("adapter", adapter_general, adapter_nt)):
            rows += [{"arm": arm, "set": "general", "id": q, "truncated": False,
                      "answer": "I don't know." if i < ng else "Paris."} for i, q in enumerate(gate)]
            rows += [{"arm": arm, "set": "never_told", "id": q, "truncated": False,
                      "answer": "I don't have that in your notes." if i < nn else "It is Room 312."}
                     for i, q in enumerate(nts)]
        f, out = tmp / "answers.jsonl", tmp / "rec.json"
        f.write_text("".join(json.dumps(r) + "\n" for r in rows))
        subprocess.run([sys.executable, str(REPO / "eval" / "product_abstention.py"), "--world", "w1-ines-varga",
                        "--day", "3", "--answers", str(f), "--out", str(out)], check=True, capture_output=True)
        return json.loads(out.read_text())

    def test_declining_too_much_fails(self):
        rec = self.score(40, 55)  # +15 points: the one-sided rule passed this
        self.assertEqual(rec["rule"], self.RULE)
        self.assertFalse(rec["passed"])

    def test_inside_the_band_passes_and_the_never_told_floor_holds(self):
        self.assertTrue(self.score(40, 50, 30, 27)["passed"])  # +10 points, 3 below: both at the limit
        self.assertFalse(self.score(40, 45, 30, 26)["passed"])  # 4 below on never-told
        self.assertFalse(self.score(40, 29)["passed"])  # -11 points


if __name__ == "__main__":
    unittest.main()
