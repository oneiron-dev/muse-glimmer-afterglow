"""Connectors A: calendar feeds, mail drafts, web search setup and a files folder, per person.

runs/<world>/connectors.json, created on the first save; absent means everything behaves as
today (no feeds, mail files off, search from the serving notes, no folder). Calendar URL bodies
live in runs/<world>/calendar-feeds/<slug>.ics. The search key lives in the macOS Keychain
(web_search.py); only key_set is stored here.

Calendar is read-only. A small stdlib parser reads VEVENTs: DTSTART/DTEND with TZID, UTC Z,
floating or all-day VALUE=DATE, DURATION, SUMMARY, LOCATION, and simple RRULE FREQ=DAILY/WEEKLY
with COUNT/UNTIL/BYDAY/INTERVAL expanded 60 days ahead from today in JST. Recurrence expands in
the source wall time before converting each occurrence to JST, so DST shifts stay correct; a
DATETIME UNTIL compares datetimes, not days. Multi-day events list on each JST date they touch
(continuations as all-day). Anything else (other FREQ, BYMONTH and friends, WKST, EXDATE, RDATE,
RECURRENCE-ID, EXRULE, bad or incomplete VEVENTs) is skipped and counted. A URL is fetched on
save and refreshed at most hourly when read; failures wait an hour too and keep the old cache.
calendar_events(world, date) feeds the calendar tool (tools.py) and the feed route.

Mail: when on, email.draft also writes <folder>/<id>.eml (RFC 5322 CRLF, UTF-8, X-Unsent: 1).
Search routes delegate to web_search.py. Direct backend API consumers must GET /api/connectors
first: it applies the saved per-person search choice after a restart or day switch.
"""
import datetime as dt
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from email import policy
from email.message import EmailMessage
from pathlib import Path

from . import paths, web_search

_LOCK = web_search._user_lock

JST = dt.timezone(dt.timedelta(hours=9))
HORIZON_DAYS = 60
FEED_REFRESH_S = 3600
FETCH_FAIL_THROTTLE_S = 3600
MAX_FEED_BYTES = 5 * 1024 * 1024
MAIL_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def now_iso():
    return dt.datetime.now(JST).isoformat(timespec="seconds")


def _today():
    return dt.datetime.now(JST).date()


# --- storage ---------------------------------------------------------------------------------------------------

def connectors_file(world):
    return paths.runs_root() / world / "connectors.json"


def feeds_dir(world):
    return paths.runs_root() / world / "calendar-feeds"


def load(world):
    """The person's document, or {} when nothing was saved. Never creates files."""
    f = connectors_file(world)
    if not f.exists():
        return {}
    try:
        doc = json.loads(f.read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def save(world, doc):
    """Write the whole document atomically under the shared lock. Returns the document."""
    if not isinstance(doc, dict):
        raise ValueError("connectors document must be an object")
    with _LOCK:
        f = connectors_file(world)
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
        os.replace(tmp, f)
    return doc


def _ts_to_iso(ts):
    if not ts:
        return None
    try:
        return dt.datetime.fromtimestamp(float(ts), JST).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None


def _fresh_counts(world, feed, today):
    """Current horizon counts from the cached raw text (rolling, no expiry). None when unreadable."""
    try:
        body = (feeds_dir(world) / (feed.get("file") or "")).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        events, skipped = parse_ics(body, today)
    except (ValueError, OverflowError):
        return None
    return len(events), skipped


def full_state(world):
    """GET /api/connectors shape: calendar feeds (no secrets), mail, search, files."""
    doc = load(world)
    today = _today()
    feeds = []
    for feed in (doc.get("calendar") or {}).get("feeds") or []:
        if not isinstance(feed, dict) or not feed.get("name"):
            continue
        fresh = _fresh_counts(world, feed, today)
        count, skipped = fresh if fresh else (int(feed.get("event_count") or 0), int(feed.get("skipped") or 0))
        feeds.append({"name": feed["name"], "url": feed.get("url"), "event_count": count, "skipped": skipped,
                      "updated_at": feed.get("updated_at"), "fetched_at": _ts_to_iso(feed.get("fetched_ts")),
                      "fetch_error": feed.get("fetch_error")})
    feeds.sort(key=lambda x: x["name"].lower())
    mail = doc.get("mail") or {}
    files = doc.get("files") or {}
    return {"world": world, "calendar": {"feeds": feeds},
            "mail": {"on": bool(mail.get("on")), "folder": mail.get("folder") or str(default_mail_folder())},
            "search": web_search.search_state(world), "files": {"folder": files.get("folder")}}


# --- ICS parser (stdlib only) ----------------------------------------------------------------------------------

def _unfold_ics(text):
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _parse_ics_line(line):
    if ":" not in line:
        return None, {}, ""
    left, _, value = line.partition(":")
    parts = left.split(";")
    name = parts[0].strip().upper()
    if "." in name:
        name = name.split(".", 1)[1]
    params = {}
    for part in parts[1:]:
        if "=" in part:
            key, _, val = part.partition("=")
            params[key.strip().upper()] = val.strip().strip('"')
    return name, params, value


def _unescape_ics(s):
    return (s.replace("\\N", "\n").replace("\\n", "\n").replace("\\;", ";")
             .replace("\\,", ",").replace("\\\\", "\\")).strip()


def _parse_ics_date(value):
    value = value.strip()
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError(f"bad date {value!r}")
    return dt.date(int(value[0:4]), int(value[4:6]), int(value[6:8]))


def _parse_wall(value, params):
    """An ICS date/datetime as (wall_date, wall_time or None, tzid or None, is_utc, is_allday). No conversion."""
    value = value.strip()
    kind = params.get("VALUE", "").upper()
    if kind and kind not in ("DATE", "DATE-TIME"):
        raise ValueError(f"unsupported VALUE={kind}")
    if kind == "DATE" and "T" in value:
        raise ValueError("DATE with a time")
    if kind == "DATE-TIME" and "T" not in value:
        raise ValueError("DATE-TIME without a time")
    if kind == "DATE" or re.fullmatch(r"\d{8}", value):
        return _parse_ics_date(value), None, None, False, True
    m = re.fullmatch(r"(\d{8})T(\d{4})(\d{2})?(Z)?", value)
    if not m:
        raise ValueError(f"bad datetime {value!r}")
    day = _parse_ics_date(m.group(1))
    wall = dt.time(int(m.group(2)[0:2]), int(m.group(2)[2:4]))
    is_utc = m.group(4) == "Z"
    return day, wall, None if is_utc else params.get("TZID"), is_utc, False


def _tzinfo(tzid, is_utc):
    if is_utc:
        return dt.timezone.utc
    if tzid:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(tzid)
        except Exception:
            raise ValueError(f"unknown timezone {tzid!r}") from None
    return None


def _parse_duration(value):
    s = str(value or "").strip().upper()
    m = re.fullmatch(r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", s)
    if not m or s == "P":
        return None
    return dt.timedelta(weeks=int(m.group(1) or 0), days=int(m.group(2) or 0), hours=int(m.group(3) or 0),
                        minutes=int(m.group(4) or 0), seconds=int(m.group(5) or 0))


def _parse_rrule(value):
    """Simple RRULE with UNTIL kept raw for DTSTART-aware parsing, else None (skipped and counted)."""
    rule = {}
    for part in value.strip().upper().split(";"):
        if not part or "=" not in part:
            return None
        key, _, val = part.partition("=")
        rule[key.strip()] = val.strip()
    if rule.get("FREQ") not in ("DAILY", "WEEKLY"):
        return None
    for key in rule:
        if key not in ("FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY"):
            return None  # BYMONTH and friends, plus WKST (ignored weeks would be wrong)
    out = {"FREQ": rule["FREQ"], "INTERVAL": 1, "COUNT": None, "UNTIL": rule.get("UNTIL"), "BYDAY": None}
    for key in ("INTERVAL", "COUNT"):
        if key in rule:
            try:
                out[key] = int(rule[key])
            except ValueError:
                return None
            if out[key] < 1:
                return None
    if "BYDAY" in rule:
        days = []
        for token in rule["BYDAY"].split(","):
            if token.strip() not in WEEKDAYS:
                return None
            days.append(WEEKDAYS[token.strip()])
        out["BYDAY"] = sorted(set(days))
    return out


def _rrule_wall_dates(start, rrule, win_start, win_end, until_date=None):
    """Candidate wall start dates in [win_start, win_end]. COUNT counts from the start, even before
    the window, so single-date checks stay right for old recurrences."""
    if start > win_end:
        return []
    if rrule is None:
        return [start] if win_start <= start <= win_end else []
    if until_date is not None and start > until_date:
        return []
    byday = rrule["BYDAY"] if rrule["BYDAY"] is not None else ([start.weekday()] if rrule["FREQ"] == "WEEKLY" else None)
    week0 = start - dt.timedelta(days=start.weekday())
    out, seen, day = [], 0, start
    while day <= win_end:
        if until_date is not None and day > until_date:
            break
        if rrule["FREQ"] == "DAILY":
            match = (day - start).days % rrule["INTERVAL"] == 0 and (byday is None or day.weekday() in byday)
        else:
            match = ((day - week0).days // 7) % rrule["INTERVAL"] == 0 and day.weekday() in byday
        if match:
            if rrule["COUNT"] is not None and seen >= rrule["COUNT"]:
                break
            seen += 1
            if day >= win_start:
                out.append(day)
        day += dt.timedelta(days=1)
    return out


def _vevent_from_props(props):
    """One VEVENT as wall-time start plus duration/span and an aware UNTIL. Raises ValueError to skip."""
    for name, _, _ in props:
        if name in ("EXDATE", "RDATE", "RECURRENCE-ID", "EXRULE"):
            raise ValueError("recurrence exception")
    start_raw = end_raw = duration_raw = None
    rrules = []
    title = place = None
    for name, params, value in props:
        if name == "DTSTART" and start_raw is None:
            start_raw = (value, params)
        elif name == "DTEND" and end_raw is None:
            end_raw = (value, params)
        elif name == "DURATION" and duration_raw is None:
            duration_raw = value
        elif name == "SUMMARY" and title is None:
            title = _unescape_ics(value)
        elif name == "LOCATION" and place is None:
            place = _unescape_ics(value)
        elif name == "RRULE":
            rrules.append(value)
    if start_raw is None:
        raise ValueError("VEVENT without DTSTART")
    if not title:
        raise ValueError("VEVENT without SUMMARY")
    if end_raw is not None and duration_raw is not None:
        raise ValueError("DTEND and DURATION together")
    if len(rrules) > 1:
        raise ValueError("multiple RRULEs")
    rrule = _parse_rrule(rrules[0]) if rrules else None
    if rrules and rrule is None:
        raise ValueError("unsupported RRULE")
    wall_date, wall_time, tzid, is_utc, allday = _parse_wall(*start_raw)
    tz = _tzinfo(tzid, is_utc)
    until = None
    if rrule and rrule["UNTIL"] is not None:
        raw = rrule["UNTIL"]
        if allday:
            if "T" in raw:
                raise ValueError("DATE UNTIL with a time")
            until = _parse_ics_date(raw)
        else:
            if "T" not in raw:
                raise ValueError("DATETIME UNTIL without a time")
            day, tm, _, utc, _ = _parse_wall(raw, {})
            naive = dt.datetime(day.year, day.month, day.day, tm.hour, tm.minute)
            if utc:
                aware = naive.replace(tzinfo=dt.timezone.utc)
                until = aware if tz is not None else aware.astimezone(JST).replace(tzinfo=None)
            elif tz is not None:
                until = naive.replace(tzinfo=tz)
            else:
                until = naive
    if allday:
        span_end = wall_date
        if end_raw is not None:
            end_date, _, _, _, end_allday = _parse_wall(*end_raw)
            if not end_allday:
                raise ValueError("all-day start with timed end")
            if end_date > wall_date:
                span_end = end_date - dt.timedelta(days=1)
        elif duration_raw is not None:
            dur = _parse_duration(duration_raw)
            if dur is None:
                raise ValueError("bad DURATION")
            if dur > dt.timedelta(0):
                end = dt.datetime(wall_date.year, wall_date.month, wall_date.day) + dur
                span_end = end.date() - dt.timedelta(days=1) if end.time() == dt.time(0, 0) else end.date()
                span_end = max(span_end, wall_date)
        return {"allday": True, "wall": wall_date, "span_end": span_end, "title": title,
                "place": place or None, "rrule": rrule, "until": until}
    naive_start = dt.datetime(wall_date.year, wall_date.month, wall_date.day, wall_time.hour, wall_time.minute)
    duration = dt.timedelta(0)
    if end_raw is not None:
        end_date, end_time, end_tzid, end_utc, end_allday = _parse_wall(*end_raw)
        if end_allday:
            end_naive = dt.datetime(end_date.year, end_date.month, end_date.day)
            start_cmp = naive_start.replace(tzinfo=tz) if tz else naive_start
            end_cmp = end_naive.replace(tzinfo=tz) if tz else end_naive
        elif tz is None and (end_utc or end_tzid):
            end_tz = _tzinfo(end_tzid, end_utc)
            end_cmp = dt.datetime(end_date.year, end_date.month, end_date.day,
                                  end_time.hour, end_time.minute, tzinfo=end_tz).astimezone(JST).replace(tzinfo=None)
            start_cmp = naive_start
        else:
            end_tz = _tzinfo(end_tzid, end_utc) if (end_utc or end_tzid) else tz
            start_cmp = naive_start.replace(tzinfo=tz) if tz else naive_start
            end_cmp = dt.datetime(end_date.year, end_date.month, end_date.day, end_time.hour, end_time.minute)
            end_cmp = end_cmp.replace(tzinfo=end_tz) if end_tz else end_cmp
        if end_cmp > start_cmp:
            try:
                duration = end_cmp - start_cmp
            except TypeError:
                raise ValueError("mismatched end timezone") from None
    elif duration_raw is not None:
        dur = _parse_duration(duration_raw)
        if dur is None:
            raise ValueError("bad DURATION")
        if dur > dt.timedelta(0):
            duration = dur
    return {"allday": False, "wall_date": wall_date, "wall_time": wall_time, "tz": tz, "title": title,
            "place": place or None, "rrule": rrule, "until": until, "duration": duration}


def _parse_vevents(text):
    vevents, skipped, in_event, props = [], 0, False, []
    for line in _unfold_ics(text):
        name, params, value = _parse_ics_line(line)
        if name == "BEGIN" and value.strip().upper() == "VEVENT":
            if in_event:
                skipped += 1  # previous VEVENT never ended
            in_event, props = True, []
        elif name == "END" and value.strip().upper() == "VEVENT" and in_event:
            in_event = False
            try:
                vevents.append(_vevent_from_props(props))
            except ValueError:
                skipped += 1
            props = []
        elif in_event and name:
            props.append((name, params, value))
    if in_event:
        skipped += 1  # truncated file: BEGIN without END
    return vevents, skipped


def parse_ics_window(text, window_start, window_end):
    """Occurrences in [window_start, window_end] (JST dates) as [{date, time, title, place?}], plus skipped."""
    vevents, skipped = _parse_vevents(text)
    out = []
    for ev in vevents:
        if ev["allday"]:
            span = (ev["span_end"] - ev["wall"]).days
            exp = window_start - dt.timedelta(days=span)
            for occ in _rrule_wall_dates(ev["wall"], ev["rrule"], exp, window_end, ev["until"]):
                end = min(occ + dt.timedelta(days=span), window_end)
                day = max(occ, window_start)
                while day <= end:
                    item = {"date": day.isoformat(), "time": "", "title": ev["title"]}
                    if ev["place"]:
                        item["place"] = ev["place"]
                    out.append(item)
                    day += dt.timedelta(days=1)
            continue
        dur = ev["duration"]
        buf = dur.days + (1 if dur.seconds or dur.microseconds else 0) + 2  # span plus TZ shift
        cands = _rrule_wall_dates(ev["wall_date"], ev["rrule"], window_start - dt.timedelta(days=buf),
                                  window_end + dt.timedelta(days=2), None)
        for wall in cands:
            naive = dt.datetime(wall.year, wall.month, wall.day, ev["wall_time"].hour, ev["wall_time"].minute)
            start = naive.replace(tzinfo=ev["tz"]) if ev["tz"] else naive
            if ev["until"] is not None:
                try:
                    if start > ev["until"]:
                        break  # later occurrences start later too
                except TypeError:
                    continue
            jst_start = start.astimezone(JST) if ev["tz"] else naive.replace(tzinfo=JST)
            if dur == dt.timedelta(0):
                jst_end = jst_start
            elif ev["tz"]:
                jst_end = (start + dur).astimezone(JST)
            else:
                jst_end = (naive + dur).replace(tzinfo=JST)
            span_end = jst_end.date()
            if dur > dt.timedelta(0) and jst_end.time() == dt.time(0, 0):
                span_end -= dt.timedelta(days=1)  # ends at midnight: that date is not touched
            day, end = max(jst_start.date(), window_start), min(span_end, window_end)
            if day > end:
                continue
            while day <= end:
                item = {"date": day.isoformat(), "time": jst_start.strftime("%H:%M") if day == jst_start.date() else "",
                        "title": ev["title"]}
                if ev["place"]:
                    item["place"] = ev["place"]
                out.append(item)
                day += dt.timedelta(days=1)
    out.sort(key=lambda e: (e["date"], e["time"], e["title"]))
    return out, skipped


def parse_ics(text, today=None):
    """Occurrences 60 days ahead from today in JST, plus the skipped count."""
    today = today or _today()
    return parse_ics_window(text, today, today + dt.timedelta(days=HORIZON_DAYS))


# --- calendar feeds ----------------------------------------------------------------------------------------------

def _feed_slug(name):
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip())[:60].strip("-") or "feed"


def _feed_filename(world, name, current=None):
    doc = load(world)
    taken = {f.get("file") for f in (doc.get("calendar") or {}).get("feeds") or []
             if isinstance(f, dict) and f.get("name") != name}
    if current and current not in taken:
        return current
    base, candidate, n = _feed_slug(name), "", 1
    while not candidate or candidate in taken:
        candidate = f"{base}.ics" if n == 1 else f"{base}-{n}.ics"
        n += 1
    return candidate


def _fetch_ics(url, opener=None, timeout=15):
    if not re.match(r"^https?://", url):
        raise ValueError("A subscription URL starts with http:// or https://.")
    req = urllib.request.Request(url, headers={"Accept": "text/calendar,text/plain,*/*", "User-Agent": "Afterglow/1.0"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as resp:
            raw = resp.read(MAX_FEED_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise ValueError(f"The feed did not answer (HTTP {e.code}). Check the URL.") from None
    except (OSError, ValueError):
        raise ValueError("The feed could not be read. Check the URL.") from None
    if len(raw) > MAX_FEED_BYTES:
        raise ValueError("That feed is over 5 MB, which is too large to keep.")
    return raw.decode("utf-8", "replace")


def _needs_refresh(feed, now_ts):
    if not feed.get("url"):
        return False
    failed = feed.get("failed_ts")
    if feed.get("fetch_error") and failed and now_ts - float(failed) < FETCH_FAIL_THROTTLE_S:
        return False
    fetched = feed.get("fetched_ts")
    return not fetched or now_ts - float(fetched) >= FEED_REFRESH_S


def _read_feed_ics(world, feed):
    try:
        f = feeds_dir(world) / (feed.get("file") or "")
        return f.read_text(encoding="utf-8") if f.is_file() else None
    except OSError:
        return None


def save_feed(world, name, ics_text=None, url=None, opener=None):
    """Add or replace one feed (pasted text or a URL, exactly one). Returns the public entry."""
    name = " ".join(str(name or "").split())[:80]
    if not name:
        raise ValueError("Name the calendar feed, for example Work.")
    has_text = ics_text is not None and str(ics_text).strip() != ""
    has_url = url is not None and str(url).strip() != ""
    if has_text == has_url:
        raise ValueError("Paste the .ics text or give a subscription URL, not both.")
    if has_url:
        clean_url = str(url).strip()[:2000]
        body, fetched_ts = _fetch_ics(clean_url, opener), time.time()
    else:
        clean_url, fetched_ts, body = None, None, str(ics_text)
        if len(body.encode("utf-8")) > MAX_FEED_BYTES:
            raise ValueError("That file is over 5 MB, which is too large to keep.")
        if "BEGIN:VEVENT" not in body.upper():
            raise ValueError("That text holds no calendar events (no VEVENT).")
    events, skipped = parse_ics(body)
    with _LOCK:
        doc = load(world)
        cal = doc.setdefault("calendar", {}).setdefault("feeds", [])
        existing = next((f for f in cal if isinstance(f, dict) and f.get("name") == name), None)
        filename = _feed_filename(world, name, (existing or {}).get("file"))
        feeds_dir(world).mkdir(parents=True, exist_ok=True)
        (feeds_dir(world) / filename).write_text(body, encoding="utf-8")
        entry = {"name": name, "url": clean_url, "file": filename, "event_count": len(events), "skipped": skipped,
                 "updated_at": now_iso(), "fetched_ts": fetched_ts, "fetch_error": None, "failed_ts": None}
        if existing is None:
            cal.append(entry)
        else:
            existing.update(entry)
        save(world, doc)
    return _public_feed(entry)


def _public_feed(feed):
    return {"name": feed["name"], "url": feed.get("url"), "event_count": int(feed.get("event_count") or 0),
            "skipped": int(feed.get("skipped") or 0), "updated_at": feed.get("updated_at"),
            "fetched_at": _ts_to_iso(feed.get("fetched_ts")), "fetch_error": feed.get("fetch_error")}


def remove_feed(world, name):
    name = " ".join(str(name or "").split())
    with _LOCK:
        doc = load(world)
        feeds = (doc.get("calendar") or {}).get("feeds") or []
        kept = [f for f in feeds if not (isinstance(f, dict) and f.get("name") == name)]
        if len(kept) == len(feeds):
            raise ValueError(f"No feed named {name or 'that'}.")
        gone = next(f for f in feeds if isinstance(f, dict) and f.get("name") == name)
        doc.setdefault("calendar", {})["feeds"] = kept
        save(world, doc)
        try:
            if gone.get("file"):
                (feeds_dir(world) / gone["file"]).unlink(missing_ok=True)
        except OSError:
            pass
    return {"removed": name}


def _refresh_feed(world, feed, opener=None):
    now_ts = time.time()
    if not _needs_refresh(feed, now_ts):
        return _read_feed_ics(world, feed)
    try:
        body = _fetch_ics(feed["url"], opener)
    except ValueError as e:
        with _LOCK:
            doc = load(world)
            for f in (doc.get("calendar") or {}).get("feeds") or []:
                if isinstance(f, dict) and f.get("name") == feed.get("name"):
                    f["fetch_error"], f["failed_ts"] = str(e)[:200], now_ts
                    save(world, doc)
                    break
        feed["fetch_error"], feed["failed_ts"] = str(e)[:200], now_ts
        return _read_feed_ics(world, feed)
    events, skipped = parse_ics(body)
    with _LOCK:
        doc = load(world)
        for f in (doc.get("calendar") or {}).get("feeds") or []:
            if isinstance(f, dict) and f.get("name") == feed.get("name"):
                try:
                    feeds_dir(world).mkdir(parents=True, exist_ok=True)
                    (feeds_dir(world) / f["file"]).write_text(body, encoding="utf-8")
                except OSError:
                    pass
                f.update(event_count=len(events), skipped=skipped, fetched_ts=now_ts, fetch_error=None, failed_ts=None)
                save(world, doc)
                feed.update(f)
                break
    return body


def calendar_events(world, date=None, opener=None):
    """Feed events for one date (YYYY-MM-DD) or, with date None, 60 days ahead. Each is
    {id, date, time, title, place?, source: feed:<name>, read_only: true}, sorted. Never raises for
    a bad date or a missing feed (those give []); never creates connectors.json."""
    feeds = (load(world).get("calendar") or {}).get("feeds") or []
    if not feeds:
        return []
    if date is None:
        window = None
    else:
        try:
            single = dt.date.fromisoformat(str(date))
        except ValueError:
            return []
        window = (single, single)
    today = _today()
    out = []
    for feed in feeds:
        if not isinstance(feed, dict) or not feed.get("name"):
            continue
        name = feed["name"]
        body = _read_feed_ics(world, feed)
        if feed.get("url"):
            try:
                body = _refresh_feed(world, feed, opener)
            except (OSError, ValueError):
                body = _read_feed_ics(world, feed)
        if not body:
            continue
        try:
            events, _ = parse_ics(body, today) if window is None else parse_ics_window(body, *window)
        except (ValueError, OverflowError):
            continue
        groups = {}
        for ev in events:
            key = (ev["date"], ev["time"])
            groups[key] = groups.get(key, 0) + 1
            item = {"id": f"feed:{name}:{ev['date']}:{ev['time'] or 'allday'}:{groups[key]}",
                    "date": ev["date"], "time": ev["time"], "title": ev["title"],
                    "source": f"feed:{name}", "read_only": True}
            if ev.get("place"):
                item["place"] = ev["place"]
            out.append(item)
    out.sort(key=lambda e: (e["date"], e["time"], e["title"]))
    return out


# --- mail drafts -------------------------------------------------------------------------------------------------

def default_mail_folder():
    return Path.home() / "Documents" / "Afterglow drafts"


def mail_state(world):
    mail = load(world).get("mail") or {}
    return {"on": bool(mail.get("on")), "folder": mail.get("folder") or str(default_mail_folder())}


def save_mail(world, on, folder=None):
    if not isinstance(on, bool):
        raise ValueError("The mail switch must be on or off.")
    target = Path(str(folder).strip()).expanduser() if folder is not None and str(folder).strip() != "" else \
        Path(mail_state(world)["folder"]).expanduser()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise ValueError(f"That mail folder could not be made ({target}).") from None
    if not target.is_dir() or not os.access(target, os.W_OK):
        raise ValueError(f"That mail folder is not writable ({target}).")
    with _LOCK:
        doc = load(world)
        doc["mail"] = {"on": on, "folder": str(target)}
        save(world, doc)
    return mail_state(world)


def write_mail_draft(world, m):
    """Write one email.draft as <folder>/<id>.eml (RFC 5322 CRLF, UTF-8, X-Unsent: 1). None when off;
    raises OSError/ValueError when on and the file cannot be written (the tool reports it).
    Use a distinct folder per person; the shared default is last-writer wins."""
    if not mail_state(world)["on"]:
        return None
    mid = str((m or {}).get("id") or "")
    if not MAIL_ID_RE.fullmatch(mid):
        raise ValueError(f"Bad draft id {mid!r}.")
    folder = Path(mail_state(world)["folder"]).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{mid}.eml"
    if path.resolve().parent != folder.resolve():
        raise ValueError("Bad draft id.")
    msg = EmailMessage(policy=policy.SMTP)
    msg["To"] = " ".join(str(m.get("to") or "").split()) or "(no recipient)"
    msg["Subject"] = " ".join(str(m.get("subject") or "").split()) or "(no subject)"
    msg["X-Unsent"] = "1"
    msg.set_content(str(m.get("body") or ""))
    path.write_bytes(msg.as_bytes())
    return path


def open_mail_draft(world, mid, runner=None):
    mid = str(mid or "")
    if not mail_state(world)["on"]:
        raise ValueError("Turn the mail connector on first.")
    if not MAIL_ID_RE.fullmatch(mid):
        raise ValueError("No draft with that id.")
    folder = Path(mail_state(world)["folder"]).expanduser()
    path = folder / f"{mid}.eml"
    try:
        if path.resolve().parent != folder.resolve():
            raise ValueError("No draft with that id.")
    except OSError:
        raise ValueError("No draft with that id.") from None
    if not path.is_file():
        raise ValueError(f"Draft {mid} has no file yet.")
    try:
        result = (runner or subprocess.run)(["open", str(path)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"The draft could not be opened ({type(e).__name__}).") from None
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError("The draft could not be opened.")
    return {"opened": mid, "file": str(path)}


# --- files folder ------------------------------------------------------------------------------------------------

def files_folder(world):
    """The person's files folder as a Path, or None when none is set. Never creates files."""
    folder = (load(world).get("files") or {}).get("folder")
    if not folder:
        return None
    try:
        return Path(str(folder)).expanduser()
    except (TypeError, ValueError):
        return None


def validate_files_folder(folder):
    raw = str(folder or "").strip()
    if not raw:
        raise ValueError("Give the folder path, for example ~/Documents.")
    try:
        resolved = Path(raw).expanduser().resolve()
    except OSError:
        raise ValueError(f"No folder at {raw}.") from None
    if resolved == Path("/").resolve() or resolved == Path.home().resolve():
        raise ValueError("Pick a folder inside your home, not the home folder or the disk itself.")
    if not resolved.is_dir():
        raise ValueError(f"No folder at {raw}.")
    if not os.access(resolved, os.R_OK):
        raise ValueError(f"That folder cannot be read ({raw}).")
    try:
        next(resolved.iterdir())
    except StopIteration:
        pass
    except OSError:
        raise ValueError(f"That folder cannot be read ({raw}).") from None
    return resolved


def save_files_folder(world, folder):
    if folder is None or str(folder).strip() == "":
        with _LOCK:
            doc = load(world)
            doc["files"] = {"folder": None}
            save(world, doc)
        return {"folder": None}
    resolved = validate_files_folder(folder)
    with _LOCK:
        doc = load(world)
        doc["files"] = {"folder": str(resolved)}
        save(world, doc)
    return {"folder": str(resolved)}


# --- routes (search state lives in web_search.py) ----------------------------------------------------------------

def _search_error(e):
    text = str(e).lower()
    if "http 401" in text or "http 403" in text or "empty" in text or "not readable" in text:
        raise ValueError("Your Exa key did not work. Check it and save it again.") from None
    raise RuntimeError("The web search did not answer. Try again in a moment.") from None


def get_connectors(app, req):
    web_search.apply_search(app)
    return full_state(app.session.world)


def post_calendar(app, req):
    body = req.body or {}
    save_feed(app.session.world, body.get("name"), body.get("ics_text"), body.get("url"))
    return full_state(app.session.world)


def remove_calendar(app, req):
    remove_feed(app.session.world, (req.body or {}).get("name"))
    return full_state(app.session.world)


def calendar_events_route(app, req):
    date = (req.query or {}).get("date")
    if date:
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            raise ValueError("Date must be YYYY-MM-DD.") from None
        return {"date": date, "events": calendar_events(app.session.world, date)}
    return {"events": calendar_events(app.session.world)}


def post_mail(app, req):
    body = req.body or {}
    if "on" not in body:
        raise ValueError("The mail switch must be on or off.")
    save_mail(app.session.world, body.get("on"), body.get("folder"))
    return full_state(app.session.world)


def open_mail(app, req):
    mid = str((req.body or {}).get("id") or "")
    drafts = app.session.world_state.state.get("emails") or []
    draft = next((d for d in drafts if isinstance(d, dict) and d.get("id") == mid), None)
    if draft is None:
        raise ValueError("No draft with that id in the current day.")
    world = app.session.world
    if not mail_state(world)["on"]:
        raise ValueError("Turn the mail connector on first.")
    try:
        write_mail_draft(world, draft)  # always current-world content; shared folder is last-writer wins
    except (OSError, ValueError):
        raise RuntimeError("The draft file could not be written. Check your Mail connector folder.") from None
    return open_mail_draft(world, mid)


def post_search(app, req):
    try:
        web_search.save_user_setting(app.session.world, req.body or {})
    except web_search.WebSearchError as e:
        _search_error(e)
    web_search.apply_search(app, force=True)
    return full_state(app.session.world)


def check_search(app, req):
    try:
        result = web_search.check_user_search(app.session.world, reserve_lock=app.lock)
    except web_search.WebSearchError as e:
        _search_error(e)
    return {"count": result["count"], **full_state(app.session.world)}


def post_files(app, req):
    from .files_store import FileStore

    # The open chat's file tools follow the setting at once: the new folder, or after a disconnect the app's own
    # files area (runs/<world>/files/, as at startup without a folder); never the old folder.
    with app.lock:
        session = app.session
        save_files_folder(session.world, (req.body or {}).get("folder"))
        session.tools.files = FileStore(session.world)
        return full_state(session.world)


ROUTES = {
    ("GET", "/api/connectors"): get_connectors,
    ("POST", "/api/connectors/calendar"): post_calendar,
    ("POST", "/api/connectors/calendar/remove"): remove_calendar,
    ("GET", "/api/connectors/calendar/events"): calendar_events_route,
    ("POST", "/api/connectors/mail"): post_mail,
    ("POST", "/api/connectors/mail/open"): open_mail,
    ("POST", "/api/connectors/search"): post_search,
    ("POST", "/api/connectors/search/check"): check_search,
    ("POST", "/api/connectors/files"): post_files,
}
