"""Offline tests for the search_web tool (web_search.py, agent.py, tools.py; no server, no network).

  python3 -m unittest discover -s app/tests
"""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import web_search  # noqa: E402
from afterglow.agent import DaySession  # noqa: E402
from afterglow.llm import GlimmerClient  # noqa: E402
from afterglow.tools import TOOL_SCHEMAS  # noqa: E402

FOUND = {"provider": "exa", "results": [{"title": f"t{i}", "url": f"https://e{i}.test/p", "snippet": "s"}
                                        for i in range(3)], "latency_ms": 900, "cost_usd": 0.007}


class FakeClient(GlimmerClient):
    def __init__(self):
        super().__init__("http://127.0.0.1:9")
        self.get = lambda path, timeout=10: []  # no adapters loaded


class Resp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.body).encode()


class WebSearchTest(unittest.TestCase):
    def setUp(self):
        self.runs = Path(tempfile.mkdtemp())
        os.environ["AFTERGLOW_RUNS"] = str(self.runs)
        (self.runs / "serving").mkdir()

    def tearDown(self):
        os.environ.pop("AFTERGLOW_RUNS", None)

    def rule(self, conf):
        (self.runs / "serving" / "serving-rule.json").write_text(json.dumps({"checks": [], "web_search": conf}))

    def test_off_by_default_and_every_request_byte_equal(self):
        before = json.dumps(FakeClient().request_body([{"role": "user", "content": "hi"}], TOOL_SCHEMAS))
        for conf in (None, {"on": False, "key_name": "exa-key"}):
            if conf is not None:
                self.rule(conf)
            s = DaySession("w-test", 1, FakeClient())
            self.assertIs(s.tool_schemas, TOOL_SCHEMAS)
            self.assertNotIn("search_web", json.dumps(s.tool_schemas))
            self.assertEqual(json.dumps(s.client.request_body([{"role": "user", "content": "hi"}], s.tool_schemas)),
                             before)
            out, _ = s.tools.run("search_web", {"query": "x"}, turn=1)
            self.assertEqual(out, {"error": "web search is off"})

    def test_on_returns_the_connector_shape_and_logs_provider_latency_cost(self):
        self.rule({"on": True, "provider": "exa", "key_name": "exa-key"})
        with mock.patch.object(web_search.ExaWeb, "search", lambda self, q, max_results=None: dict(FOUND)):
            s = DaySession("w-test", 1, FakeClient())
            self.assertEqual(s.tool_schemas[-1]["function"]["name"], "search_web")
            self.assertEqual(s.tool_schemas[:-1], TOOL_SCHEMAS)
            out, written = s.tools.run("search_web", {"query": "llama.cpp release notes"}, turn=3)
        self.assertEqual(out, {"provider": "exa", "results": FOUND["results"]})
        self.assertEqual(written, [])
        self.assertEqual(s.tools.web_searches, [{"turn": 3, "query": "llama.cpp release notes", **FOUND}])

    def test_the_cap_counts_the_day_logs_and_refuses_past_it(self):
        log = self.runs / "wt" / "1" / "daylog.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text(json.dumps({"turn": 1, "role": "assistant", "web_searches": [{"query": "a"}, {"query": "b"}]}) +
                       "\n" + json.dumps({"turn": 2, "role": "user", "content": "web_searches is just text here"}) + "\n")
        self.assertEqual(web_search.searches_so_far(), 2)
        self.assertIsNone(web_search.cap_refusal({"searches": 3, "through": "2026-11-01"}, today=dt.date(2026, 10, 29)))
        self.assertIn("cap of 3", web_search.cap_refusal({"searches": 3}, pending=1))
        self.assertIn("through 2026-11-01",
                      web_search.cap_refusal({"searches": 500, "through": "2026-11-01"}, today=dt.date(2026, 11, 2)))
        self.rule({"on": True, "key_name": "exa-key", "cap": {"searches": 2}})
        with mock.patch.object(web_search.ExaWeb, "search", side_effect=AssertionError("must not be called")):
            s = DaySession("w-test", 1, FakeClient())
            out, _ = s.tools.run("search_web", {"query": "x"}, turn=1)
        self.assertIn("cap of 2", out["error"])
        self.assertEqual(s.tools.web_searches, [])  # a refused search is not sent, so not counted

    def test_exa_reply_is_cut_to_three_with_cost_and_the_key_never_leaks(self):
        reply = {"results": [{"title": f"T{i}", "url": f"https://x{i}.test", "highlights": ["a  b", "c"]}
                             for i in range(5)], "costDollars": {"total": 0.007}}
        sent = []

        def urlopen(req, timeout=None):
            sent.append(req)
            return Resp(reply)

        ex = web_search.ExaWeb("exa-key", max_results=5)  # capped at 3
        ex._key = "sk-test-not-real"
        with mock.patch("urllib.request.urlopen", urlopen):
            out = ex.search("q")
        self.assertEqual(out["provider"], "exa")
        self.assertEqual(out["results"], [{"title": f"T{i}", "url": f"https://x{i}.test", "snippet": "a b c"}
                                          for i in range(3)])
        self.assertEqual(out["cost_usd"], 0.007)
        self.assertEqual(json.loads(sent[0].data)["numResults"], 3)

        def fails(req, timeout=None):
            raise OSError("connection refused for sk-test-not-real")

        with mock.patch("urllib.request.urlopen", fails), self.assertRaises(web_search.WebSearchError) as e:
            ex.search("q")
        self.assertNotIn("sk-test-not-real", str(e.exception))


if __name__ == "__main__":
    unittest.main()
