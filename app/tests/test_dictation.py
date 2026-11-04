"""Dictation conversion, engines, cleanup, routing, settings, and thread isolation."""
import base64
import io
import json
import math
import os
import struct
import sys
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import dictation, routes, threads  # noqa: E402


# ---------- helpers ----------

def make_wav_bytes(duration_s=1.0, framerate=44100, nchannels=1, freq=440.0, silence=False):
    nframes = int(duration_s * framerate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(nchannels)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        if silence:
            wf.writeframes(b"\x00\x00" * nframes * nchannels)
        else:
            wf.writeframes(b"".join(
                struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / framerate)))
                for i in range(nframes) for _ in range(nchannels)))
    return buf.getvalue()

def make_silence_wav_bytes(duration_s, framerate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(b"\x00\x00" * int(duration_s * framerate))
    return buf.getvalue()

def write_wav_file(path, duration_s=0.5, framerate=16000, silence=False, freq=440.0):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(make_wav_bytes(duration_s, framerate, 1, freq, silence=silence))
    return path

def ffmpeg_available():
    p = getattr(dictation, "FFMPEG", "/opt/homebrew/bin/ffmpeg")
    return isinstance(p, str) and os.path.isfile(p) and os.access(p, os.X_OK)

class FakeClient:
    def __init__(self, answer="Cleaned.", fail=False, extra=None):
        self.answer = answer
        self.fail = fail
        self.extra = dict(extra or {})
        self.calls = []

    def chat(self, messages, *args, **kw):
        self.calls.append({"messages": messages, "args": args, "kw": kw})
        if self.fail:
            raise self.fail if isinstance(self.fail, Exception) else RuntimeError("model down")
        return {"message": {"content": self.answer}, **self.extra}

class FakeSession:
    def __init__(self, world="w-test", day=1, turn=7):
        self.world, self.day, self.turn = world, int(day), turn

class FakeApp:
    def __init__(self, client=None, world="w-test", day=1, turn=7, lock=None):
        self.session = FakeSession(world, day, turn)
        self.client = client if client is not None else FakeClient()
        self.lock = lock if lock is not None else threading.Lock()

def dictation_request(raw, mime="audio/webm", context="composer"):
    return routes.Request("POST", "/api/dictation", f"context={context}", {"Content-Type": mime}, raw)

def ambient_request(doc):
    return routes.Request("POST", "/api/ambient", "", {"Content-Type": "application/json"},
                            json.dumps(doc).encode())

def settings_post_request(doc):
    return routes.Request("POST", "/api/dictation/settings", "", {"Content-Type": "application/json"},
                            json.dumps(doc).encode())

def settings_get_request():
    return routes.Request("GET", "/api/dictation/settings", "", {}, None)

class RunsMixin:
    def setUp(self):
        self._old_runs = os.environ.get("AFTERGLOW_RUNS")
        self._last_engine = dictation._last_engine
        dictation._last_engine = None
        self._temp_runs = tempfile.TemporaryDirectory(prefix="afterglow-dictation-test-")
        self.runs = Path(self._temp_runs.name)
        os.environ["AFTERGLOW_RUNS"] = str(self.runs)

    def tearDown(self):
        if self._old_runs is None:
            os.environ.pop("AFTERGLOW_RUNS", None)
        else:
            os.environ["AFTERGLOW_RUNS"] = self._old_runs
        self._temp_runs.cleanup()
        dictation._last_engine = self._last_engine

    def daylog(self, world="w-test", day=1):
        return self.runs / world / str(day) / "daylog.jsonl"

    def threads_index(self, world="w-test", day=1):
        return self.runs / world / str(day) / "threads" / "index.json"


# ---------- convert_audio ----------

class ConvertAudioTest(RunsMixin, unittest.TestCase):
    def test_sine_wav_converts_to_16k_mono_and_reports_duration(self):
        if not ffmpeg_available():
            self.skipTest("ffmpeg binary missing")
        raw = make_wav_bytes(duration_s=1.0, framerate=44100, freq=440.0)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.wav"
            seconds = dictation.convert_audio(raw, "audio/wav", out)
            self.assertAlmostEqual(seconds, 1.0, delta=0.05)
            with wave.open(str(out), "rb") as wf:
                self.assertEqual((wf.getframerate(), wf.getnchannels(), wf.getsampwidth()), (16000, 1, 2))

    def test_ffmpeg_command_line_is_16k_mono_with_61s_cap(self):
        seen = {}
        def fake_run(cmd, input=None, capture_output=None, timeout=None):
            seen.update(cmd=list(cmd), input=input, capture_output=capture_output, timeout=timeout)
            write_wav_file(cmd[-1], duration_s=0.5)
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")
        with mock.patch.object(dictation.subprocess, "run", side_effect=fake_run):
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / "o.wav"
                self.assertAlmostEqual(dictation.convert_audio(b"raw", "audio/webm", out), 0.5, delta=0.05)
        cmd = seen["cmd"]
        self.assertEqual(cmd[0], dictation.FFMPEG)
        for flag in ("-hide_banner", "-loglevel", "error", "-y", "-i", "pipe:0", "-t", "61",
                       "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[-1], str(out))
        self.assertEqual((seen["input"], seen["capture_output"], seen["timeout"]), (b"raw", True, 30))

    def test_rejects_unsupported_mime_without_calling_ffmpeg(self):
        with mock.patch.object(dictation.subprocess, "run", side_effect=AssertionError("no ffmpeg")):
            with tempfile.TemporaryDirectory() as d:
                for bad in ("", "text/plain", "audio/mpeg", "audio/ogg", None):
                    with self.assertRaises(ValueError, msg=f"mime={bad!r}"):
                        dictation.convert_audio(b"xxx", bad, Path(d) / "o.wav")

    def test_rejects_empty_and_oversize_input(self):
        with tempfile.TemporaryDirectory() as d:
            for bad in (b"", None):
                with self.assertRaises(ValueError):
                    dictation.convert_audio(bad, "audio/wav", Path(d) / "o.wav")
        with mock.patch.object(dictation, "MAX_BYTES", 10):
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(ValueError):
                    dictation.convert_audio(b"x" * 11, "audio/wav", Path(d) / "o.wav")

    def test_duration_gate_rejects_over_60s(self):
        def fake_run(cmd, input=None, capture_output=None, timeout=None):
            Path(cmd[-1]).write_bytes(make_silence_wav_bytes(61.0))
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")
        with mock.patch.object(dictation.subprocess, "run", side_effect=fake_run):
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaisesRegex(ValueError, "60"):
                    dictation.convert_audio(b"x", "audio/wav", Path(d) / "o.wav")

    def test_real_61s_wav_is_rejected(self):
        if not ffmpeg_available():
            self.skipTest("ffmpeg binary missing")
        raw = make_silence_wav_bytes(61.0)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, "60"):
                dictation.convert_audio(raw, "audio/wav", Path(d) / "o.wav")

    def test_ffmpeg_failures_map_to_plain_errors(self):
        import subprocess as sp
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.wav"
            with mock.patch.object(dictation.subprocess, "run", side_effect=FileNotFoundError("x")):
                with self.assertRaisesRegex(RuntimeError, "ffmpeg"):
                    dictation.convert_audio(b"x", "audio/wav", out)
            with mock.patch.object(dictation.subprocess, "run",
                                   side_effect=sp.TimeoutExpired(cmd="ffmpeg", timeout=30)):
                with self.assertRaises(ValueError):
                    dictation.convert_audio(b"x", "audio/wav", out)
            with mock.patch.object(dictation.subprocess, "run",
                                   return_value=mock.Mock(returncode=1, stdout=b"", stderr=b"bad")):
                with self.assertRaisesRegex(ValueError, "could not be read"):
                    dictation.convert_audio(b"not-audio", "audio/wav", out)


# ---------- WhisperCpp ----------

class WhisperCppTest(RunsMixin, unittest.TestCase):
    def test_local_server_is_used_instead_of_cli(self):
        with tempfile.TemporaryDirectory() as d:
            wav = write_wav_file(Path(d) / "a.wav", duration_s=0.2)
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = b'{"text":"Hello."}'
            with mock.patch.object(dictation._whisper, "ensure_started", return_value="http://127.0.0.1:9876") as start, \
                 mock.patch.object(dictation.urllib.request, "urlopen", return_value=response) as post:
                engine = dictation.WhisperCpp()
                self.assertEqual(engine.transcribe(wav), "Hello.")
                start.assert_called_once_with(dictation.DEFAULT_MODEL)
                self.assertEqual(post.call_args.args[0].full_url, "http://127.0.0.1:9876/inference")

    def test_failed_local_request_stops_server_for_next_call(self):
        with tempfile.TemporaryDirectory() as d:
            wav = write_wav_file(Path(d) / "a.wav", duration_s=0.2)
            with mock.patch.object(dictation._whisper, "ensure_started", return_value="http://127.0.0.1:9876"), \
                 mock.patch.object(dictation._whisper, "stop") as stop, \
                 mock.patch.object(dictation.urllib.request, "urlopen", side_effect=TimeoutError):
                with self.assertRaisesRegex(RuntimeError, "whisper server"):
                    dictation.WhisperCpp().transcribe(wav)
                stop.assert_called_once()

    def test_server_url_posts_multipart_and_skips_cli(self):
        with tempfile.TemporaryDirectory() as d:
            wav = write_wav_file(Path(d) / "a.wav", duration_s=0.3)
            wav_bytes = wav.read_bytes()
            seen = {}
            class Resp:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return json.dumps({"text": "  hello  "}).encode()
            def fake_urlopen(req, timeout=None):
                seen["url"] = req.full_url
                seen["ctype"] = req.headers.get("Content-type") or req.headers.get("Content-Type")
                seen["data"] = req.data
                seen["timeout"] = timeout
                return Resp()
            with mock.patch.object(dictation.subprocess, "run", side_effect=AssertionError("no CLI")), \
                 mock.patch.object(dictation.urllib.request, "urlopen", side_effect=fake_urlopen):
                out = dictation.WhisperCpp(model="m", server_url="http://127.0.0.1:8080/").transcribe(str(wav))
            self.assertEqual(out, "hello")
            self.assertEqual(seen["url"], "http://127.0.0.1:8080/inference")
            self.assertIn("multipart/form-data; boundary=", seen["ctype"])
            self.assertIn(wav_bytes, seen["data"])
            self.assertIn(b'name="response_format"', seen["data"])
            self.assertIn(b"auto", seen["data"])
            self.assertEqual(seen["timeout"], 120)

    def test_server_language_and_failures(self):
        with tempfile.TemporaryDirectory() as d:
            wav = write_wav_file(Path(d) / "a.wav", duration_s=0.2)
            seen = {}
            class Resp:
                def __init__(self, payload): self.payload = payload
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return self.payload
            def fake_urlopen(req, timeout=None):
                seen["data"] = req.data
                return Resp(json.dumps({"text": "x"}).encode())
            with mock.patch.object(dictation.urllib.request, "urlopen", side_effect=fake_urlopen):
                dictation.WhisperCpp(model="m", server_url="http://h").transcribe(str(wav), language="ja")
            self.assertIn(b"\r\nja\r\n", seen["data"])
            for exc in (OSError("down"), ValueError("bad")):
                with mock.patch.object(dictation.urllib.request, "urlopen", side_effect=exc):
                    with self.assertRaisesRegex(RuntimeError, "whisper server"):
                        dictation.WhisperCpp(model="m", server_url="http://h").transcribe(str(wav))

    def test_qwen_delegates_to_persistent_worker(self):
        with mock.patch.object(dictation._qwen, "transcribe", return_value="Hello.") as transcribe:
            self.assertEqual(dictation.Qwen3ASR().transcribe("a.wav", "Japanese"), "Hello.")
            transcribe.assert_called_once_with("a.wav", "Japanese")


# ---------- cleanup ----------

class CleanupTest(RunsMixin, unittest.TestCase):
    def test_skip_only_clean_text_without_custom_rules(self):
        for text in ("Hello, Mira.", "What time is it?", "I will call tomorrow. We can talk then.", "明日電話します。", "今日は晴れです！", ""):
            self.assertTrue(dictation.clean_can_skip(text), text)
            app = FakeApp(FakeClient(fail=AssertionError("no model")))
            self.assertEqual(dictation.cleanup(app, text, ""), text)
            self.assertEqual(app.client.calls, [])
        for text in ("hello.", "Hello", "Um, hello.", "Uh, call Mira.", "Hello. we should call.",
                     "Hello, i am home.", "Scratch that. Call Mira.", "えっと、明日です。", "あの、明日です。", "The the dog."):
            self.assertFalse(dictation.clean_can_skip(text), text)
        self.assertFalse(dictation.clean_can_skip("Hello.", "Always formal."))

    def test_cleanup_budget_is_capped(self):
        app = FakeApp()
        dictation.cleanup(app, "word " * 1000, "")
        self.assertEqual(app.client.calls[0]["kw"]["max_tokens"], dictation.CLEAN_MAX_TOKENS)

    def test_prompt_rules_and_budget(self):
        app = FakeApp(FakeClient(answer="  Cleaned.  "))
        text = "um hello scratch that hi there"
        out = dictation.cleanup(app, text, "Always formal.")
        self.assertEqual(out, "Cleaned.")
        call = app.client.calls[0]
        self.assertEqual((call["kw"]["reasoning"], call["kw"]["temperature"]), ("minimal", 0.2))
        self.assertEqual(call["kw"]["max_tokens"], 96 + max(32, len(text.split()) * 2))
        lowered = call["messages"][0]["content"].lower()
        for phrase in ("punctuation", "casing", "um", "uh", "scratch that", "no, i mean",
                       "never answer", "return only"):
            self.assertIn(phrase, lowered)
        self.assertIn("Always formal.", call["messages"][0]["content"])
        self.assertEqual(call["messages"][1]["content"], text)

    def test_empty_skips_model_and_failure_returns_raw(self):
        app = FakeApp(FakeClient(answer="unused"))
        self.assertEqual(dictation.cleanup(app, "   ", "r"), "")
        self.assertEqual(app.client.calls, [])
        for fail in (RuntimeError("x"), ValueError("x")):
            app = FakeApp(FakeClient(fail=fail))
            self.assertEqual(dictation.cleanup(app, "keep me", "r"), "keep me")
        app = FakeApp(FakeClient(answer="   "))
        self.assertEqual(dictation.cleanup(app, "raw", ""), "raw")

    def test_japanese_single_word_uses_character_budget(self):
        app = FakeApp(FakeClient(answer="x"))
        ja = "あ" * 50  # one whitespace-delimited "word"
        dictation.cleanup(app, ja, "")
        self.assertEqual(app.client.calls[0]["kw"]["max_tokens"], 146)
        dictation.cleanup(app, "two words", "")
        self.assertEqual(app.client.calls[1]["kw"]["max_tokens"], 128)
        long_text = " ".join(f"w{i}" for i in range(50))
        dictation.cleanup(app, long_text, "")
        self.assertEqual(app.client.calls[2]["kw"]["max_tokens"], 196)

    def test_length_finish_returns_raw_without_retry(self):
        app = FakeApp(FakeClient(answer="truncated...", extra={"finish_reason": "length"}))
        self.assertEqual(dictation.cleanup(app, "a long take here", ""), "a long take here")
        self.assertEqual(len(app.client.calls), 1)

    def test_holds_app_lock(self):
        lock = mock.MagicMock()
        lock.__enter__ = mock.Mock(return_value=None)
        lock.__exit__ = mock.Mock(return_value=False)
        dictation.cleanup(FakeApp(FakeClient(answer="x"), lock=lock), "hello", "")
        lock.__enter__.assert_called_once()


# ---------- wants_explanation ----------

class WantsExplanationTest(RunsMixin, unittest.TestCase):
    def test_explain_prefix_empty_and_no_attachment(self):
        no_model = FakeClient(fail=AssertionError("no model"))
        for text, attached, expect in (("explain this", True, True), ("Explain x", False, True),
                                       ("", True, False), ("   ", False, False),
                                       ("what is this?", False, False), ("why x?", False, False)):
            self.assertEqual(dictation.wants_explanation(FakeApp(no_model), text, attached), expect, text)
        self.assertEqual(no_model.calls, [])

    def test_attached_keywords_explain_without_model(self):
        no_model = FakeClient(fail=AssertionError("no model"))
        for text in ("what is this", "what's this", "what\u2019s this", "how does this work",
                       "why is this red", "show me more", "is this ok?", "settings?"):
            app = FakeApp(no_model)
            self.assertTrue(dictation.wants_explanation(app, text, True), text)
        self.assertEqual(no_model.calls, [])

    def test_obvious_dictation_starts_are_paste(self):
        no_model = FakeClient(fail=AssertionError("no model"))
        for text in ("remind me to call", "Remember to buy", "note that x", "Dear Anna", "I am home",
                       "i have cats", "I will call", "i went there", "We have a plan", "we will ship"):
            self.assertFalse(dictation.wants_explanation(FakeApp(no_model), text, True), text)
        self.assertEqual(no_model.calls, [])

    def test_ambiguous_gets_one_tiny_call_and_yes_no_table(self):
        for text, answer, expect in (("tell me about this", "yes", True),
                                     ("tell me about this", "no", False),
                                     ("tell me about this", "Yes.", True),
                                     ("summarize the selection", "yes", True),
                                     ("summarize the selection", "no", False)):
            app = FakeApp(FakeClient(answer=answer))
            self.assertEqual(dictation.wants_explanation(app, text, True), expect, f"{text}/{answer}")
            self.assertEqual(len(app.client.calls), 1)
            kw = app.client.calls[0]["kw"]
            self.assertEqual((kw["reasoning"], kw["max_tokens"], kw["temperature"]), ("minimal", 3, 0.2))

    def test_ambiguous_failure_is_paste(self):
        self.assertFalse(dictation.wants_explanation(FakeApp(FakeClient(fail=RuntimeError("x"))),
                                                   "tell me about this", True))


# ---------- transcribe_audio ----------

class TranscribeAudioTest(RunsMixin, unittest.TestCase):
    def test_shape_and_silence_skips_asr_cleanup_and_cleans_tempdir(self):
        app = FakeApp()
        captured = {}
        def tone_convert(raw, mime, wav):
            write_wav_file(wav, duration_s=0.3)
            return 0.3
        with mock.patch.object(dictation, "convert_audio", side_effect=tone_convert), \
             mock.patch.object(dictation.Qwen3ASR, "transcribe", return_value="hello") as eng, \
             mock.patch.object(dictation, "cleanup", return_value="Hello.") as cl:
            out = dictation.transcribe_audio(app, b"a", "audio/webm")
        self.assertEqual((out["raw"], out["clean"], out["engine"]), ("hello", "Hello.", "qwen3"))
        self.assertIn("asr", out["ms"])
        self.assertIn("clean", out["ms"])
        eng.assert_called_once()
        cl.assert_called_once()
        def silent_convert(raw, mime, wav):
            captured["dir"] = str(Path(wav).parent)
            write_wav_file(wav, duration_s=0.3, silence=True)
            return 0.3
        with mock.patch.object(dictation, "convert_audio", side_effect=silent_convert), \
             mock.patch.object(dictation.Qwen3ASR, "transcribe", side_effect=AssertionError("no ASR")), \
             mock.patch.object(dictation, "cleanup", side_effect=AssertionError("no cleanup")):
            out = dictation.transcribe_audio(app, b"a", "audio/wav")
        self.assertEqual((out["raw"], out["clean"]), ("", ""))
        self.assertFalse(Path(captured["dir"]).exists(), "temp dir must be removed")

    def test_blank_markers_and_silent_gate(self):
        for marker in ("[BLANK_AUDIO]", "[silence]", "(silence)", "[no speech]"):
            with mock.patch.object(dictation, "convert_audio",
                                   side_effect=lambda r, m, wv: (write_wav_file(wv, duration_s=0.2), 0.2)[1]), \
                 mock.patch.object(dictation.Qwen3ASR, "transcribe", return_value=marker), \
                 mock.patch.object(dictation, "cleanup", side_effect=AssertionError("no cleanup")):
                out = dictation.transcribe_audio(FakeApp(), b"a", "audio/webm")
            self.assertEqual((out["raw"], out["clean"]), ("", ""), marker)
        with tempfile.TemporaryDirectory() as d:
            tone = write_wav_file(Path(d) / "t.wav", duration_s=0.2)
            silent = write_wav_file(Path(d) / "s.wav", duration_s=0.2, silence=True)
            self.assertFalse(dictation._silent(str(tone)))
            self.assertTrue(dictation._silent(str(silent)))


    def test_clean_text_skips_model_and_reports_it(self):
        with mock.patch.object(dictation, "convert_audio", side_effect=lambda r, m, w: write_wav_file(w)), \
             mock.patch.object(dictation.Qwen3ASR, "transcribe", return_value="Hello, Mira."), \
             mock.patch.object(dictation, "cleanup", side_effect=AssertionError("no model")):
            out = dictation.transcribe_audio(FakeApp(), b"a", "audio/wav")
        self.assertEqual(out["raw"], out["clean"])
        self.assertTrue(out["clean_skipped"])

    def test_qwen_failure_uses_warm_whisper_and_reports_actual_engine(self):
        for error in (RuntimeError("worker failed"), OSError("broken pipe"), ValueError("bad JSON"), TimeoutError()):
            with mock.patch.object(dictation, "convert_audio", side_effect=lambda r, m, w: write_wav_file(w)), \
                 mock.patch.object(dictation.Qwen3ASR, "transcribe", side_effect=error), \
                 mock.patch.object(dictation.WhisperCpp, "transcribe", return_value="Hello.") as whisper:
                out = dictation.transcribe_audio(FakeApp(), b"a", "audio/wav")
            whisper.assert_called_once()
            self.assertEqual((out["engine"], out["fallback_from"]), ("whisper.cpp", "qwen3"))
            self.assertEqual(dictation.settings_get(FakeApp(), None)["in_use"], "whisper.cpp")

    def test_both_engines_failing_is_not_silent_success(self):
        with mock.patch.object(dictation, "convert_audio", side_effect=lambda r, m, w: write_wav_file(w)), \
             mock.patch.object(dictation.Qwen3ASR, "transcribe", side_effect=RuntimeError("qwen failed")), \
             mock.patch.object(dictation.WhisperCpp, "transcribe", side_effect=RuntimeError("whisper failed")):
            with self.assertRaisesRegex(RuntimeError, "whisper failed"):
                dictation.transcribe_audio(FakeApp(), b"a", "audio/wav")


# ---------- /api/dictation ----------

class DictationRouteTest(RunsMixin, unittest.TestCase):
    def result(self):
        return {"raw": "hi", "clean": "Hi.", "engine": "whisper.cpp", "ms": {"asr": 1, "clean": 2}}

    def test_composer_returns_shape_and_writes_nothing(self):
        app = FakeApp(world="w-test", day=1, turn=4)
        with mock.patch.object(dictation, "transcribe_audio", return_value=self.result()) as tr:
            out = dictation.dictation(app, dictation_request(b"audio", "audio/webm", "composer"))
        self.assertEqual(out, self.result())
        self.assertNotIn("thread", out)
        tr.assert_called_once_with(app, b"audio", "audio/webm")
        self.assertFalse(self.threads_index().exists())
        self.assertFalse(self.daylog().exists())

    def test_ambient_context_records_thread(self):
        app = FakeApp(world="w-test", day=1, turn=9)
        with mock.patch.object(dictation, "transcribe_audio", return_value=dict(self.result())):
            out = dictation.dictation(app, dictation_request(b"a", "audio/mp4", "ambient"))
        self.assertRegex(out["thread"], r"^t\d+$")
        entry = threads.get("w-test", 1, out["thread"])
        self.assertEqual((entry["kind"], entry["at_turn"]), ("dictation", 9))
        self.assertEqual(entry["records"][0]["content"], "hi")
        self.assertFalse(self.daylog().exists())

    def test_unknown_context_and_propagated_errors(self):
        with mock.patch.object(dictation, "transcribe_audio", side_effect=AssertionError("validate first")):
            with self.assertRaisesRegex(ValueError, "composer or ambient"):
                dictation.dictation(FakeApp(), dictation_request(b"a", "audio/webm", "nope"))
        with mock.patch.object(dictation, "transcribe_audio", side_effect=ValueError("Record up to 60")):
            with self.assertRaisesRegex(ValueError, "60"):
                dictation.dictation(FakeApp(), dictation_request(b"a", "audio/webm", "composer"))


# ---------- /api/ambient ----------

class AmbientRouteTest(RunsMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._saved = sys.modules.pop("afterglow.explainer", None)

    def tearDown(self):
        super().tearDown()
        if self._saved is not None:
            sys.modules["afterglow.explainer"] = self._saved
        else:
            sys.modules.pop("afterglow.explainer", None)

    def doc(self, raw=b"audio", mime="audio/webm", **extra):
        d = {"audio_b64": base64.b64encode(raw).decode(), "mime": mime}
        d.update(extra)
        return d

    def result(self, raw="remind me", clean="Remind me."):
        return {"raw": raw, "clean": clean, "engine": "whisper.cpp", "ms": {"asr": 1, "clean": 1}}

    def test_paste_creates_thread_with_id_string(self):
        app = FakeApp(FakeClient(fail=AssertionError("no model")), world="w-test", day=1, turn=2)
        with mock.patch.object(dictation, "transcribe_audio", return_value=self.result()) as tr:
            out = dictation.ambient(app, ambient_request(self.doc(app_name="Notes")))
        self.assertEqual(out["mode"], "paste")
        self.assertEqual(out["text"], "Remind me.")
        self.assertRegex(out["thread"], r"^t\d+$")
        tr.assert_called_once_with(app, b"audio", "audio/webm")
        entry = threads.get("w-test", 1, out["thread"])
        rec = entry["records"][0]
        self.assertEqual((rec["content"], rec["clean"], rec["app_name"]), ("remind me", "Remind me.", "Notes"))
        self.assertFalse(self.daylog().exists())

    def test_explain_with_fake_explainer_present(self):
        import types
        seen = {}
        def fake_create(app, question=None, crop_png_b64=None, ocr=None, ax=None, app_name=None):
            seen.update(question=question, crop_png_b64=crop_png_b64, ocr=ocr, ax=ax, app_name=app_name)
            return {"id": "e1", "url": "/explain/e1"}
        mod = types.ModuleType("afterglow.explainer")
        mod.create = fake_create
        sys.modules["afterglow.explainer"] = mod
        ocr = [{"text": "OK", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}]
        ax = {"role": "button", "title": "OK", "value": "", "app": "Safari"}
        with mock.patch.object(dictation, "transcribe_audio",
                               return_value=self.result("what is this", "What is this?")):
            out = dictation.ambient(FakeApp(), ambient_request(self.doc(crop_png_b64="png", ocr=ocr, ax=ax,
                                                                            app_name="Safari")))
        self.assertEqual(out, {"mode": "explain", "id": "e1", "url": "/explain/e1", "text": "What is this?"})
        self.assertEqual((seen["question"], seen["crop_png_b64"], seen["ocr"], seen["ax"], seen["app_name"]),
                         ("What is this?", "png", ocr, ax, "Safari"))

    def test_explain_missing_falls_back_other_import_errors_raise(self):
        sys.modules.pop("afterglow.explainer", None)
        real_import = dictation.importlib.import_module
        def missing(name, package=None):
            if name == ".explainer":
                raise ModuleNotFoundError("missing", name="afterglow.explainer")
            return real_import(name, package)
        with mock.patch.object(dictation, "transcribe_audio",
                               return_value=self.result("what is this", "What is this?")), \
             mock.patch.object(dictation.importlib, "import_module", side_effect=missing):
            out = dictation.ambient(FakeApp(), ambient_request(self.doc(crop_png_b64="png")))
        self.assertEqual(out["mode"], "paste")
        with mock.patch.object(dictation, "transcribe_audio",
                               return_value=self.result("what is this", "What is this?")), \
             mock.patch.object(dictation.importlib, "import_module",
                               side_effect=ModuleNotFoundError("x", name="some_dep")):
            with self.assertRaises(ModuleNotFoundError):
                dictation.ambient(FakeApp(), ambient_request(self.doc(crop_png_b64="png")))

    def test_invalid_audio_rejected_before_transcribe(self):
        with mock.patch.object(dictation, "transcribe_audio", side_effect=AssertionError("validate first")):
            with self.assertRaises(ValueError):
                dictation.ambient(FakeApp(), ambient_request({}))
            with self.assertRaisesRegex(ValueError, "base64"):
                dictation.ambient(FakeApp(), ambient_request({"audio_b64": "!!!", "mime": "audio/webm"}))
            with self.assertRaises(ValueError):
                dictation.ambient(FakeApp(), routes.Request("POST", "/api/ambient", "",
                                                        {"Content-Type": "text/plain"}, b"x"))


# ---------- settings ----------

class SettingsTest(RunsMixin, unittest.TestCase):
    def test_get_defaults(self):
        out = dictation.settings_get(FakeApp(), settings_get_request())
        self.assertEqual((out["engine"], out["model"], out["server_url"], out["rules"]),
                         ("qwen3", dictation.DEFAULT_MODEL, "", ""))
        by_id = {e["id"]: e for e in out["engines"]}
        self.assertTrue(by_id["whisper.cpp"]["available"])
        self.assertEqual(by_id["qwen3"]["available"], dictation.asr_runtime.ASR_PYTHON.is_file())

    def test_post_round_trip_persists_files(self):
        app = FakeApp()
        body = {"engine": "whisper.cpp", "model": "/tmp/m.bin",
                "server_url": "http://127.0.0.1:8080/", "rules": "A.\nB."}
        out = dictation.settings_post(app, settings_post_request(body))
        self.assertEqual((out["model"], out["server_url"], out["rules"]), ("/tmp/m.bin", "http://127.0.0.1:8080", "A.\nB."))
        self.assertEqual(json.loads((self.runs / "_settings" / "dictation.json").read_text()),
                         {"engine": "whisper.cpp", "model": "/tmp/m.bin", "server_url": "http://127.0.0.1:8080"})
        self.assertEqual((self.runs / "_settings" / "dictation-rules.md").read_text(), "A.\nB.")
        again = dictation.settings_get(app, settings_get_request())
        self.assertEqual(again["rules"], "A.\nB.")

    def test_post_invalid_inputs_rejected(self):
        app = FakeApp()
        self.assertEqual(dictation.settings_post(app, settings_post_request({"engine": "qwen3"}))["engine"], "qwen3")
        with self.assertRaisesRegex(ValueError, "Unknown ASR"):
            dictation.settings_post(app, settings_post_request({"engine": "bogus"}))
        for bad in ("ftp://h", "http://", "http://u@h/"):
            with self.assertRaisesRegex(ValueError, "HTTP server", msg=bad):
                dictation.settings_post(app, settings_post_request({"server_url": bad}))
        with self.assertRaisesRegex(ValueError, "must be text"):
            dictation.settings_post(app, settings_post_request({"engine": 1}))
        with self.assertRaisesRegex(ValueError, "16,000"):
            dictation.settings_post(app, settings_post_request({"rules": "x" * 16001}))
        with self.assertRaises(ValueError):
            dictation.settings_post(app, routes.Request("POST", "/api/dictation/settings", "",
                                                    {"Content-Type": "text/plain"}, b"x"))


# ---------- routes ----------

class RoutesTest(unittest.TestCase):
    def test_dictation_routes_registered(self):
        self.assertIs(dictation.ROUTES[("POST", "/api/dictation")], dictation.dictation)
        self.assertIs(dictation.ROUTES[("POST", "/api/ambient")], dictation.ambient)
        self.assertIs(dictation.ROUTES[("GET", "/api/dictation/settings")], dictation.settings_get)
        self.assertIs(dictation.ROUTES[("POST", "/api/dictation/settings")], dictation.settings_post)


if __name__ == "__main__":
    unittest.main()
