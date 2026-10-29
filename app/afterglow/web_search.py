"""The day agent's web search: one tool, search_web, OFF unless the serving notes switch it on.

Shaped as Oneiron's connector verb connector.web.search, so it can move under Oneiron without a rewrite:
  web.search({"query", "max_results"}) -> {"provider": "exa", "results": [{"title", "url", "snippet"}]}
and each call also reports latency_ms and cost_usd (Exa's costDollars when it returns them) for the day log.

runs/serving/serving-rule.json "web_search": {"on": true|false, "provider": "exa", "key_name": "<skate key name>",
"max_results": 3, "cap": {"searches": 500, "through": "2026-11-01"}}. Absent or "on": false, the tool is not offered
and every request is byte-equal to a chat without it. On, the query goes to Exa (POST https://api.exa.ai/search) and
up to max_results results come back; the day log records the query, the results, provider, latency and cost, and the
day page shows "searched the web: <query>", so the data that leaves the machine is visible. The cap counts every
search in the day logs (runs/*/*/daylog.jsonl) and refuses past its number or after its date. The key is read from
the owner's secret store by its name (skate get <key_name>) once per session, kept in memory only, and never printed,
logged or written.
"""
import datetime as dt
import json
import subprocess
import time
import urllib.error
import urllib.request

from . import paths

SEARCH_WEB_SCHEMA = {"type": "function", "function": {
    "name": "search_web",
    "description": "Search the public web for general knowledge your notes cannot hold: news, documentation, "
                   "public facts. Returns up to 3 results with title, url and snippet. A query may name public "
                   "places and topics from the conversation, never people's names, contact details or private "
                   "identifiers (codes, numbers, account or file names); use recall for what the user told you.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "the words to search the web for: public places and topics, "
                                                   "no names of people, contact details or private identifiers"}},
        "required": ["query"]}}}

EXA_URL = "https://api.exa.ai/search"
SNIPPET_CHARS = 300
JST = dt.timezone(dt.timedelta(hours=9))


class WebSearchError(Exception):
    pass


def web_search_setting():
    """The serving notes' web search entry with its defaults: {"on": False, "provider": "exa", "key_name": None,
    "max_results": 3, "cap": None, ...}."""
    f = paths.runs_root() / "serving" / "serving-rule.json"
    conf = (json.loads(f.read_text()).get("web_search") if f.exists() else None) or {}
    return {"on": False, "provider": "exa", "key_name": None, "max_results": 3, "cap": None, **conf}


def searches_so_far():
    """Every web search the day logs record (runs/<world>/<day>/daylog.jsonl, "web_searches"), errors included."""
    n = 0
    for log in paths.runs_root().glob("*/*/daylog.jsonl"):
        for line in log.read_text().splitlines():
            if '"web_searches"' in line:
                n += len(json.loads(line).get("web_searches") or [])
    return n


def cap_refusal(cap, pending=0, today=None):
    """Why a search is refused under the cap ({"searches": N, "through": "YYYY-MM-DD"}), or None. pending = this
    session's searches not yet in a day log."""
    if not cap:
        return None
    today = today or dt.datetime.now(JST).date()
    if cap.get("through") and today > dt.date.fromisoformat(cap["through"]):
        return f"web search is capped through {cap['through']}; it needs the owner's word to go on"
    if cap.get("searches") is not None and searches_so_far() + pending >= int(cap["searches"]):
        return f"the web search cap of {cap['searches']} searches is reached"
    return None


class ExaWeb:
    """connector.web.search on Exa."""
    provider = "exa"

    def __init__(self, key_name, max_results=3, timeout=20):
        self.key_name = key_name
        self.max_results = max(1, min(int(max_results), 3))
        self.timeout = timeout
        self._key = None

    def key(self):
        if self._key is None:
            if not self.key_name:
                raise WebSearchError("no key name is set for the web search (serving notes web_search.key_name)")
            r = subprocess.run(["skate", "get", self.key_name], capture_output=True, text=True, timeout=10)
            if r.returncode != 0 or not r.stdout.strip():
                raise WebSearchError(f"the web search key {self.key_name!r} is not readable")
            self._key = r.stdout.strip()
        return self._key

    def search(self, query, max_results=None):
        """{"provider", "results": [{"title", "url", "snippet"}], "latency_ms", "cost_usd"}."""
        n = max(1, min(int(max_results or self.max_results), self.max_results))
        body = {"query": query, "numResults": n, "contents": {"highlights": {"maxCharacters": SNIPPET_CHARS}}}
        req = urllib.request.Request(EXA_URL, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "x-api-key": self.key()})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                doc = json.loads(resp.read())
        except urllib.error.HTTPError as e:  # the status only: the body may echo the request
            raise WebSearchError(f"web search failed: HTTP {e.code}") from None
        except (OSError, ValueError) as e:
            raise WebSearchError(f"web search failed: {type(e).__name__}") from None
        results = []
        for r in (doc.get("results") or [])[:n]:
            snippet = " ".join((r.get("highlights") or [])) or (r.get("text") or "")
            results.append({"title": r.get("title") or "", "url": r.get("url") or "",
                            "snippet": " ".join(snippet.split())[:SNIPPET_CHARS]})
        cost = doc.get("costDollars")
        cost = cost.get("total") if isinstance(cost, dict) else cost
        return {"provider": self.provider, "results": results, "latency_ms": round(1000 * (time.time() - t0)),
                "cost_usd": cost}


def connector(setting):
    """web.search for a setting that is on: (query, max_results=None) -> the verb's output."""
    if setting.get("provider", "exa") != "exa":
        raise WebSearchError(f"unknown web search provider {setting.get('provider')!r}")
    return ExaWeb(setting.get("key_name"), setting.get("max_results", 3)).search
