"""Offline user-key and cap tests. Never access the real Keychain or Exa."""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from afterglow import connectors, web_search
from afterglow.tools import TOOL_SCHEMAS


class UserSearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runs = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"AFTERGLOW_RUNS": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cap = {"searches": 3, "through": (dt.date.today() + dt.timedelta(days=2)).isoformat()}

    def save(self, world="person", **extra):
        with mock.patch.object(web_search, "keychain_set"):
            return web_search.save_user_setting(world, {"on": True, "key": "fake-key-for-tests",
                                                       "cap": self.cap, **extra})

    def test_keychain_uses_fixed_service_account_and_fake_runner(self):
        calls = []
        def runner(cmd, **kwargs):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="fake-key-for-tests\n")
        web_search.keychain_set("fake-key-for-tests", runner)
        self.assertEqual(calls[0], ["security", "add-generic-password", "-s", "afterglow-web-search",
                                    "-a", "exa", "-w", "fake-key-for-tests", "-U"])
        self.assertEqual(web_search.keychain_get(runner), "fake-key-for-tests")
        self.assertEqual(calls[1], ["security", "find-generic-password", "-s", "afterglow-web-search",
                                    "-a", "exa", "-w"])
        def fail(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd, stderr="fake-key-for-tests")
        with self.assertRaises(web_search.WebSearchError) as error:
            web_search.keychain_set("fake-key-for-tests", fail)
        self.assertNotIn("fake-key-for-tests", str(error.exception))

    def test_settings_store_no_key_and_are_per_person(self):
        state = self.save()
        self.assertTrue(state["on"])
        self.assertTrue(state["user_on"])
        self.assertTrue(state["key_set"])
        self.assertNotIn("fake-key-for-tests", (self.runs / "person" / "connectors.json").read_text())
        self.assertNotIn("fake-key-for-tests", json.dumps(state))
        self.assertTrue(web_search.web_search_setting("person")["user_key"])
        self.assertFalse(web_search.web_search_setting("other")["on"])
        self.assertFalse((self.runs / "other" / "connectors.json").exists())

    def test_no_saved_setting_is_identical_to_serving_and_does_not_create_files(self):
        serving = self.runs / "serving"
        serving.mkdir()
        (serving / "serving-rule.json").write_text(json.dumps({"web_search": {
            "on": True, "key_name": "serving-key", "cap": {"searches": 500, "through": "2026-11-01"}}}))
        self.assertEqual(web_search.web_search_setting("person"), web_search.web_search_setting())
        self.assertIsNone(connectors.files_folder("person"))
        self.assertFalse((self.runs / "person").exists())
        self.save()
        web_search.save_user_setting("person", {"on": False})
        self.assertEqual(web_search.web_search_setting("person"), web_search.web_search_setting())

    def test_user_connector_reads_keychain_not_skate(self):
        self.save()
        with mock.patch.object(web_search, "keychain_get", return_value="fake-key-for-tests") as key:
            search = web_search.connector(web_search.web_search_setting("person"))
            self.assertEqual(search.__self__.key(), "fake-key-for-tests")
            self.assertEqual(search.__self__.key(), "fake-key-for-tests")
            key.assert_called_once()

    def test_check_counts_results_and_attempts_and_respects_cap(self):
        self.save()
        with mock.patch.object(web_search.ExaWeb, "search", return_value={"results": [{}, {}]}) as search:
            self.assertEqual(web_search.check_user_search("person"), {"count": 2})
            self.assertEqual(web_search.searches_so_far("person"), 1)
            search.assert_called_once_with("Exa web search documentation")
        with mock.patch.object(web_search.ExaWeb, "search", side_effect=web_search.WebSearchError("HTTP 401")):
            with self.assertRaises(web_search.WebSearchError):
                web_search.check_user_search("person")
        self.assertEqual(web_search.searches_so_far("person"), 2)
        cap = web_search.web_search_setting("person")["cap"]
        self.assertIsNotNone(web_search.cap_refusal(cap, pending=1))
        self.assertIsNone(web_search.cap_refusal({**cap, "_world": "other"}, pending=1))

    def test_validation_precedes_keychain_write(self):
        for body in ({"on": True, "key": "fake-key-for-tests"},
                     {"on": True, "key": "fake-key-for-tests", "cap": {"searches": -1, "through": "2026-11-05"}},
                     {"on": True, "key": "fake-key-for-tests", "cap": {"searches": 2, "through": "bad"}}):
            with mock.patch.object(web_search, "keychain_set") as put:
                with self.assertRaises(ValueError):
                    web_search.save_user_setting("person", body)
                put.assert_not_called()
        self.assertFalse((self.runs / "person").exists())

    def test_apply_search_activates_user_and_leaves_unconfigured_session_unchanged(self):
        original = object()
        session = SimpleNamespace(world="person", tools=SimpleNamespace(web_search=original, web_cap=None),
                                  tool_schemas=TOOL_SCHEMAS, web_search_on=False)
        app = SimpleNamespace(session=session, lock=threading.Lock())
        web_search.apply_search(app)
        self.assertIs(session.tools.web_search, original)
        self.save()
        web_search.apply_search(app)
        self.assertTrue(session.web_search_on)
        self.assertEqual(session.tool_schemas[-1]["function"]["name"], "search_web")
        self.assertEqual(session.tools.web_cap["_world"], "person")
        web_search.save_user_setting("person", {"on": False})
        web_search.apply_search(app)
        self.assertFalse(session.web_search_on)
        self.assertIs(session.tools.web_search, original)
        self.assertIs(session.tool_schemas, TOOL_SCHEMAS)

    def test_saved_off_is_noop_and_cli_choices_survive_enable_disable(self):
        for original_on in (False, True):
            schemas = TOOL_SCHEMAS + [web_search.SEARCH_WEB_SCHEMA] if original_on else TOOL_SCHEMAS
            original_search = object() if original_on else None
            original_cap = {"searches": 7}
            session = SimpleNamespace(world="person", web_search_on=original_on, tool_schemas=schemas,
                                      tools=SimpleNamespace(web_search=original_search, web_cap=original_cap))
            app = SimpleNamespace(session=session, lock=threading.Lock())
            web_search.save_user_setting("person", {"on": False})
            web_search.apply_search(app)
            self.assertEqual(session.web_search_on, original_on)
            self.assertIs(session.tool_schemas, schemas)
            self.assertIs(session.tools.web_search, original_search)
            self.save()
            web_search.apply_search(app)
            self.assertTrue(session.web_search_on)
            web_search.save_user_setting("person", {"on": False})
            web_search.apply_search(app)
            self.assertEqual(session.web_search_on, original_on)
            self.assertIs(session.tool_schemas, schemas)
            self.assertIs(session.tools.web_search, original_search)
            self.assertIs(session.tools.web_cap, original_cap)


if __name__ == "__main__":
    unittest.main()
