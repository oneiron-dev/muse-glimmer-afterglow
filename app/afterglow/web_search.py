"""The day agent's web search: one tool, search_web, OFF unless the serving notes switch it on.

runs/serving/serving-rule.json "web_search": {"on": true|false, "provider": "exa", "key_name": "<skate key name>",
"max_results": 3}. Absent or "on": false, the tool is not offered and every request is byte-equal to a chat without
it. On, the query goes to the provider (Exa, POST https://api.exa.ai/search) and up to max_results results come back
as {"title", "snippet", "url"}; the day log records the query and the results, and the day page shows "searched the
web: <query>", so the data that leaves the machine is visible. The key is read from the owner's secret store by its
name (skate get <key_name>) once per session, kept in memory only, and never printed, logged or written.
"""
import json
import subprocess
import urllib.error
import urllib.request

from . import paths

SEARCH_WEB_SCHEMA = {"type": "function", "function": {
    "name": "search_web",
    "description": "Search the public web for things your notes cannot hold: news, documentation, public facts. "
                   "Returns up to 3 results with title, snippet and url. Never search for the user's own facts; "
                   "use recall for those.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "the words to search the web for"}},
        "required": ["query"]}}}

EXA_URL = "https://api.exa.ai/search"
SNIPPET_CHARS = 300


class WebSearchError(Exception):
    pass


def web_search_setting():
    """The serving notes' web search entry with its defaults: {"on": False, "provider": "exa", "key_name": None,
    "max_results": 3, ...}."""
    f = paths.runs_root() / "serving" / "serving-rule.json"
    conf = (json.loads(f.read_text()).get("web_search") if f.exists() else None) or {}
    return {"on": False, "provider": "exa", "key_name": None, "max_results": 3, **conf}


class ExaSearch:
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

    def search(self, query):
        """[{"title", "snippet", "url"}], at most max_results."""
        body = {"query": query, "numResults": self.max_results,
                "contents": {"highlights": {"maxCharacters": SNIPPET_CHARS}}}
        req = urllib.request.Request(EXA_URL, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "x-api-key": self.key()})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                doc = json.loads(resp.read())
        except urllib.error.HTTPError as e:  # the status only: the body may echo the request
            raise WebSearchError(f"web search failed: HTTP {e.code}") from None
        except (OSError, ValueError) as e:
            raise WebSearchError(f"web search failed: {type(e).__name__}") from None
        out = []
        for r in (doc.get("results") or [])[:self.max_results]:
            snippet = " ".join((r.get("highlights") or [])) or (r.get("text") or "")
            out.append({"title": r.get("title") or "", "snippet": " ".join(snippet.split())[:SNIPPET_CHARS],
                        "url": r.get("url") or ""})
        return out


def provider(setting):
    """The search function for a setting that is on: query -> results."""
    if setting.get("provider", "exa") != "exa":
        raise WebSearchError(f"unknown web search provider {setting.get('provider')!r}")
    return ExaSearch(setting.get("key_name"), setting.get("max_results", 3)).search
