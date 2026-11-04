"""Offline deterministic tests for the clipboard diary.

No real clipboard, Keychain, network, or model. The watcher thread is either
kept idle with a huge interval (manual capture() ticks) or exercised live with
a small interval and a bounded Event wait (never sleep polling).

  cd app && python3 -m unittest discover -s tests -p test_clipboard.py
"""
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import clipboard, routes  # noqa: E402

LARGE_INTERVAL = 3600  # watcher stays idle; tests tick manually via capture()
LIVE_INTERVAL = 0.01  # live-watcher tests only, with bounded Event waits
JWT = ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0."
       "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")
PEM = ("-----BEGIN RSA PRIVATE KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8A\n"
       "-----END RSA PRIVATE KEY-----")
ENTROPY_TOKEN = "xQ2v9Lm4Zp7Rt3Kw8Ns6Jd2Hf5Gb1Vc9"  # 32 chars, mixed, high entropy


class FakeReader:
    """Stand-in for read_clipboard; the test sets .value between capture() ticks."""

    def __init__(self, value=""):
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.value


class FakeKeychain:
    """Stand-in for Keychain; records saves/labels/deletes, never touches disk."""

    def __init__(self, fail_save=False):
        self.store = {}
        self.labels = {}
        self.deleted = []
        self.fail_save = fail_save

    def save(self, clip_id, value):
        if self.fail_save:
            raise RuntimeError("Your Keychain could not be updated.")
        self.store[clip_id] = value

    def label(self, clip_id, label):
        self.labels[clip_id] = label

    def delete(self, clip_id):
        self.deleted.append(clip_id)
        self.store.pop(clip_id, None)


def make_app(world="w", day=1):
    app = SimpleNamespace()
    app.session = SimpleNamespace(world=world, day=day)
    app.lock = threading.Lock()
    app.attached = []

    def attach(name, text):
        app.attached.append((name, text))
        return {"name": name, "chars": len(text)}

    app.attach = attach
    return app


def req(body=None, query=None):
    return SimpleNamespace(body=body, query=query or {})


class ClipBase(unittest.TestCase):
    def setUp(self):
        self._old_runs = os.environ.get("AFTERGLOW_RUNS")
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["AFTERGLOW_RUNS"] = self._tmp.name
        self._clips = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for c in self._clips:
            try:
                c.stop()
            except Exception:
                pass
        self._tmp.cleanup()
        if self._old_runs is None:
            os.environ.pop("AFTERGLOW_RUNS", None)
        else:
            os.environ["AFTERGLOW_RUNS"] = self._old_runs

    def runs(self):
        return Path(os.environ["AFTERGLOW_RUNS"])

    def mkclip(self, app, reader=None, keychain=None, interval=LARGE_INTERVAL,
               app_name="TestApp"):
        c = clipboard.Clipboard(
            app, reader=FakeReader() if reader is None else reader,
            keychain=FakeKeychain() if keychain is None else keychain,
            app_name=lambda name=app_name: name, interval=interval)
        self._clips.append(c)
        return c

    def clips_file(self, world="w", day=1):
        return self.runs() / world / str(day) / "clips.jsonl"

    def read_clips(self, world="w", day=1):
        f = self.clips_file(world, day)
        return [json.loads(line) for line in f.read_text().splitlines() if line.strip()]


class ClassifierTest(unittest.TestCase):
    TRUE = [
        "sk-abcdefghijklmnop1234",
        "token sk-abcdefghijklmnop1234 here",
        "ghp_AAAAAAAAAAAAAAAAAAAA",
        "github_pat_AAAAAAAAAAAAAAAAAAAA",
        "xoxb-123456789012",
        "xoxp-123456789012",
        "AKIAIOSFODNN7EXAMPLE",
        "AIzaAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        JWT,
        "bearer " + JWT,
        PEM,
        "user: bob\npassword: hunter2\n",
        "Password: secret123",
        "  PASSWORD:  x  ",
        "card 4111111111111111",
        "4242 4242 4242 4242",
        "378282246310005",
        "4111-1111-1111-1111",
        ENTROPY_TOKEN,
        "here: " + ENTROPY_TOKEN + " end",
        # Explicit key shapes still win next to a UUID or URL.
        "sk-abcdefghijklmnop1234 id 123e4567-e89b-12d3-a456-426614174000",
        # Broadened assignment detector: inline colon/equals, passwd/pwd, quotes.
        "login password: hunter2 ok",
        "password=hunter2",
        "router Password = secret123 done",
        "passwd=secret123",
        "pwd: hunter2",
        '{"user": "bob", "password": "hunter2"}',
        'password = "hunter2"',
    ]
    FALSE = [
        "Remember to buy milk and walk the dog tomorrow morning.",
        "Meeting notes: Alice will draft the plan by Friday, Bob reviews.",
        "see https://example.com/docs?page=1 for details",
        "http://localhost:8667/api/clips?day=1",
        # A long token inside a URL is not a copied key.
        "https://example.com/token/abcdefghijklmnopqrstuvwxyz123456",
        "123e4567-e89b-12d3-a456-426614174000",
        "id 123e4567-e89b-12d3-a456-426614174000 done",
        "UPPER 123E4567-E89B-12D3-A456-426614174000 UUID",
        # Short codes are out of scope, even after code/PIN/OTP words.
        "your code is 123456",
        "code 12345678",
        "PIN: 1234",
        "the PIN is 9876",
        "OTP 567890",
        "use 4321 as the code",
        "abc123",
        "1234",
        "short",
        # Low-entropy long runs are not keys.
        "a" * 32,
        "ab" * 16,
        "0" * 40,
        # Password without the "password: ..." line shape.
        "my password is hunter2",
        "password hunter2",
        # Card-shaped but failing Luhn, or all one digit.
        "4111111111111112",
        "0000000000000",
        "1234 5678",
        # Spec is 32+: a 31-char high-entropy token stays false.
        ENTROPY_TOKEN[:31],
    ]

    def test_true_positives(self):
        for text in self.TRUE:
            with self.subTest(text=text[:40]):
                self.assertTrue(clipboard.is_secret(text))

    def test_false_positives(self):
        for text in self.FALSE:
            with self.subTest(text=text[:40]):
                self.assertFalse(clipboard.is_secret(text))


class MaskTest(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(clipboard.mask("sk-abcdefghijklmnop"), "sk\u2022\u2022\u2022\u2022\u2022\u2022mnop")
        self.assertEqual(clipboard.mask(JWT), JWT[:2] + "\u2022" * 6 + JWT[-4:])

    def test_hides_middle(self):
        secret = "ghp_AAAAAAAAAAAAAAAAAAAA"
        m = clipboard.mask(secret)
        self.assertTrue(m.startswith(secret[:2]) and m.endswith(secret[-4:]))
        self.assertNotIn(secret[2:-4], m)


class DefaultOffTest(ClipBase):
    def test_default_off_no_file_no_thread(self):
        app = make_app()
        clip = self.mkclip(app)
        clip.sync()
        self.assertFalse(clip.on)
        self.assertTrue(clip.available)  # fake reader counts as available
        self.assertIsNone(clip.thread)
        self.assertFalse((self.runs() / "w" / "clipboard.json").exists())
        self.assertFalse(self.clips_file().exists())

    def test_sync_is_idempotent(self):
        app = make_app()
        clip = self.mkclip(app)
        clip.sync()
        clip.sync()
        self.assertFalse(clip.on)
        self.assertIsNone(clip.thread)

    def test_capture_while_off_saves_nothing(self):
        app = make_app()
        reader = FakeReader("hello world")
        clip = self.mkclip(app, reader=reader)
        clip.sync()
        self.assertIsNone(clip.capture())
        self.assertEqual(reader.calls, 0)  # off: the clipboard is not even read
        self.assertFalse(self.clips_file().exists())

    def test_state_shape_while_off(self):
        app = make_app()
        clip = self.mkclip(app)
        st = clip.state()
        self.assertEqual(st, {"on": False, "available": True, "count": 0,
                              "pending": [], "error": None})


class CaptureTest(ClipBase):
    def test_set_on_starts_one_daemon_thread(self):
        app = make_app()
        clip = self.mkclip(app)
        clip.sync()
        st = clip.set_on(True)
        self.assertTrue(st["on"])
        self.assertIsNotNone(clip.thread)
        self.assertTrue(clip.thread.daemon)
        self.assertTrue(clip.thread.is_alive())
        self.assertEqual(clip.thread.name, "afterglow-clipboard")
        clip.set_on(True)  # restart must not leak a second thread
        live = [t for t in threading.enumerate() if t.name == "afterglow-clipboard"]
        self.assertEqual(len(live), 1)
        clip.set_on(False)
        self.assertFalse(clip.on)
        self.assertIsNone(clip.thread)

    def test_large_interval_never_ticks_by_itself(self):
        app = make_app()
        reader = FakeReader("hello world")
        clip = self.mkclip(app, reader=reader)
        clip.set_on(True)
        self.assertEqual(reader.calls, 0)
        self.assertFalse(self.clips_file().exists())

    def test_redundant_sync_keeps_thread(self):
        app = make_app()
        clip = self.mkclip(app)
        clip.set_on(True)
        first = clip.thread
        clip.sync()
        self.assertIs(clip.thread, first)

    def test_set_on_validates(self):
        app = make_app()
        clip = self.mkclip(app)
        for bad in (None, "yes", 1):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                clip.set_on(bad)

    def test_set_on_refuses_when_unavailable(self):
        app = make_app()
        with mock.patch.object(clipboard, "clipboard_command", return_value=None):
            clip = clipboard.Clipboard(app, reader=None, keychain=FakeKeychain(),
                                       app_name=lambda: "T", interval=LARGE_INTERVAL)
            self._clips.append(clip)
            self.assertFalse(clip.available)
            with self.assertRaises(RuntimeError):
                clip.set_on(True)
            st = clip.set_on(False)  # switching off always works
            self.assertFalse(st["on"])
            self.assertIsNone(clip.thread)

    def test_text_capture_shape(self):
        app = make_app()
        reader = FakeReader("hello world")
        clip = self.mkclip(app, reader=reader)
        clip.set_on(True)
        got = clip.capture()
        self.assertEqual(got["kind"], "text")
        self.assertEqual(got["text"], "hello world")
        self.assertEqual(got["chars"], len("hello world"))
        self.assertEqual(got["app"], "TestApp")
        self.assertEqual(len(got["id"]), 32)
        int(got["id"], 16)
        self.assertRegex(got["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        rows = self.read_clips()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "hello world")

    def test_dedup_skips_unchanged(self):
        app = make_app()
        reader = FakeReader("same note")
        clip = self.mkclip(app, reader=reader)
        clip.set_on(True)
        self.assertIsNotNone(clip.capture())
        self.assertIsNone(clip.capture())
        self.assertEqual(len(self.read_clips()), 1)
        reader.value = "new note"
        self.assertIsNotNone(clip.capture())
        self.assertEqual(len(self.read_clips()), 2)

    def test_empty_and_blank_skipped(self):
        app = make_app()
        reader = FakeReader("")
        clip = self.mkclip(app, reader=reader)
        clip.set_on(True)
        self.assertIsNone(clip.capture())
        reader.value = "   \n  "
        self.assertIsNone(clip.capture())
        self.assertFalse(self.clips_file().exists())

    def test_truncate_keeps_full_chars(self):
        app = make_app()
        long_text = "ab " * 11000  # 33000 chars of plain words, not a secret
        self.assertFalse(clipboard.is_secret(long_text))
        reader = FakeReader(long_text)
        clip = self.mkclip(app, reader=reader)
        clip.set_on(True)
        got = clip.capture()
        self.assertEqual(got["kind"], "text")
        self.assertEqual(len(got["text"]), clipboard.MAX_CHARS)
        self.assertEqual(got["chars"], len(long_text))
        self.assertEqual(len(self.read_clips()[0]["text"]), clipboard.MAX_CHARS)

    def test_capture_yields_when_app_busy(self):
        app = make_app()
        reader = FakeReader("hello world")
        clip = self.mkclip(app, reader=reader)
        clip.set_on(True)
        app.lock.acquire()
        try:
            self.assertIsNone(clip.capture())
        finally:
            app.lock.release()
        self.assertFalse(self.clips_file().exists())


class SecretTest(ClipBase):
    SECRET = "sk-abcdefghijklmnop1234"

    def test_secret_masked_keychain_holds_value(self):
        app = make_app()
        reader = FakeReader(self.SECRET)
        keys = FakeKeychain()
        clip = self.mkclip(app, reader=reader, keychain=keys)
        clip.set_on(True)
        got = clip.capture()
        self.assertEqual(got["kind"], "secret")
        self.assertEqual(got["text"], clipboard.mask(self.SECRET))
        self.assertEqual(keys.store[got["id"]], self.SECRET)
        raw = self.clips_file().read_text()
        self.assertNotIn(self.SECRET, raw)
        self.assertIn(clipboard.mask(self.SECRET), raw)
        st = clip.state()
        self.assertEqual(st["count"], 1)
        self.assertEqual([c["id"] for c in st["pending"]], [got["id"]])

    def test_classify_before_truncate(self):
        # A key past the 20k limit must still land as a secret, never as text.
        app = make_app()
        filler = "note " * 5000  # 25000 plain chars
        text = filler + " " + self.SECRET
        self.assertGreater(len(text), clipboard.MAX_CHARS)
        self.assertTrue(clipboard.is_secret(text))
        reader = FakeReader(text)
        keys = FakeKeychain()
        clip = self.mkclip(app, reader=reader, keychain=keys)
        clip.set_on(True)
        got = clip.capture()
        self.assertEqual(got["kind"], "secret")
        raw = self.clips_file().read_text()
        self.assertNotIn(self.SECRET, raw)
        self.assertEqual(got["chars"], len(text))

    def test_keychain_save_failure_writes_no_json(self):
        app = make_app()
        reader = FakeReader(self.SECRET)
        clip = self.mkclip(app, reader=reader, keychain=FakeKeychain(fail_save=True))
        clip.set_on(True)
        with self.assertRaises(RuntimeError) as ctx:
            clip.capture()
        self.assertNotIn(self.SECRET, str(ctx.exception))
        self.assertFalse(self.clips_file().exists())

    def test_text_capture_needs_no_keychain(self):
        app = make_app()
        reader = FakeReader("plain note")
        keys = FakeKeychain(fail_save=True)  # must never be called for text
        clip = self.mkclip(app, reader=reader, keychain=keys)
        clip.set_on(True)
        got = clip.capture()
        self.assertEqual(got["kind"], "text")
        self.assertEqual(keys.store, {})


class RealKeychainMessageTest(unittest.TestCase):
    def test_messages_never_carry_the_secret(self):
        secret = "sk-abcdefghijklmnop1234"
        with mock.patch.object(clipboard.platform, "system", return_value="Linux"):
            with self.assertRaises(RuntimeError) as ctx:
                clipboard.Keychain().save("id1", secret)
            self.assertNotIn(secret, str(ctx.exception))
        with mock.patch.object(clipboard.platform, "system", return_value="Darwin"), \
                mock.patch.object(clipboard.shutil, "which", return_value="/usr/bin/security"), \
                mock.patch.object(clipboard.subprocess, "run", side_effect=OSError("denied")):
            with self.assertRaises(RuntimeError) as ctx:
                clipboard.Keychain().save("id1", secret)
            self.assertNotIn(secret, str(ctx.exception))
            self.assertNotIn("denied", str(ctx.exception))

    def test_delete_tolerates_missing_item(self):
        done = mock.Mock(returncode=0)
        missing = mock.Mock(returncode=44)
        failed = mock.Mock(returncode=1)
        with mock.patch.object(clipboard.platform, "system", return_value="Darwin"), \
                mock.patch.object(clipboard.shutil, "which", return_value="/usr/bin/security"):
            with mock.patch.object(clipboard.subprocess, "run", return_value=missing):
                clipboard.Keychain().delete("gone")  # no raise
            with mock.patch.object(clipboard.subprocess, "run", return_value=done):
                clipboard.Keychain().delete("gone")
            with mock.patch.object(clipboard.subprocess, "run", return_value=failed):
                with self.assertRaises(RuntimeError):
                    clipboard.Keychain().delete("gone")


class LabelDeleteAttachTest(ClipBase):
    def _on_with_secret(self, secret="sk-abcdefghijklmnop1234"):
        app = make_app()
        reader = FakeReader(secret)
        keys = FakeKeychain()
        clip = self.mkclip(app, reader=reader, keychain=keys)
        clip.set_on(True)
        got = clip.capture()
        return app, clip, keys, got

    def test_label_secret(self):
        app, clip, keys, got = self._on_with_secret()
        self.assertEqual(clip.change("label", got["id"], "router admin"), {"ok": True})
        self.assertEqual(keys.labels[got["id"]], "router admin")
        rows = self.read_clips()
        self.assertEqual(rows[0]["label"], "router admin")
        self.assertEqual(clip.state()["pending"], [])

    def test_label_accepts_240_chars(self):
        app, clip, keys, got = self._on_with_secret()
        clip.change("label", got["id"], "l" * 240)
        self.assertEqual(self.read_clips()[0]["label"], "l" * 240)

    def test_label_refuses(self):
        app, clip, keys, got = self._on_with_secret()
        for bad in ("", "   ", "l" * 241, None, 123):
            with self.subTest(bad=repr(bad)[:20]), self.assertRaises(ValueError):
                clip.change("label", got["id"], bad)
        with self.assertRaises(ValueError):  # the label must not be the key itself
            clip.change("label", got["id"], "sk-abcdefghijklmnop1234")
        self.assertNotIn(got["id"], keys.labels)
        with self.assertRaises(ValueError):  # unknown id
            clip.change("label", "nope", "x")
        # Plain clips need no label.
        clip2_app_reader = FakeReader("plain")
        app2 = make_app(world="w2")
        clip2 = self.mkclip(app2, reader=clip2_app_reader)
        clip2.set_on(True)
        text_clip = clip2.capture()
        with self.assertRaises(ValueError):
            clip2.change("label", text_clip["id"], "x")

    def test_delete_text_and_secret(self):
        app = make_app()
        reader = FakeReader("plain note")
        keys = FakeKeychain()
        clip = self.mkclip(app, reader=reader, keychain=keys)
        clip.set_on(True)
        text_clip = clip.capture()
        reader.value = "sk-abcdefghijklmnop1234"
        secret_clip = clip.capture()
        self.assertEqual(clip.change("delete", text_clip["id"]), {"ok": True})
        self.assertEqual([c["id"] for c in self.read_clips()], [secret_clip["id"]])
        self.assertEqual(keys.deleted, [])
        self.assertEqual(clip.change("delete", secret_clip["id"]), {"ok": True})
        self.assertEqual(self.read_clips(), [])
        self.assertIn(secret_clip["id"], keys.deleted)
        self.assertNotIn(secret_clip["id"], keys.store)
        with self.assertRaises(ValueError):
            clip.change("delete", "nope")

    def test_attach_text_ok(self):
        app = make_app()
        reader = FakeReader("send me to chat")
        clip = self.mkclip(app, reader=reader)
        clip.set_on(True)
        got = clip.capture()
        out = clip.change("attach", got["id"])
        self.assertTrue(out["ok"])
        self.assertEqual(app.attached, [(f"copy-{got['id'][:8]}.txt", "send me to chat")])

    def test_attach_refuses_secret(self):
        app, clip, keys, got = self._on_with_secret()
        with self.assertRaises(RuntimeError):
            clip.change("attach", got["id"])
        self.assertEqual(app.attached, [])

    def test_attach_refuses_secret_shaped_text(self):
        # Belt and braces: even a text-kind row carrying a key cannot attach.
        app = make_app()
        clip = self.mkclip(app)
        clip.set_on(True)
        f = self.clips_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"id": "abc123", "ts": "2026-01-01T00:00:00+0000",
                                 "kind": "text", "text": "sk-abcdefghijklmnop1234",
                                 "chars": 23, "app": "T"}) + "\n")
        with self.assertRaises(RuntimeError):
            clip.change("attach", "abc123")
        self.assertEqual(app.attached, [])

    def test_unknown_action(self):
        app, clip, keys, got = self._on_with_secret()
        with self.assertRaises(ValueError):
            clip.change("rename", got["id"])


class DayTransitionTest(ClipBase):
    def test_day_switch_keeps_switch_moves_file(self):
        app = make_app(world="w", day=1)
        reader = FakeReader("day one note")
        clip = self.mkclip(app, reader=reader)
        clip.set_on(True)
        first_thread = clip.thread
        clip.capture()
        app.session = SimpleNamespace(world="w", day=2)
        clip.sync()
        self.assertTrue(clip.on)  # the saved switch carries over
        self.assertIsNot(clip.thread, first_thread)
        self.assertTrue(clip.thread.is_alive())
        reader.value = "day two note"
        clip.capture()
        self.assertEqual([c["text"] for c in self.read_clips(day=1)], ["day one note"])
        self.assertEqual([c["text"] for c in self.read_clips(day=2)], ["day two note"])
        self.assertEqual([c["text"] for c in clip.listing()], ["day two note"])
        self.assertEqual([c["text"] for c in clip.listing(day=1)], ["day one note"])
        self.assertEqual([c["text"] for c in clip.listing(day="1")], ["day one note"])
        self.assertEqual(clip.listing(day=9), [])
        self.assertEqual(clip.state()["count"], 1)

    def test_person_switch_honours_each_saved_switch(self):
        app = make_app(world="w1", day=1)
        clip = self.mkclip(app)
        clip.set_on(True)
        app.session = SimpleNamespace(world="w2", day=1)
        clip.sync()
        self.assertFalse(clip.on)
        self.assertIsNone(clip.thread)
        self.assertFalse((self.runs() / "w2" / "clipboard.json").exists())
        clip.set_on(True)
        app.session = SimpleNamespace(world="w1", day=1)
        clip.sync()
        self.assertTrue(clip.on)
        self.assertTrue(clip.thread.is_alive())
        app.session = SimpleNamespace(world="w2", day=1)
        clip.sync()
        self.assertTrue(clip.on)
        self.assertTrue(clip.thread.is_alive())

    def test_off_stays_off_across_days(self):
        app = make_app(world="w", day=1)
        clip = self.mkclip(app)
        clip.sync()
        app.session = SimpleNamespace(world="w", day=2)
        clip.sync()
        self.assertFalse(clip.on)
        self.assertIsNone(clip.thread)
        self.assertFalse(self.clips_file(day=2).exists())

    def test_listing_validates_day(self):
        app = make_app()
        clip = self.mkclip(app)
        clip.set_on(True)
        for bad in ("nope", "0", "-1", "1.5", 0):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                clip.listing(day=bad)


class PathsRoutesTest(ClipBase):
    def test_file_layout_and_switch_shape(self):
        app = make_app(world="w", day=3)
        reader = FakeReader("hello")
        clip = self.mkclip(app, reader=reader)
        clip.set_on(True)
        clip.capture()
        switch = self.runs() / "w" / "clipboard.json"
        self.assertEqual(json.loads(switch.read_text()), {"on": True})
        rows = self.read_clips(world="w", day=3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]) >= {"id", "ts", "kind", "text", "chars", "app"}, True)
        self.assertEqual(list(self.runs().rglob("*.tmp")), [])  # atomic writes leave no tmp
        clip.set_on(False)
        self.assertEqual(json.loads(switch.read_text()), {"on": False})

    def test_routes_table(self):
        self.assertIn("clipboard", routes.FEATURES)
        self.assertEqual(set(clipboard.ROUTES), {
            ("GET", "/api/clipboard"), ("POST", "/api/clipboard"),
            ("GET", "/api/clips"), ("POST", "/api/clips/label"),
            ("POST", "/api/clips/delete"), ("POST", "/api/clips/attach")})
        self.assertIs(clipboard.ROUTES[("GET", "/api/clipboard")], clipboard.state)
        self.assertIs(clipboard.ROUTES[("POST", "/api/clipboard")], clipboard.toggle)
        self.assertIs(clipboard.ROUTES[("GET", "/api/clips")], clipboard.clips)
        self.assertIs(clipboard.ROUTES[("POST", "/api/clips/label")], clipboard.label)
        self.assertIs(clipboard.ROUTES[("POST", "/api/clips/delete")], clipboard.delete)
        self.assertIs(clipboard.ROUTES[("POST", "/api/clips/attach")], clipboard.attach)

    def _routed(self, value="hello"):
        app = make_app()
        reader = FakeReader(value)
        keys = FakeKeychain()
        clip = self.mkclip(app, reader=reader, keychain=keys)
        app._clipboard = clip  # connector reuses the injected doubles
        return app, clip, reader, keys

    def test_state_toggle_clips_routes(self):
        app, clip, reader, keys = self._routed()
        st = clipboard.state(app, req())
        self.assertEqual((st["on"], st["count"]), (False, 0))
        out = clipboard.toggle(app, req(body={"on": True}))
        self.assertTrue(out["on"])
        clip.capture()
        self.assertEqual(len(clipboard.clips(app, req())["clips"]), 1)
        self.assertEqual(len(clipboard.clips(app, req(query={"day": "1"}))["clips"]), 1)
        with self.assertRaises(ValueError):
            clipboard.clips(app, req(query={"day": "nope"}))
        with self.assertRaises(ValueError):
            clipboard.toggle(app, req(body={"on": "yes"}))
        with self.assertRaises(ValueError):
            clipboard.toggle(app, req(body={}))

    def test_label_delete_attach_routes(self):
        app, clip, reader, keys = self._routed("sk-abcdefghijklmnop1234")
        clipboard.toggle(app, req(body={"on": True}))
        secret = clip.capture()
        reader.value = "plain note"
        text = clip.capture()
        self.assertEqual(clipboard.label(
            app, req(body={"id": secret["id"], "label": "router"})), {"ok": True})
        self.assertEqual(keys.labels[secret["id"]], "router")
        with self.assertRaises(RuntimeError):
            clipboard.attach(app, req(body={"id": secret["id"]}))
        out = clipboard.attach(app, req(body={"id": text["id"]}))
        self.assertTrue(out["ok"])
        self.assertEqual(app.attached[0][1], "plain note")
        self.assertEqual(clipboard.delete(app, req(body={"id": text["id"]})), {"ok": True})
        self.assertEqual(clipboard.delete(app, req(body={"id": secret["id"]})), {"ok": True})
        self.assertEqual(self.read_clips(), [])
        self.assertIn(secret["id"], keys.deleted)


class ConnectorTest(ClipBase):
    def test_lazy_singleton_without_hooks(self):
        app = make_app()
        first = clipboard.connector(app)
        self._clips.append(first)
        self.assertIsInstance(first, clipboard.Clipboard)
        self.assertIs(clipboard.connector(app), first)
        self.assertFalse(first.on)
        self.assertIsNone(first.thread)

    def test_binds_day_and_profile_switches(self):
        app = make_app(world="w", day=1)
        opened = []

        def open_day(day):
            opened.append(("day", day))
            app.session = SimpleNamespace(world="w", day=day)
            return "day"

        def create_profile(world):
            opened.append(("profile", world))
            app.session = SimpleNamespace(world=world, day=1)
            return "profile"

        app.open_day = open_day
        app.create_profile = create_profile
        conn = clipboard.connector(app)
        self._clips.append(conn)
        wrapped_day, wrapped_profile = app.open_day, app.create_profile
        self.assertIsNot(wrapped_day, open_day)
        self.assertIsNot(wrapped_profile, create_profile)
        self.assertIs(clipboard.connector(app), conn)  # no double wrap
        self.assertIs(app.open_day, wrapped_day)
        self.assertIs(app.create_profile, wrapped_profile)
        self.assertEqual(app.open_day(2), "day")
        self.assertIs(conn.session, app.session)
        self.assertEqual(app.create_profile("w2"), "profile")
        self.assertIs(conn.session, app.session)
        self.assertEqual(opened, [("day", 2), ("profile", "w2")])


class LiveWatcherTest(ClipBase):
    def test_watcher_saves_copies(self):
        app = make_app()
        fired = threading.Event()

        def reader():
            fired.set()
            return "live hello world"

        keys = FakeKeychain()
        clip = clipboard.Clipboard(app, reader=reader, keychain=keys,
                                   app_name=lambda: "Live", interval=LIVE_INTERVAL)
        self._clips.append(clip)
        clip.set_on(True)
        self.assertTrue(fired.wait(timeout=5), "watcher never ticked")
        clip.stop()  # join: the in-flight tick fully lands before we read
        rows = self.read_clips()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["text"], rows[0]["app"]), ("live hello world", "Live"))

    def test_watcher_error_stays_generic(self):
        app = make_app()
        fired = threading.Event()

        def reader():
            fired.set()
            raise RuntimeError("clipboard denied: sk-live-secret")

        clip = clipboard.Clipboard(app, reader=reader, keychain=FakeKeychain(),
                                   app_name=lambda: "Live", interval=LIVE_INTERVAL)
        self._clips.append(clip)
        clip.set_on(True)
        self.assertTrue(fired.wait(timeout=5), "watcher never ticked")
        clip.stop()
        self.assertEqual(clip.error,
                         "A copy could not be saved. Check clipboard access and your Keychain.")
        self.assertNotIn("sk-live-secret", clip.error or "")
        self.assertFalse(self.clips_file().exists())


class CaptureLockTest(ClipBase):
    """Slow OS calls must never hold the foreground chat's app.lock; a day
    change or stop during IO aborts the write and rolls back Keychain saves."""

    SECRET = "sk-abcdefghijklmnop1234"

    def test_slow_io_never_holds_app_lock(self):
        app = make_app()
        probes = []

        def check(where):
            free = app.lock.acquire(blocking=False)
            probes.append((where, free))
            if free:
                app.lock.release()
            return free

        def reader():
            assert check("reader"), "app.lock held during clipboard read"
            return self.SECRET

        keys = FakeKeychain()
        real_save = keys.save

        def save(clip_id, value):
            assert check("keychain"), "app.lock held during Keychain save"
            return real_save(clip_id, value)

        keys.save = save

        def app_name():
            assert check("app_name"), "app.lock held during front-app lookup"
            return "TestApp"

        clip = clipboard.Clipboard(app, reader=reader, keychain=keys,
                                   app_name=app_name, interval=LARGE_INTERVAL)
        self._clips.append(clip)
        clip.set_on(True)
        got = clip.capture()
        self.assertIsNotNone(got)
        self.assertEqual(got["kind"], "secret")
        self.assertEqual(sorted(probes),
                         [("app_name", True), ("keychain", True), ("reader", True)])
        self.assertEqual(len(self.read_clips()), 1)

    def test_mid_read_day_flip_writes_nowhere(self):
        app = make_app(world="w", day=1)

        def reader():
            app.session = SimpleNamespace(world="w", day=2)
            return "hello world"

        clip = self.mkclip(app, reader=reader)
        clip.set_on(True)
        clip.capture()
        self.assertFalse(self.clips_file(day=1).exists())
        self.assertFalse(self.clips_file(day=2).exists())

    def test_mid_read_day_flip_rolls_back_keychain_secret(self):
        app = make_app(world="w", day=1)

        def reader():
            app.session = SimpleNamespace(world="w", day=2)
            return self.SECRET

        keys = FakeKeychain()
        saved = []
        real_save = keys.save

        def save(clip_id, value):
            saved.append(clip_id)
            return real_save(clip_id, value)

        keys.save = save
        clip = self.mkclip(app, reader=reader, keychain=keys)
        clip.set_on(True)
        clip.capture()
        self.assertFalse(self.clips_file(day=1).exists())
        self.assertFalse(self.clips_file(day=2).exists())
        self.assertEqual(len(saved), 1)
        self.assertNotIn(saved[0], keys.store)
        self.assertEqual(keys.deleted, saved)

    def test_stop_during_io_rolls_back_keychain_secret(self):
        app = make_app()
        holder = {}

        def reader():
            holder["clip"].stop_event.set()
            return self.SECRET

        keys = FakeKeychain()
        clip = self.mkclip(app, reader=reader, keychain=keys)
        holder["clip"] = clip
        clip.set_on(True)
        clip.capture()
        self.assertFalse(self.clips_file().exists())
        self.assertEqual(keys.store, {})
        self.assertEqual(len(keys.deleted), 1)


if __name__ == "__main__":
    unittest.main()
