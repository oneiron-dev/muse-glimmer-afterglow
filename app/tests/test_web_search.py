"""Offline tests for the search_web tool (web_search.py, agent.py, tools.py; no server, no network).

  python3 -m unittest discover -s app/tests
"""
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


class FakeClient(GlimmerClient):
    def __init__(self):
        super().__init__("http://127.0.0.1:9")
        self.get = lambda path, timeout=10: []  # no adapters loaded


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

    def test_on_offers_the_tool_and_logs_query_and_results(self):
        self.rule({"on": True, "provider": "exa", "key_name": "exa-key"})
        found = [{"title": f"t{i}", "snippet": "s", "url": f"https://e{i}.test/p"} for i in range(3)]
        with mock.patch.object(web_search.ExaSearch, "search", lambda self, q: found):
            s = DaySession("w-test", 1, FakeClient())
            self.assertEqual(s.tool_schemas[-1]["function"]["name"], "search_web")
            self.assertEqual(s.tool_schemas[:-1], TOOL_SCHEMAS)
            out, written = s.tools.run("search_web", {"query": "llama.cpp release notes"}, turn=3)
        self.assertEqual((out["query"], len(out["results"]), written), ("llama.cpp release notes", 3, []))
        self.assertEqual(s.tools.web_searches, [{"turn": 3, "query": "llama.cpp release notes", "results": found}])

    def test_exa_reply_is_cut_to_three_results_and_the_key_never_leaks(self):
        reply = {"results": [{"title": f"T{i}", "url": f"https://x{i}.test", "highlights": ["a  b", "c"]}
                             for i in range(5)]}

        class Resp:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(self.body).encode()

        sent = []

        def urlopen(req, timeout=None):
            sent.append(req)
            return Resp(reply)

        ex = web_search.ExaSearch("exa-key", max_results=5)  # capped at 3
        ex._key = "sk-test-not-real"
        with mock.patch("urllib.request.urlopen", urlopen):
            out = ex.search("q")
        self.assertEqual(out, [{"title": f"T{i}", "snippet": "a b c", "url": f"https://x{i}.test"} for i in range(3)])
        self.assertEqual(json.loads(sent[0].data)["numResults"], 3)

        def fails(req, timeout=None):
            raise OSError("connection refused for sk-test-not-real")

        with mock.patch("urllib.request.urlopen", fails), self.assertRaises(web_search.WebSearchError) as e:
            ex.search("q")
        self.assertNotIn("sk-test-not-real", str(e.exception))


if __name__ == "__main__":
    unittest.main()
