"""Connectors A backend tests (connectors.py). Offline only: fake Keychain, network and open."""
import datetime as dt
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import connectors, web_search  # noqa: E402
from afterglow.tools import ToolBox, WorldState  # noqa: E402

TODAY = dt.date(2026, 11, 4)
JST = connectors.JST


def ics(*events):
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n" + "".join(events) + "END:VCALENDAR\r\n"


def event(uid, dtstart, summary="Title", location=None, rrule=None, dtend=None, duration=None, extra=""):
    lines = [f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTART:{dtstart}\r\n"]
    if dtend:
        lines.append(f"DTEND:{dtend}\r\n")
    if duration:
        lines.append(f"DURATION:{duration}\r\n")
    lines.append(f"SUMMARY:{summary}\r\n")
    if location:
        lines.append(f"LOCATION:{location}\r\n")
    if rrule:
        lines.append(f"RRULE:{rrule}\r\n")
    lines.append(extra)
    lines.append("END:VEVENT\r\n")
    return "".join(lines)


def event_params(uid, start_line, summary="Title", end_line="", rrule=None, extra=""):
    out = f"BEGIN:VEVENT\r\nUID:{uid}\r\n{start_line}\r\n"
    if end_line:
        out += f"{end_line}\r\n"
    out += f"SUMMARY:{summary}\r\n"
    if rrule:
        out += f"RRULE:{rrule}\r\n"
    return out + extra + "END:VEVENT\r\n"


class TempRunsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {"AFTERGLOW_RUNS": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.today = mock.patch.object(connectors, "_today", return_value=TODAY)
        self.today.start()
        self.addCleanup(self.today.stop)
        self.runs = Path(self.tmp.name)

    def app(self, world="person", emails=None):
        session = SimpleNamespace(world=world, world_state=SimpleNamespace(state={"emails": emails or []}))
        return SimpleNamespace(session=session, lock=threading.Lock())

    def req(self, body=None, query=None):
        return SimpleNamespace(body=body or {}, query=query or {})


class ICSTest(TempRunsTest):
    def test_timed_tzid_utc_allday_weekly_count_and_broken_skipped(self):
        body = ics(
            event_params("1", "DTSTART;TZID=America/New_York:20261104T100000", "NY standup"),
            event("2", "20261104T120000Z", "UTC review"),
            event_params("3", "DTSTART;VALUE=DATE:20261105", "All hands"),
            event("4", "20261102T090000", "Weekly sync", rrule="FREQ=WEEKLY;COUNT=3"),
            "BEGIN:VEVENT\r\nUID:5\r\nSUMMARY:Broken has no start\r\nEND:VEVENT\r\n",
        )
        found, skipped = connectors.parse_ics(body, TODAY)
        self.assertEqual(skipped, 1)
        by_title = {}
        for e in found:
            by_title.setdefault(e["title"], []).append(e)
        # NY 10:00 EST (Nov 4, after fall-back) is JST Nov 5 00:00.
        self.assertEqual([(e["date"], e["time"]) for e in by_title["NY standup"]], [("2026-11-05", "00:00")])
        self.assertEqual([(e["date"], e["time"]) for e in by_title["UTC review"]], [("2026-11-04", "21:00")])
        self.assertEqual([(e["date"], e["time"]) for e in by_title["All hands"]], [("2026-11-05", "")])
        self.assertEqual([e["date"] for e in by_title["Weekly sync"]], ["2026-11-09", "2026-11-16"])
        win, _ = connectors.parse_ics_window(body, dt.date(2026, 11, 2), dt.date(2026, 11, 2) + dt.timedelta(days=60))
        self.assertEqual([e["date"] for e in win if e["title"] == "Weekly sync"],
                         ["2026-11-02", "2026-11-09", "2026-11-16"])

    def test_allday_dtend_span_and_timed_midnight_span(self):
        body = ics(
            event_params("1", "DTSTART;VALUE=DATE:20261104", "Conf", end_line="DTEND;VALUE=DATE:20261106"),
            event("2", "20261104T230000", "Late shift", dtend="20261105T010000"),
        )
        found, skipped = connectors.parse_ics(body, TODAY)
        self.assertEqual(skipped, 0)
        conf = [e for e in found if e["title"] == "Conf"]
        self.assertEqual([(e["date"], e["time"]) for e in conf], [("2026-11-04", ""), ("2026-11-05", "")])
        late = [e for e in found if e["title"] == "Late shift"]
        self.assertEqual([(e["date"], e["time"]) for e in late], [("2026-11-04", "23:00"), ("2026-11-05", "")])

    def test_duration_and_tzid_end(self):
        body = ics(
            event("1", "20261104T100000", "Two hours", duration="PT2H"),
            event_params("2", "DTSTART;VALUE=DATE:20261104", "Two days", end_line="", extra="DURATION:P2D\r\n"),
            event_params("3", "DTSTART;TZID=America/New_York:20261026T100000", "NY long",
                         end_line="DTEND;TZID=America/New_York:20261026T120000"),
        )
        found, _ = connectors.parse_ics(body, dt.date(2026, 10, 26))
        two = [e for e in found if e["title"] == "Two hours"]
        self.assertEqual([(e["date"], e["time"]) for e in two], [("2026-11-04", "10:00")])
        days = [e for e in found if e["title"] == "Two days"]
        self.assertEqual([e["date"] for e in days], ["2026-11-04", "2026-11-05"])
        # 10:00-12:00 EDT Oct 26 is JST 23:00 Oct 26 to 01:00 Oct 27: two JST dates.
        long = [e for e in found if e["title"] == "NY long"]
        self.assertEqual([(e["date"], e["time"]) for e in long], [("2026-10-26", "23:00"), ("2026-10-27", "")])

    def test_dst_expands_wall_time_before_jst(self):
        body = ics(event_params("1", "DTSTART;TZID=America/New_York:20261026T100000", "Mon standup",
                                rrule="FREQ=WEEKLY;COUNT=2"))
        found, _ = connectors.parse_ics(body, dt.date(2026, 10, 26))
        # Oct 26 EDT 10:00 is JST 23:00 same day; Nov 2 EST 10:00 is JST 00:00 next day.
        self.assertEqual([(e["date"], e["time"]) for e in found],
                         [("2026-10-26", "23:00"), ("2026-11-03", "00:00")])

    def test_until_datetime_compares_time_not_day(self):
        body = ics(event("1", "20261104T100000", "Daily", rrule="FREQ=DAILY;UNTIL=20261105T090000"))
        found, _ = connectors.parse_ics(body, TODAY)
        self.assertEqual([e["date"] for e in found], ["2026-11-04"])

    def test_byday_weekly_count(self):
        body = ics(event("1", "20261102T090000", "MonWed", rrule="FREQ=WEEKLY;BYDAY=MO,WE;COUNT=4"))
        found, skipped = connectors.parse_ics_window(body, dt.date(2026, 11, 2), dt.date(2026, 11, 30))
        self.assertEqual(skipped, 0)
        self.assertEqual([e["date"] for e in found], ["2026-11-02", "2026-11-04", "2026-11-09", "2026-11-11"])

    def test_until_date_allday_inclusive(self):
        body = ics(event_params("1", "DTSTART;VALUE=DATE:20261104", "Daily stand", rrule="FREQ=DAILY;UNTIL=20261106"))
        found, skipped = connectors.parse_ics(body, TODAY)
        self.assertEqual(skipped, 0)
        self.assertEqual([e["date"] for e in found], ["2026-11-04", "2026-11-05", "2026-11-06"])

    def test_unknown_tzid_and_invalid_end_skip(self):
        body = ics(
            event_params("1", "DTSTART;TZID=Nope/Nowhere:20261104T100000", "Bad zone"),
            event("2", "20261104T100000", "Bad end", dtend="not-a-date"),
            event("3", "20261104T100000", "Bad dur", duration="P1M"),
        )
        found, skipped = connectors.parse_ics(body, TODAY)
        self.assertEqual((found, skipped), ([], 3))

    def test_exceptions_unsupported_and_malformed_count(self):
        body = ics(
            event("1", "20261104T100000", "Has exdate", extra="EXDATE:20261105T100000\r\n"),
            event("2", "20261104T100000", "Has rdate", extra="RDATE:20261105T100000\r\n"),
            event("3", "20261104T100000", "Moved", extra="RECURRENCE-ID:20261104T100000\r\n"),
            event("4", "20261104T100000", "Monthly", rrule="FREQ=MONTHLY;COUNT=2"),
            event("5", "20261104T100000", "By month", rrule="FREQ=WEEKLY;BYMONTH=1;COUNT=2"),
            event("6", "20261104T100000", "Wkst", rrule="FREQ=WEEKLY;INTERVAL=2;WKST=SU;COUNT=2"),
            "BEGIN:VEVENT\r\nUID:7\r\nDTSTART:20261104T100000\r\nEND:VEVENT\r\n",  # no SUMMARY
            "BEGIN:VEVENT\r\nUID:8\r\nSUMMARY:No start\r\nEND:VEVENT\r\n",  # no DTSTART
            event("9", "20261104T100000", "Both ends", dtend="20261104T110000", duration="PT1H"),
            "BEGIN:VEVENT\r\nUID:10\r\nDTSTART:20261104T100000\r\nSUMMARY:Truncated\r\n",  # no END
        )
        found, skipped = connectors.parse_ics(body, TODAY)
        self.assertEqual((found, skipped), ([], 10))


class CalendarFeedsTest(TempRunsTest):
    def test_save_text_keeps_raw_for_rolling_horizon(self):
        raw = ics(event("1", "20261110T100000", "Future"))
        feed = connectors.save_feed("person", "Work", ics_text=raw)
        self.assertEqual(feed["event_count"], 1)
        cached = self.runs / "person" / "calendar-feeds" / "Work.ics"
        self.assertEqual(cached.read_bytes(), raw.encode())
        self.assertEqual(len(connectors.calendar_events("person")), 1)
        self.assertEqual(len(connectors.calendar_events("person", "2026-11-10")), 1)
        with mock.patch.object(connectors, "_today", return_value=dt.date(2026, 12, 1)):
            self.assertEqual(connectors.calendar_events("person"), [])  # past, outside rolling horizon
            self.assertEqual(len(connectors.calendar_events("person", "2026-11-10")), 1)  # single date still lists

    def test_url_only_http_and_errors_hide_token(self):
        with self.assertRaises(ValueError) as e:
            connectors.save_feed("person", "Bad", url="ftp://example.com/feed.ics")
        self.assertNotIn("ftp://", str(e.exception))
        token_url = "https://example.com/feed?token=fake-token-123"

        class HTTP:
            def read(self, n=-1):
                raise connectors.urllib.error.HTTPError(token_url, 404, "x", {}, None)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with self.assertRaises(ValueError) as e:
            connectors.save_feed("person", "Tok", url=token_url, opener=lambda req, timeout=None: HTTP())
        self.assertNotIn("fake-token-123", str(e.exception))
        self.assertFalse((self.runs / "person" / "connectors.json").exists())

    def test_failed_refresh_waits_hour_and_keeps_cache(self):
        import time as time_mod

        now = time_mod.time()
        self.assertFalse(connectors._needs_refresh({"url": None}, now))
        self.assertTrue(connectors._needs_refresh({"url": "https://x.test/f.ics"}, now))
        self.assertFalse(connectors._needs_refresh({"url": "https://x.test/f.ics", "fetched_ts": now - 100}, now))
        self.assertTrue(connectors._needs_refresh({"url": "https://x.test/f.ics", "fetched_ts": now - 3700}, now))
        recent_fail = {"url": "https://x.test/f.ics", "fetched_ts": now - 3700,
                       "fetch_error": "e", "failed_ts": now - 100}
        self.assertFalse(connectors._needs_refresh(recent_fail, now))
        old_fail = {"url": "https://x.test/f.ics", "fetched_ts": now - 3700,
                    "fetch_error": "e", "failed_ts": now - 3700}
        self.assertTrue(connectors._needs_refresh(old_fail, now))
        never_ok = {"url": "https://x.test/f.ics", "fetch_error": "e", "failed_ts": now - 100}
        self.assertFalse(connectors._needs_refresh(never_ok, now))

    def test_url_refresh_success_and_failed_call_counts(self):
        import time as time_mod

        v1 = ics(event("1", "20261110T100000", "V1"))
        v2 = ics(event("1", "20261110T100000", "V2 one"), event("2", "20261111T100000", "V2 two"))
        calls = []

        class Resp:
            def __init__(self, body):
                self.body = body
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self, n=-1):
                return self.body.encode()

        def ok(body):
            def opener(req, timeout=None):
                calls.append(getattr(req, "full_url", ""))
                return Resp(body)
            return opener

        connectors.save_feed("person", "Sub", url="https://x.test/f.ics", opener=ok(v1))
        self.assertEqual(len(calls), 1)
        self.assertEqual([e["title"] for e in connectors.calendar_events("person")], ["V1"])
        real_now = time_mod.time()
        with mock.patch("time.time", return_value=real_now + 3700):
            out = connectors.calendar_events("person", opener=ok(v2))
            self.assertEqual(sorted(e["title"] for e in out), ["V2 one", "V2 two"])
            self.assertEqual(len(calls), 2)

        def failing(req, timeout=None):
            calls.append("fail")
            raise connectors.urllib.error.HTTPError("https://x.test/f.ics", 500, "x", {}, None)

        with mock.patch("time.time", return_value=real_now + 7400):
            out = connectors.calendar_events("person", opener=failing)  # keeps old cache
            self.assertEqual(sorted(e["title"] for e in out), ["V2 one", "V2 two"])
            self.assertEqual(len(calls), 3)
            out2 = connectors.calendar_events("person", opener=failing)  # throttled, no new call
            self.assertEqual(sorted(e["title"] for e in out2), ["V2 one", "V2 two"])
            self.assertEqual(len(calls), 3)
        state = connectors.full_state("person")["calendar"]["feeds"][0]
        self.assertIsNotNone(state["fetch_error"])


class MailTest(TempRunsTest):
    def test_write_none_when_off_and_raises_when_on_fails(self):
        self.assertIsNone(connectors.write_mail_draft("person", {"id": "M001", "to": "a", "subject": "s", "body": "b"}))
        folder = Path(tempfile.mkdtemp())
        connectors.save_mail("person", True, str(folder))
        with self.assertRaises(ValueError):
            connectors.write_mail_draft("person", {"id": "../evil", "to": "a", "subject": "s", "body": "b"})
        with mock.patch.object(Path, "write_bytes", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                connectors.write_mail_draft("person", {"id": "M001", "to": "a", "subject": "s", "body": "b"})

    def test_eml_smtp_crlf_headers_utf8(self):
        folder = Path(tempfile.mkdtemp())
        connectors.save_mail("person", True, str(folder))
        path = connectors.write_mail_draft("person", {"id": "M001", "to": "amy@test", "subject": "caf\u00e9",
                                                      "body": "hi caf\u00e9"})
        raw = path.read_bytes()
        self.assertIn(b"\r\n", raw)
        self.assertIn(b"X-Unsent: 1", raw)
        self.assertIn(b"To: amy@test", raw)
        self.assertIn("caf\u00e9".encode("utf-8"), raw)

    def test_open_refuses_off_and_uses_fake_runner(self):
        with self.assertRaises(ValueError):
            connectors.open_mail_draft("person", "M001", runner=lambda *a, **k: None)
        folder = Path(tempfile.mkdtemp())
        connectors.save_mail("person", True, str(folder))
        connectors.write_mail_draft("person", {"id": "M001", "to": "a", "subject": "s", "body": "b"})
        calls = []

        def runner(cmd, **kwargs):
            calls.append(cmd)
            return SimpleNamespace(returncode=0)

        out = connectors.open_mail_draft("person", "M001", runner=runner)
        self.assertEqual(out["opened"], "M001")
        self.assertEqual(calls, [["open", str(folder / "M001.eml")]])

    def test_open_route_validates_session_and_materializes(self):
        folder = Path(tempfile.mkdtemp())
        connectors.save_mail("person", True, str(folder))
        draft = {"id": "M001", "to": "a", "subject": "s", "body": "b"}
        app = self.app(emails=[draft])
        with self.assertRaises(ValueError):
            connectors.open_mail(app, self.req({"id": "M999"}))
        with mock.patch.object(connectors, "open_mail_draft", return_value={"opened": "M001"}) as opened:
            connectors.open_mail(app, self.req({"id": "M001"}))
            self.assertTrue((folder / "M001.eml").is_file())  # created before enabling, written on open
            opened.assert_called_once_with("person", "M001")

    def test_open_route_rewrites_stale_file(self):
        folder = Path(tempfile.mkdtemp())
        connectors.save_mail("person", True, str(folder))
        (folder / "M001.eml").write_bytes(b"old stale content")
        draft = {"id": "M001", "to": "a", "subject": "s", "body": "new body"}
        app = self.app(emails=[draft])
        with mock.patch.object(connectors, "open_mail_draft", return_value={"opened": "M001"}):
            connectors.open_mail(app, self.req({"id": "M001"}))
            self.assertIn(b"new body", (folder / "M001.eml").read_bytes())
            self.assertNotIn(b"old stale", (folder / "M001.eml").read_bytes())

    def test_email_tool_writes_file_only_when_on(self):
        ws = WorldState(self.runs / "off" / "1" / "world_state.json", seed={"calendar": [], "tasks": []})
        out, _ = ToolBox(None, ws, day=1).run("email.draft", {"to": "a", "subject": "s", "body": "b"})
        self.assertIn("drafted", out)  # off: no file, no error, real home untouched
        folder = Path(tempfile.mkdtemp())
        connectors.save_mail("writer", True, str(folder))
        ws = WorldState(self.runs / "writer" / "1" / "world_state.json", seed={"calendar": [], "tasks": []})
        out, _ = ToolBox(None, ws, day=1).run("email.draft", {"to": "a", "subject": "s", "body": "b"})
        self.assertIn("drafted", out)
        self.assertIn(b"X-Unsent: 1", (folder / "M001.eml").read_bytes())
        connectors.save_mail("failer", True, str(Path(tempfile.mkdtemp())))
        ws = WorldState(self.runs / "failer" / "1" / "world_state.json", seed={"calendar": [], "tasks": []})
        with mock.patch.object(Path, "write_bytes", side_effect=OSError("disk")):
            out, _ = ToolBox(None, ws, day=1).run("email.draft", {"to": "a", "subject": "s", "body": "b"})
            self.assertIn("could not be saved as a file", out["error"])
            self.assertEqual(ws.state["emails"], [])


class FilesTest(TempRunsTest):
    def open_files_session(self):
        from afterglow.agent import DaySession

        folder = self.runs / "folder-a"
        folder.mkdir()
        (folder / "a.txt").write_text("from A")
        connectors.save_files_folder("person", str(folder))
        with mock.patch.dict(os.environ, {"AFTERGLOW_WORLDS": str(self.runs / "worlds")}):
            session = DaySession("person", 1, client=SimpleNamespace(), date="2026-11-04",
                                 decide_supersede=False, decide_route=False, web_search=False, dials=[])
        return SimpleNamespace(session=session, lock=threading.Lock()), folder

    def test_save_folder_rebinds_open_session_file_tools(self):
        app, folder_a = self.open_files_session()
        folder_b = self.runs / "folder-b"
        folder_b.mkdir()
        (folder_b / "b.txt").write_text("from B")
        save = connectors.save_files_folder
        locked = []

        def saving(world, folder):
            locked.append(app.lock.locked())
            return save(world, folder)

        with mock.patch.object(connectors, "save_files_folder", side_effect=saving):
            connectors.post_files(app, self.req({"folder": str(folder_b)}))
        listed, _ = app.session.tools.run("files.list", {})
        self.assertEqual([entry["name"] for entry in listed], ["b.txt"])
        read, _ = app.session.tools.run("files.read", {"id": listed[0]["id"]})
        self.assertEqual(read["text"], "from B")
        written, _ = app.session.tools.run("files.write", {"name": "new.txt", "text": "new in B"})
        self.assertNotIn("error", written)
        self.assertEqual((folder_b / "new.txt").read_text(), "new in B")
        self.assertEqual(sorted(p.name for p in folder_a.iterdir()), ["a.txt"])
        self.assertEqual(locked, [True])
        self.assertFalse(app.lock.locked())

    def test_disconnect_moves_open_session_file_tools_off_the_old_folder(self):
        app, folder_a = self.open_files_session()
        save = connectors.save_files_folder
        locked = []

        def saving(world, folder):
            locked.append(app.lock.locked())
            return save(world, folder)

        with mock.patch.object(connectors, "save_files_folder", side_effect=saving):
            connectors.post_files(app, self.req({"folder": None}))
        result, _ = app.session.tools.run("files.write", {"name": "after.txt", "text": "not for folder A"})
        self.assertNotIn("error", result)
        self.assertIsNone(connectors.files_folder("person"))
        # the old folder is untouched; the write lands in the app's own files area, as after a fresh start
        self.assertEqual(sorted(p.name for p in folder_a.iterdir()), ["a.txt"])
        self.assertTrue((self.runs / "person" / "files" / "after.txt").exists())
        self.assertEqual(locked, [True])
        self.assertFalse(app.lock.locked())

    def test_validate_and_save(self):
        self.assertIsNone(connectors.files_folder("person"))
        with self.assertRaises(ValueError):
            connectors.validate_files_folder("/nope-missing-xyz")
        with self.assertRaises(ValueError):
            connectors.validate_files_folder("/")
        with self.assertRaises(ValueError):
            connectors.validate_files_folder(str(Path.home()))
        with mock.patch.object(Path, "iterdir", side_effect=OSError("denied")):
            with self.assertRaises(ValueError):
                connectors.validate_files_folder(tempfile.gettempdir())
        folder = Path(tempfile.mkdtemp())
        (folder / "b.txt").write_bytes(b"12")
        (folder / "a.txt").write_bytes(b"1")
        connectors.save_files_folder("person", str(folder))
        self.assertEqual(connectors.files_folder("person"), folder.resolve())
        self.assertEqual(connectors.save_files_folder("person", "")["folder"], None)
        self.assertIsNone(connectors.files_folder("person"))
        self.assertNotIn(("GET", "/api/connectors/files"), connectors.ROUTES)  # the list is meta-files' GET /api/files


class RoutesTest(TempRunsTest):
    def test_post_mail_rejects_non_bool(self):
        with self.assertRaises(ValueError):
            connectors.post_mail(self.app(), self.req({"on": "true"}))
        with self.assertRaises(ValueError):
            connectors.post_mail(self.app(), self.req({}))

    def test_post_search_forces_apply(self):
        with mock.patch.object(web_search, "save_user_setting", return_value={}) as saved, \
                mock.patch.object(web_search, "apply_search") as applied:
            connectors.post_search(self.app(), self.req({"on": False}))
            saved.assert_called_once()
            applied.assert_called_once_with(mock.ANY, force=True)

    def test_check_reserves_under_app_lock_but_search_runs_unlocked(self):
        app = self.app()
        cap = {"searches": 3, "through": "2099-11-01"}
        with mock.patch.object(web_search, "keychain_set"):
            web_search.save_user_setting("person", {"on": True, "key": "fake-key-for-tests", "cap": cap})
        save = connectors.save
        def saving(world, doc):
            self.assertTrue(app.lock.locked())
            return save(world, doc)
        def search(query):
            self.assertFalse(app.lock.locked())
            return {"results": [{}, {}]}
        with mock.patch.object(connectors, "save", side_effect=saving), \
                mock.patch.object(web_search.ExaWeb, "search", side_effect=search):
            out = connectors.check_search(app, self.req({}))
            self.assertEqual(out["count"], 2)
        self.assertFalse(app.lock.locked())


class DefaultsTest(TempRunsTest):
    def test_absent_config_changes_nothing_and_creates_no_files(self):
        self.assertEqual(connectors.calendar_events("person", "2026-11-04"), [])
        self.assertEqual(connectors.load("person"), {})
        self.assertFalse(connectors.mail_state("person")["on"])
        self.assertFalse((self.runs / "person").exists())

    def test_toolbox_no_config_identical_and_no_files(self):
        seed = {"calendar": [{"id": "E001", "date": "2026-11-04", "time": "09:00", "title": "Local standup"}], "tasks": []}
        ws = WorldState(self.runs / "person" / "1" / "world_state.json", seed=seed)
        out, _ = ToolBox(None, ws, day=1).run("calendar.list_events", {"date": "2026-11-04"})
        self.assertEqual(out["events"], seed["calendar"])
        self.assertFalse((self.runs / "person" / "connectors.json").exists())
        self.assertFalse((self.runs / "person" / "calendar-feeds").exists())

    def test_toolbox_merges_feed_events(self):
        connectors.save_feed("person", "Work", ics_text=ics(event("1", "20261104T100000", "Feed review")))
        seed = {"calendar": [{"id": "E001", "date": "2026-11-04", "time": "09:00", "title": "Local standup"}], "tasks": []}
        ws = WorldState(self.runs / "person" / "1" / "world_state.json", seed=seed)
        out, _ = ToolBox(None, ws, day=1).run("calendar.list_events", {"date": "2026-11-04"})
        self.assertEqual([(e["time"], e["title"]) for e in out["events"]], [("09:00", "Local standup"), ("10:00", "Feed review")])
        feed = out["events"][1]
        self.assertEqual(feed["source"], "feed:Work")
        self.assertTrue(feed["read_only"])
        self.assertTrue(feed["id"].startswith("feed:"))


class KeychainContractTest(TempRunsTest):
    def test_keychain_fixed_names_through_fake_runner(self):
        calls = []

        def runner(cmd, **kwargs):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="fake-key-for-tests\n")

        web_search.keychain_set("fake-key-for-tests", runner)
        self.assertEqual(calls[0], ["security", "add-generic-password", "-s", "afterglow-web-search",
                                    "-a", "exa", "-w", "fake-key-for-tests", "-U"])
        self.assertEqual(web_search.keychain_get(runner), "fake-key-for-tests")


if __name__ == "__main__":
    unittest.main()
