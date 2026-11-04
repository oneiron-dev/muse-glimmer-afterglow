"""Speech follows Settings > Models: exact defaults with nothing saved, saved choices after."""
import io
import os
import struct
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import asr_runtime, dictation  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self._old_models = sys.modules.get("afterglow.models")
        self._qwen, self._whisper = dictation._qwen, dictation._whisper
        self._last_engine = dictation._last_engine
        dictation._last_engine = None
        self._old_runs = os.environ.get("AFTERGLOW_RUNS")
        self._temp_runs = tempfile.TemporaryDirectory(prefix="afterglow-speech-test-")
        os.environ["AFTERGLOW_RUNS"] = str(self._temp_runs.name)

    def tearDown(self):
        if self._old_models is not None:
            sys.modules["afterglow.models"] = self._old_models
        else:
            sys.modules.pop("afterglow.models", None)
        try:
            if dictation._qwen is not self._qwen and dictation._qwen is not None:
                dictation._qwen.stop()
        except Exception:
            pass
        dictation._qwen, dictation._whisper = self._qwen, self._whisper
        dictation._last_engine = self._last_engine
        if self._old_runs is None:
            os.environ.pop("AFTERGLOW_RUNS", None)
        else:
            os.environ["AFTERGLOW_RUNS"] = self._old_runs
        self._temp_runs.cleanup()

    def fake_models(self, choice=None, model_path=None, fail=False, saved_doc=None):
        mod = types.ModuleType("afterglow.models")
        if fail:
            def _boom(*a, **k):
                raise RuntimeError("models broken (fake)")
            mod.speech_choice = _boom
            mod.model_path = _boom
            mod.saved = _boom
        else:
            mod.speech_choice = lambda: choice
            mod.model_path = lambda c: model_path
            mod.saved = lambda: saved_doc
        sys.modules["afterglow.models"] = mod
        return mod

    def tone_wav(self, path):
        import math
        nframes = 8000
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"".join(
                struct.pack("<h", int(12000 * math.sin(2 * 3.14159 * 440 * i / 16000)))
                for i in range(nframes)))
        Path(path).write_bytes(buf.getvalue())


class DefaultsTest(Base):
    def test_exact_asr_default_with_nothing_saved(self):
        self.fake_models(choice=None)
        self.assertIsNone(asr_runtime.selected_qwen_model())
        self.assertIsNone(asr_runtime.selected_whisper_path())
        self.assertEqual(asr_runtime.active_qwen_model(), asr_runtime.ASR_MODEL)
        self.assertEqual(asr_runtime.active_qwen_model(), "mlx-community/Qwen3-ASR-1.7B-8bit")
        self.assertEqual(dictation.WhisperCpp._wanted_model(), dictation.DEFAULT_MODEL)
        self.assertEqual(dictation._get_qwen()._model, asr_runtime.ASR_MODEL)

    def test_broken_or_missing_models_module_keeps_defaults(self):
        self.fake_models(fail=True)
        self.assertEqual(asr_runtime.active_qwen_model(), asr_runtime.ASR_MODEL)
        self.assertIsNone(asr_runtime.selected_whisper_path())
        sys.modules.pop("afterglow.models", None)
        with mock.patch("importlib.import_module", side_effect=ImportError("no models")):
            self.assertEqual(asr_runtime.active_qwen_model(), asr_runtime.ASR_MODEL)
            self.assertIsNone(asr_runtime.selected_whisper_path())
            self.assertEqual(dictation.WhisperCpp._wanted_model(), dictation.DEFAULT_MODEL)

    def test_unknown_speech_runtime_keeps_todays_behavior(self):
        self.fake_models(choice={"id": "future-thing", "runtime": "corespeech"})
        self.assertEqual(asr_runtime.active_qwen_model(), asr_runtime.ASR_MODEL)
        self.assertIsNone(asr_runtime.selected_whisper_path())
        self.assertEqual(dictation.WhisperCpp._wanted_model(), dictation.DEFAULT_MODEL)


class SavedChoiceTest(Base):
    def test_mlx_audio_choice_changes_the_qwen_model(self):
        self.fake_models(choice={"id": "granite-speech-5.0-470m-turboctc", "slot": "speech",
                                 "runtime": "mlx-audio",
                                 "repo": "ibm-granite/granite-speech-5.0-470m-turboctc"})
        self.assertEqual(asr_runtime.selected_qwen_model(),
                         "ibm-granite/granite-speech-5.0-470m-turboctc")
        self.assertEqual(asr_runtime.active_qwen_model(),
                         "ibm-granite/granite-speech-5.0-470m-turboctc")
        self.assertEqual(dictation._get_qwen()._model,
                         "ibm-granite/granite-speech-5.0-470m-turboctc")
        self.assertFalse(dictation._get_qwen().status()["warm"])

    def test_mlx_audio_choice_prefers_lane_local_weights(self):
        lane = Path(self._temp_runs.name) / "granite-speech-5.0-470m-turboctc"
        lane.mkdir()
        (lane / "config.json").write_text("{}")
        self.fake_models(choice={"id": "granite-speech-5.0-470m-turboctc", "slot": "speech",
                                 "runtime": "mlx-audio",
                                 "repo": "ibm-granite/granite-speech-5.0-470m-turboctc"},
                         model_path=lane)
        self.assertEqual(asr_runtime.selected_qwen_model(), str(lane))
        self.assertEqual(asr_runtime.active_qwen_model(), str(lane))
        self.assertEqual(dictation._get_qwen()._model, str(lane))

    def test_mlx_audio_choice_without_repo_is_refused_loudly(self):
        self.fake_models(choice={"id": "bad-row", "slot": "speech", "runtime": "mlx-audio"})
        self.assertIsNone(asr_runtime.selected_qwen_model())
        with self.assertRaisesRegex(RuntimeError, "weights address"):
            dictation.Qwen3ASR().transcribe("/tmp/nowhere.wav")

    def test_whisper_choice_resolves_an_existing_file(self):
        tmp = Path(self._temp_runs.name)
        weights = tmp / "ggml-large-v3-turbo.bin"
        weights.write_bytes(b"fake")
        self.fake_models(choice={"id": "whisper-large-v3-turbo", "slot": "speech",
                                 "runtime": "whisper.cpp"}, model_path=weights)
        self.assertEqual(asr_runtime.selected_whisper_path(), str(weights))
        self.assertEqual(dictation.WhisperCpp()._wanted_model(), str(weights))
        self.assertEqual(dictation.WhisperCpp("custom.bin").model, "custom.bin")

    def test_whisper_choice_without_weights_is_refused_loudly(self):
        self.fake_models(choice={"id": "whisper-large-v3-turbo", "slot": "speech",
                                 "runtime": "whisper.cpp"},
                         model_path=Path("/nonexistent/ggml.bin"))
        self.assertIsNone(asr_runtime.selected_whisper_path())
        with self.assertRaisesRegex(RuntimeError, "Download the chosen"):
            dictation.WhisperCpp().transcribe("/tmp/nowhere.wav")

    def test_transcribe_prefers_the_saved_whisper_choice(self):
        tmp = Path(self._temp_runs.name)
        weights = tmp / "ggml-large-v3.bin"
        weights.write_bytes(b"fake")
        self.fake_models(choice={"id": "whisper-large-v3", "slot": "speech",
                                 "runtime": "whisper.cpp"}, model_path=weights)

        def fake_convert(raw, mime, wav_path):
            self.tone_wav(wav_path)
            return 0.5

        with mock.patch.object(dictation, "convert_audio", side_effect=fake_convert), \
                mock.patch.object(dictation.Qwen3ASR, "transcribe",
                                   side_effect=AssertionError("qwen must not run")) as qwen, \
                mock.patch.object(dictation.WhisperCpp, "transcribe",
                                   return_value="Hello.") as whisper:
            out = dictation.transcribe_audio(mock.Mock(), b"raw", "audio/webm")
        qwen.assert_not_called()
        whisper.assert_called_once()
        self.assertEqual((out["engine"], out["clean"]), ("whisper.cpp", "Hello."))

    def test_transcribe_prefers_the_saved_qwen_choice(self):
        (Path(self._temp_runs.name) / "_settings").mkdir(parents=True, exist_ok=True)
        (Path(self._temp_runs.name) / "_settings" / "dictation.json").write_text(
            '{"engine": "whisper.cpp", "model": "/tmp/custom.bin", "server_url": ""}')
        self.fake_models(choice={"id": "qwen3-asr-1.7b-8bit", "slot": "speech",
                                 "runtime": "mlx-audio",
                                 "repo": "mlx-community/Qwen3-ASR-1.7B-8bit"})

        def fake_convert(raw, mime, wav_path):
            self.tone_wav(wav_path)
            return 0.5

        with mock.patch.object(dictation, "convert_audio", side_effect=fake_convert), \
                mock.patch.object(dictation.Qwen3ASR, "transcribe",
                                   return_value="Hello.") as qwen, \
                mock.patch.object(dictation.WhisperCpp, "transcribe",
                                   side_effect=AssertionError("whisper must not run")):
            out = dictation.transcribe_audio(mock.Mock(), b"raw", "audio/webm")
        qwen.assert_called_once()
        self.assertEqual((out["engine"], out["clean"]), ("qwen3", "Hello."))


class KeepAfterInferenceTest(Base):
    def run_transcribe(self):
        def fake_convert(raw, mime, wav_path):
            self.tone_wav(wav_path)
            return 0.5

        with mock.patch.object(dictation, "convert_audio", side_effect=fake_convert), \
                mock.patch.object(dictation.Qwen3ASR, "transcribe", return_value="Hello."), \
                mock.patch.object(dictation._qwen, "stop") as qstop, \
                mock.patch.object(dictation._whisper, "stop") as wstop:
            dictation.transcribe_audio(mock.Mock(), b"raw", "audio/webm")
        return qstop, wstop

    def test_explicit_keep_false_stops_owned_workers(self):
        self.fake_models(choice={"id": "qwen3-asr-1.7b-8bit", "slot": "speech",
                                 "runtime": "mlx-audio",
                                 "repo": "mlx-community/Qwen3-ASR-1.7B-8bit"},
                         saved_doc={"speech": "qwen3-asr-1.7b-8bit",
                                    "keep_loaded": {"speech": False}})
        qstop, wstop = self.run_transcribe()
        qstop.assert_called_once()
        wstop.assert_called_once()

    def test_keep_true_and_absent_leave_workers_warm(self):
        for saved_doc in ({"keep_loaded": {"speech": True}}, {"keep_loaded": {}},
                          {"speech": "qwen3-asr-1.7b-8bit"}, None):
            self.fake_models(choice=None, saved_doc=saved_doc)
            qstop, wstop = self.run_transcribe()
            qstop.assert_not_called()
            wstop.assert_not_called()
        sys.modules.pop("afterglow.models", None)
        with mock.patch("importlib.import_module", side_effect=ImportError("no models")):
            self.assertFalse(dictation._speech_keep_explicit_false())


class WhisperWinsTest(Base):
    def custom_dictation(self):
        (Path(self._temp_runs.name) / "_settings").mkdir(parents=True, exist_ok=True)
        (Path(self._temp_runs.name) / "_settings" / "dictation.json").write_text(
            '{"engine": "whisper.cpp", "model": "/tmp/custom.bin", '
            '"server_url": "http://legacy:8080"}')

    def run_transcribe(self):
        def fake_convert(raw, mime, wav_path):
            self.tone_wav(wav_path)
            return 0.5

        with mock.patch.object(dictation, "convert_audio", side_effect=fake_convert), \
                mock.patch.object(dictation, "WhisperCpp") as cls:
            cls.return_value.transcribe.return_value = "Hello."
            out = dictation.transcribe_audio(mock.Mock(), b"raw", "audio/webm")
        return cls, out

    def test_saved_whisper_wins_over_custom_dictation_config(self):
        self.custom_dictation()
        weights = Path(self._temp_runs.name) / "ggml-large-v3.bin"
        weights.write_bytes(b"fake")
        self.fake_models(choice={"id": "whisper-large-v3", "slot": "speech",
                                 "runtime": "whisper.cpp"}, model_path=weights)
        cls, out = self.run_transcribe()
        cls.assert_called_once_with(None, "")
        self.assertEqual(out["engine"], "whisper.cpp")

    def test_without_saved_choice_dictation_config_rules(self):
        self.custom_dictation()
        self.fake_models(choice=None)
        cls, out = self.run_transcribe()
        cls.assert_called_once_with("/tmp/custom.bin", "http://legacy:8080")
        self.assertEqual(out["engine"], "whisper.cpp")


class LifecycleTest(Base):
    def test_lifecycle_calls_never_raise_nor_spawn(self):
        self.fake_models(choice=None)
        for call in (lambda: dictation.configure_speech("speech"),
                     lambda: dictation.configure_speech("vision"),
                     lambda: dictation.set_speech_keep_loaded("speech", False),
                     lambda: dictation.set_speech_keep_loaded("speech", True),
                     lambda: dictation.set_speech_keep_loaded("vision", True)):
            self.assertIsInstance(call(), dict)
        self.assertFalse(dictation._qwen.status()["warm"])
        self.assertIsNone(dictation._qwen._proc)

    def test_configure_speech_stops_qwen_when_switching_to_whisper(self):
        weights = Path(self._temp_runs.name) / "ggml-large-v3.bin"
        weights.write_bytes(b"fake")
        self.fake_models(choice={"id": "whisper-large-v3", "slot": "speech",
                                 "runtime": "whisper.cpp"}, model_path=weights)
        if getattr(dictation._qwen, "_model", None) != asr_runtime.ASR_MODEL:
            try:
                dictation._qwen.stop()
            except Exception:
                pass
            dictation._qwen = asr_runtime.QwenWorker()
        with mock.patch.object(dictation._qwen, "stop") as qstop:
            dictation.configure_speech("speech")
        qstop.assert_called_once()

    def test_configure_speech_keeps_matching_qwen_warm(self):
        self.fake_models(choice=None)
        if getattr(dictation._qwen, "_model", None) != asr_runtime.ASR_MODEL:
            try:
                dictation._qwen.stop()
            except Exception:
                pass
            dictation._qwen = asr_runtime.QwenWorker()
        with mock.patch.object(dictation._qwen, "stop") as qstop:
            dictation.configure_speech("speech")
        qstop.assert_not_called()

    def test_selection_reads_write_no_files(self):
        runs = Path(os.environ["AFTERGLOW_RUNS"])
        self.fake_models(choice=None)
        asr_runtime.active_qwen_model()
        asr_runtime.selected_whisper_path()
        dictation._get_qwen().status()
        dictation.configure_speech("speech")
        self.assertFalse(any(runs.iterdir()))


if __name__ == "__main__":
    unittest.main()
