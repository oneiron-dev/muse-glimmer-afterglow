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
from contextlib import nullcontext
import json
import subprocess
import threading
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


def web_search_setting(world=None):
    """Serving defaults, or this person's enabled Keychain-backed choice when world is supplied."""
    if world is not None:
        user = user_setting(world)
        if user and user.get("on") and user.get("key_set"):
            return {"on": True, "provider": "exa", "key_name": None, "max_results": 3,
                    "user_key": True, "cap": {**user["cap"], "_world": world}}
    f = paths.runs_root() / "serving" / "serving-rule.json"
    conf = (json.loads(f.read_text()).get("web_search") if f.exists() else None) or {}
    return {"on": False, "provider": "exa", "key_name": None, "max_results": 3, "cap": None, **conf}


def searches_so_far(world=None):
    """Every web search the day logs record (runs/<world>/<day>/daylog.jsonl, "web_searches"), errors included."""
    n = 0
    logs = ((paths.runs_root() / world).glob("*/daylog.jsonl") if world is not None
            else paths.runs_root().glob("*/*/daylog.jsonl"))
    for log in logs:
        for line in log.read_text().splitlines():
            if '"web_searches"' in line:
                n += len(json.loads(line).get("web_searches") or [])
    if world is not None:
        n += (user_setting(world) or {}).get("checks", 0)
    return n


def cap_refusal(cap, pending=0, today=None):
    """Why a search is refused under the cap ({"searches": N, "through": "YYYY-MM-DD"}), or None. pending = this
    session's searches not yet in a day log."""
    if not cap:
        return None
    today = today or dt.datetime.now(JST).date()
    if cap.get("through") and today > dt.date.fromisoformat(cap["through"]):
        return f"web search is capped through {cap['through']}; it needs the owner's word to go on"
    if cap.get("searches") is not None and searches_so_far(cap.get("_world")) + pending >= int(cap["searches"]):
        return f"the web search cap of {cap['searches']} searches is reached"
    return None


class ExaWeb:
    """connector.web.search on Exa."""
    provider = "exa"

    def __init__(self, key_name, max_results=3, timeout=20, key_reader=None):
        self.key_name = key_name
        self.max_results = max(1, min(int(max_results), 3))
        self.timeout = timeout
        self._key = None
        self.key_reader = key_reader

    def key(self):
        if self._key is None:
            if self.key_reader:
                self._key = self.key_reader()
                return self._key
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
    return ExaWeb(setting.get("key_name"), setting.get("max_results", 3),
                  key_reader=keychain_get if setting.get("user_key") else None).search


# The service/account names are fixed by the connector's Keychain contract. The secret never enters its JSON.
KEYCHAIN_SERVICE = "afterglow-web-search"
KEYCHAIN_ACCOUNT = "exa"
_user_lock = threading.RLock()


def _keychain(args, runner=None):
    try:
        result = (runner or subprocess.run)(["security", *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        raise WebSearchError("The web search key could not be read or saved in Keychain.") from None
    if result.returncode:
        raise WebSearchError("The web search key could not be read or saved in Keychain.")
    return result.stdout.strip()


def keychain_set(key, runner=None):
    if not isinstance(key, str) or not key.strip():
        raise ValueError("Enter your Exa key.")
    _keychain(["add-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT,
               "-w", key.strip(), "-U"], runner)


def keychain_get(runner=None):
    key = _keychain(["find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"], runner)
    if not key:
        raise WebSearchError("The web search key is empty in Keychain.")
    return key


def user_setting(world):
    from . import connectors
    return connectors.load(world).get("search")


def save_user_setting(world, body):
    from . import connectors
    with _user_lock:
        doc = connectors.load(world)
        current = doc.get("search") or {}
        if body.get("provider", "exa") != "exa":
            raise ValueError("Exa is the only web search provider available.")
        on = body.get("on", current.get("on", False))
        if not isinstance(on, bool):
            raise ValueError("The web search switch must be on or off.")
        cap = body.get("cap", current.get("cap"))
        if cap is not None:
            if not isinstance(cap, dict):
                raise ValueError("Set a search limit and a through date.")
            count = cap.get("searches")
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError("The search limit must be a positive whole number.")
            try:
                through = dt.date.fromisoformat(cap.get("through", "")).isoformat()
            except (TypeError, ValueError):
                raise ValueError("Set a through date as YYYY-MM-DD.") from None
            cap = {"searches": count, "through": through}
        has_key = current.get("key_set", False) or bool(body.get("key"))
        if on and (not has_key or not cap):
            raise ValueError("Set your Exa key, search limit and through date before turning it on.")
        if body.get("key"):
            keychain_set(body["key"])
        doc["search"] = {"on": on, "provider": "exa", "key_set": bool(has_key), "cap": cap,
                         "checks": current.get("checks", 0)}
        connectors.save(world, doc)
    return search_state(world)


def search_state(world):
    user = user_setting(world) or {}
    setting = web_search_setting(world)
    cap = setting.get("cap")
    return {"on": bool(setting.get("on")), "provider": setting.get("provider"),
            "max_results": setting.get("max_results", 3),
            "key_set": bool(user.get("key_set")), "user_on": bool(user.get("on")),
            "user_cap": user.get("cap"), "cap": {k: v for k, v in cap.items() if k != "_world"} if cap else None,
            "used": searches_so_far(world if setting.get("user_key") else None),
            "paused": bool(setting.get("on") and cap_refusal(cap)),
            "source": "your key" if setting.get("user_key") else "serving notes"}


def apply_search(app, force=False):
    """Apply a saved user choice; disabling it restores this day's original serving/CLI choice."""
    from .tools import TOOL_SCHEMAS
    with app.lock:
        session = app.session
        user = user_setting(session.world) or {}
        baseline = getattr(session, "_connector_search_base", None)
        if not (user.get("on") and user.get("key_set")):
            if baseline is not None:
                (session.web_search_on, session.tool_schemas,
                 session.tools.web_search, session.tools.web_cap) = baseline
                del session._connector_search_base
                del session._connector_search
            return
        setting = web_search_setting(session.world)
        signature = json.dumps(setting, sort_keys=True)
        if not force and getattr(session, "_connector_search", None) == signature:
            return
        if baseline is None:
            session._connector_search_base = (session.web_search_on, session.tool_schemas,
                                              session.tools.web_search, session.tools.web_cap)
        session.web_search_on = True
        session.tool_schemas = TOOL_SCHEMAS + [SEARCH_WEB_SCHEMA]
        session.tools.web_search = connector(setting)
        session.tools.web_cap = setting.get("cap")
        session._connector_search = signature


def check_user_search(world, reserve_lock=None):
    """Reserve one counted Check before sending it. Serialize the reservation with chat, not the network call."""
    from . import connectors
    with (reserve_lock if reserve_lock is not None else nullcontext()), _user_lock:
        user = user_setting(world) or {}
        if not user.get("key_set") or not user.get("cap"):
            raise ValueError("Save your Exa key and cap before checking it.")
        setting = {"on": True, "provider": "exa", "user_key": True,
                   "cap": {**user["cap"], "_world": world}}
        refused = cap_refusal(setting["cap"])
        if refused:
            raise ValueError(refused)
        doc = connectors.load(world)
        doc["search"]["checks"] = user.get("checks", 0) + 1
        connectors.save(world, doc)
    result = connector(setting)("Exa web search documentation")
    return {"count": len(result["results"])}
