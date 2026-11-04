"""Bounded offline tests for warm vision (fake workers, no model, no venv install)."""
import base64
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import routes, vision, vlm_runtime, vlm_worker  # noqa: E402

FAKE_VLM = r"""
import sys, json, os, time
mode = "echo"
for i, a in enumerate(sys.argv):
    if a == "--model" and i + 1 < len(sys.argv):
        mode = sys.argv[i + 1]
name, _, arg = mode.partition(":")
print(json.dumps({"ready": True, "model": mode}), flush=True)
GOOD_OBS = [{"id": "v0", "kind": "emoji", "text": "thumbs up", "label": "thumbs-up",
             "box": [0.42, 0.30, 0.08, 0.12]}]
for raw in sys.stdin:
    if not raw.strip():
        continue
    doc = json.loads(raw)
    if doc.get("shutdown") is True or doc.get("command") == "shutdown":
        break
    cid = doc.get("crop_id", "c0")
    if name == "error":
        print(json.dumps({"ok": False, "error": "bad pixels (fake)"}), flush=True)
    elif name == "malformed":
        print("not-json", flush=True)
    elif name == "partial":
        sys.stdout.write('{"ok": true')
        sys.stdout.flush()
        time.sleep(10)
    elif name == "badbox":
        print(json.dumps({"ok": True, "crop_id": cid, "ms": 3, "unknowns": [],
                          "observations": [{"id": "v0", "kind": "icon", "text": "x",
                                            "label": "x", "box": [0.9, 0.9, 0.5, 0.5]}]}), flush=True)
    elif name == "mismatch":
        print(json.dumps({"ok": True, "crop_id": "other", "ms": 3, "unknowns": [],
                          "observations": GOOD_OBS}), flush=True)
    elif name == "toomany":
        print(json.dumps({"ok": True, "crop_id": cid, "ms": 3, "unknowns": [],
                          "observations": [dict(o, id=f"v{i}") for i, o in
                                           enumerate([GOOD_OBS[0]] * 9)]}), flush=True)
    elif name == "crash-once":
        n = 0
        try:
            if os.path.exists(arg):
                n = int(open(arg).read().strip() or "0")
        except Exception:
            n = 0
        try:
            open(arg, "w").write(str(n + 1))
        except Exception:
            pass
        if n == 0:
            sys.exit(1)
        print(json.dumps({"ok": True, "crop_id": cid, "ms": 3, "unknowns": [],
                          "observations": GOOD_OBS}), flush=True)
    elif name == "delay":
        time.sleep(float(arg or "0.3"))
        print(json.dumps({"ok": True, "crop_id": cid, "ms": 3, "unknowns": [],
                          "observations": GOOD_OBS}), flush=True)
    else:
        print(json.dumps({"ok": True, "crop_id": cid, "ms": 3,
                          "unknowns": ["the small print"],
                          "observations": GOOD_OBS}), flush=True)
"""

WARM_PNG = base64.b64decode(vlm_worker.WARM_PNG_B64)


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True


class Base(unittest.TestCase):
    def tmpdir(self):
        t = tempfile.TemporaryDirectory(prefix="vlm-test-")
        self.addCleanup(t.cleanup)
        return Path(t.name)

    def fake_worker(self, tmp):
        p = tmp / "fake_vlm.py"
        p.write_text(FAKE_VLM)
        return str(p)

    def png(self, tmp, name="c.png"):
        p = tmp / name
        p.write_bytes(WARM_PNG)
        return str(p)

    def worker(self, worker_path, model="echo", **kw):
        kw.setdefault("start_timeout", 5)
        kw.setdefault("describe_timeout", 5)
        kw.setdefault("stop_timeout", 2)
        w = vlm_runtime.VlmWorker(model=model, python=sys.executable,
                                  worker_path=worker_path, **kw)
        self.addCleanup(w.stop)
        return w


class FactsValidationTest(unittest.TestCase):
    def good(self, **kw):
        doc = {"ok": True, "crop_id": "c1", "ms": 12, "unknowns": ["u"],
               "observations": [{"id": "v0", "kind": "emoji", "text": "t", "label": "l",
                                 "box": [0.1, 0.2, 0.3, 0.4]}]}
        doc.update(kw)
        return doc

    def test_valid_facts_pass(self):
        out = vlm_runtime.validate_facts(self.good(), "c1")
        self.assertEqual(out["observations"][0]["box"], [0.1, 0.2, 0.3, 0.4])
        self.assertEqual((out["ms"], out["unknowns"]), (12, ["u"]))

    def test_protocol_violations_reject_the_whole_reply(self):
        bad = [
            {"ok": False, "error": "x"},
            self.good(crop_id="other"),
            self.good(observations="not-a-list"),
            self.good(ms=-1),
            "not-a-dict",
        ]
        for doc in bad:
            with self.assertRaises(RuntimeError, msg=f"{doc!r}"[:160]):
                vlm_runtime.validate_facts(doc, "c1")

    def test_model_mistakes_are_repaired_not_thrown_away(self):
        o = lambda i, box, **kw: {"id": i, "kind": "text", "text": f"t{i}", "label": "l", "box": box, **kw}
        out = vlm_runtime.validate_facts(self.good(observations=[
            o("b0", [0, 0, 0.1, 0.1]),                     # wrong id: renumbered
            o("v7", [0.05, 0.65, 0.35, 0.75]),             # corners, not x-y-w-h: converted
            o("v2", [0.9, 0.9, 0.5, 0.5]),                 # unusable: no box, the text stays
            o("v3", [0, 0, float("nan"), 0.1]),
            {"id": "v4", "kind": "", "text": "送信 Send", "box": [0, 0, 0.2, 0.1], "state": "disabled"},  # no label
            {"id": "v5", "kind": "text", "text": "", "label": "", "box": [0, 0, 0.1, 0.1]},  # empty: skipped
        ] + [o("v9", [0, 0, 0.1, 0.1])] * 5, unknowns=["ok", 3]), "c1")
        obs = out["observations"]
        self.assertEqual([x["id"] for x in obs], [f"v{i}" for i in range(vlm_runtime.MAX_OBSERVATIONS)])
        self.assertEqual(obs[0]["box"], [0.0, 0.0, 0.1, 0.1])
        self.assertEqual([round(v, 2) for v in obs[1]["box"]], [0.05, 0.65, 0.3, 0.1])
        self.assertIsNone(obs[2]["box"])
        self.assertIsNone(obs[3]["box"])
        self.assertEqual((obs[4]["text"], obs[4]["label"], obs[4]["kind"], obs[4]["state"]), ("送信 Send", "", "object", "disabled"))
        self.assertEqual(out["unknowns"], ["ok"])

    def test_a_box_on_the_0_to_1000_grid_is_scaled(self):
        self.assertEqual(vlm_runtime._repair_box([0, 0, 1000, 1000]), [0.0, 0.0, 1.0, 1.0])
        self.assertEqual(vlm_runtime._repair_box([100, 200, 300, 400]), [0.1, 0.2, 0.3, 0.4])
        self.assertIsNone(vlm_runtime._repair_box([0, 0, 2000, 10]))


class WorkerProtocolTest(Base):
    def test_describe_reuses_one_warm_worker(self):
        tmp = self.tmpdir()
        w = self.worker(self.fake_worker(tmp))
        facts = w.describe_file(self.png(tmp), "What is this?", crop_id="c9")
        self.assertEqual(facts["crop_id"], "c9")
        self.assertEqual(facts["observations"][0]["label"], "thumbs-up")
        pid1 = w.status()["pid"]
        self.assertTrue(w.status()["warm"])
        w.describe_file(self.png(tmp), crop_id="c10")
        self.assertEqual(w.status()["pid"], pid1)
        self.assertTrue(pid_alive(pid1))

    def test_describe_png_writes_only_a_temp_crop(self):
        tmp = self.tmpdir()
        w = self.worker(self.fake_worker(tmp))
        before = set(os.listdir(tmp))
        facts = w.describe_png(WARM_PNG, crop_id="c1")
        self.assertEqual(facts["crop_id"], "c1")
        self.assertEqual(set(os.listdir(tmp)), before | {"fake_vlm.py"})
        with self.assertRaises(RuntimeError):
            w.describe_png(b"")
        with self.assertRaises(RuntimeError):
            w.describe_png(b"x" * (vlm_runtime.MAX_PNG_BYTES + 1))

    def test_bad_facts_kill_error_stays_warm(self):
        tmp = self.tmpdir()
        fw = self.fake_worker(tmp)
        for model in ("malformed", "mismatch"):
            w = self.worker(fw, model=model)
            with self.assertRaises(RuntimeError, msg=model):
                w.describe_file(self.png(tmp), crop_id="c1")
            self.assertFalse(w.status()["warm"])
        for model in ("badbox", "toomany"):  # the model's own mistakes are repaired, not fatal
            w = self.worker(fw, model=model)
            facts = w.describe_file(self.png(tmp), crop_id="c1")
            self.assertLessEqual(len(facts["observations"]), vlm_runtime.MAX_OBSERVATIONS, model)
            if model == "badbox":
                self.assertIsNone(facts["observations"][0]["box"])
            w.stop()
        w = self.worker(fw, model="error")
        with self.assertRaisesRegex(RuntimeError, "bad pixels"):
            w.describe_file(self.png(tmp), crop_id="c1")
        self.assertTrue(w.status()["warm"])

    def test_partial_line_times_out(self):
        tmp = self.tmpdir()
        w = self.worker(self.fake_worker(tmp), model="partial", describe_timeout=0.4)
        t0 = time.time()
        with self.assertRaises(RuntimeError):
            w.describe_file(self.png(tmp), crop_id="c1")
        self.assertLess(time.time() - t0, 2.0)
        self.assertFalse(w.status()["warm"])

    def test_crash_then_next_restarts(self):
        tmp = self.tmpdir()
        w = self.worker(self.fake_worker(tmp), model=f"crash-once:{tmp / 'n'}")
        with self.assertRaises(RuntimeError):
            w.describe_file(self.png(tmp), crop_id="c1")
        facts = w.describe_file(self.png(tmp), crop_id="c1")
        self.assertEqual(facts["observations"][0]["id"], "v0")
        self.assertTrue(w.status()["warm"])

    def test_serialized_calls(self):
        tmp = self.tmpdir()
        w = self.worker(self.fake_worker(tmp), model="delay:0.3")
        img = self.png(tmp)
        bar = threading.Barrier(2)
        out = []

        def one():
            bar.wait()
            out.append(w.describe_file(img, crop_id="c1")["crop_id"])

        ts = [threading.Thread(target=one) for _ in range(2)]
        t0 = time.time()
        [t.start() for t in ts]
        [t.join(timeout=10) for t in ts]
        self.assertGreaterEqual(time.time() - t0, 0.5)
        self.assertEqual(out, ["c1", "c1"])

    def test_status_never_raises_nor_spawns(self):
        w = vlm_runtime.VlmWorker(model="echo", python="/nonexistent/python",
                                  worker_path="/nonexistent/worker.py")
        self.addCleanup(w.stop)
        self.assertFalse(w.status()["warm"])
        self.assertIsNone(w.status()["pid"])
        self.assertIsNone(w._proc)
        with self.assertRaises(RuntimeError):
            w.describe_file("/nonexistent/c.png", crop_id="c1")

    def test_no_mlx_import(self):
        self.assertNotIn("mlx_vlm", sys.modules)
        self.assertNotIn("mlx", sys.modules)

    def test_warm_image_is_a_real_png(self):
        tmp = self.tmpdir()
        data = Path(vlm_worker._warm_image(str(tmp))).read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        try:
            from PIL import Image
        except ImportError:
            pass
        else:
            self.assertEqual(Image.open(tmp / "warm.png").size, (224, 224))

    def test_worker_module_has_no_ml_imports(self):
        src = Path(vlm_worker.__file__).read_text()
        top, _, _ = src.partition("def run(")
        self.assertNotIn("mlx_vlm", top)
        self.assertNotIn("import mlx", top)


class LifecycleTest(Base):
    def setUp(self):
        self._old_models = sys.modules.get("afterglow.models")
        sys.modules.pop("afterglow.models", None)
        vlm_runtime.configure("vision")
        vlm_runtime.set_keep_loaded("vision", False)

    def tearDown(self):
        sys.modules.pop("afterglow.models", None)
        vlm_runtime.configure("vision")
        vlm_runtime.set_keep_loaded("vision", False)
        if self._old_models is not None:
            sys.modules["afterglow.models"] = self._old_models
        else:
            sys.modules.pop("afterglow.models", None)

    def fake_models(self, choice=None, keep=False, weights=None):
        mod = types.ModuleType("afterglow.models")
        mod.vision_choice = lambda: choice
        mod.keep_loaded = lambda slot: keep
        mod.model_path = lambda c: weights if weights is not None else Path("/nonexistent")
        sys.modules["afterglow.models"] = mod
        return mod

    def test_configure_uses_the_checked_local_path_only(self):
        tmp = self.tmpdir()
        weights = tmp / "weights"
        weights.mkdir()
        choice = {"id": "qwen3.5-4b-mac", "runtime": "mlx-vlm",
                  "repo": "mlx-community/Qwen3.5-4B-MLX-4bit"}
        self.fake_models(choice=choice, keep=False, weights=weights)
        out = vlm_runtime.configure("vision")
        self.assertEqual(out["model"], str(weights))
        self.assertEqual(vlm_runtime.active_model(), str(weights))
        self.assertFalse(vlm_runtime.status()["warm"])
        self.assertFalse(vlm_runtime.status()["starting"])
        self.assertFalse(vlm_runtime.is_keep_loaded())

    def test_configure_without_local_weights_configures_nothing(self):
        choice = {"id": "qwen3.5-4b-mac", "runtime": "mlx-vlm",
                  "repo": "mlx-community/Qwen3.5-4B-MLX-4bit"}
        self.fake_models(choice=choice, keep=True, weights=Path("/nonexistent"))
        out = vlm_runtime.configure("vision")
        self.assertIsNone(out["model"])
        self.assertIsNone(vlm_runtime.active_model())
        self.assertFalse(vlm_runtime.status()["starting"])

    def test_configure_warms_in_background_when_kept(self):
        tmp = self.tmpdir()
        weights = tmp / "weights"
        weights.mkdir()
        choice = {"id": "qwen3.5-4b-mac", "runtime": "mlx-vlm",
                  "repo": "mlx-community/Qwen3.5-4B-MLX-4bit"}
        self.fake_models(choice=choice, keep=True, weights=weights)
        called = threading.Event()
        with mock.patch.object(vlm_runtime.VlmWorker, "warmup",
                               side_effect=lambda *a, **k: called.set()):
            vlm_runtime.configure("vision")
            self.assertTrue(called.wait(timeout=5))
        self.assertTrue(vlm_runtime.is_keep_loaded())

    def test_configure_ignores_other_slots(self):
        self.assertEqual(vlm_runtime.configure("speech"), {"slot": "speech"})
        self.assertEqual(vlm_runtime.set_keep_loaded("speech", True), {"slot": "speech"})
        self.assertIsNone(vlm_runtime.active_model())

    def test_keep_loaded_off_stops_a_warm_worker(self):
        tmp = self.tmpdir()
        w = self.worker(self.fake_worker(tmp))
        w.describe_file(self.png(tmp), crop_id="c1")
        pid = w.status()["pid"]
        with mock.patch.object(vlm_runtime, "_worker", w):
            vlm_runtime.set_keep_loaded("vision", False)
        self.assertFalse(pid_alive(pid))

    def test_status_never_raises(self):
        with mock.patch.object(vlm_runtime, "_worker", object()):
            st = vlm_runtime.status()
        self.assertFalse(st["warm"])

    def test_ensure_runtime_is_explicit_only(self):
        with mock.patch.object(vlm_runtime, "VLM_PYTHON", Path("/nonexistent/python")):
            self.assertIsNone(vlm_runtime._runtime_version())
            with mock.patch.object(vlm_runtime.subprocess, "run",
                                    side_effect=AssertionError("must not spawn")):
                with mock.patch.object(vlm_runtime, "_find_uv", return_value=None):
                    out = vlm_runtime.ensure_runtime()
        self.assertEqual(out["ready"], False)
        self.assertIn("uv", out["line"])


class VisionApiTest(Base):
    def setUp(self):
        self._old_models = sys.modules.get("afterglow.models")
        sys.modules.pop("afterglow.models", None)
        self._old_runs = os.environ.get("AFTERGLOW_RUNS")
        self._temp_runs = tempfile.TemporaryDirectory(prefix="afterglow-vision-test-")
        os.environ["AFTERGLOW_RUNS"] = self._temp_runs.name
        vlm_runtime.configure("vision")
        vlm_runtime.set_keep_loaded("vision", False)

    def tearDown(self):
        sys.modules.pop("afterglow.models", None)
        vlm_runtime.configure("vision")
        vlm_runtime.set_keep_loaded("vision", False)
        if self._old_models is not None:
            sys.modules["afterglow.models"] = self._old_models
        else:
            sys.modules.pop("afterglow.models", None)
        if self._old_runs is None:
            os.environ.pop("AFTERGLOW_RUNS", None)
        else:
            os.environ["AFTERGLOW_RUNS"] = self._old_runs
        self._temp_runs.cleanup()

    def fake_models(self, mode="none", choice=None, keep=False, weights=None):
        mod = types.ModuleType("afterglow.models")
        mod.vision_mode = lambda: mode
        mod.vision_choice = lambda: choice
        mod.keep_loaded = lambda slot: keep
        mod.model_path = lambda c: weights if weights is not None else Path("/nonexistent")
        sys.modules["afterglow.models"] = mod
        return mod

    def mac_choice(self):
        return {"id": "qwen3.5-4b-mac", "runtime": "mlx-vlm",
                "repo": "mlx-community/Qwen3.5-4B-MLX-4bit"}

    def test_mode_passes_the_app_through(self):
        mod = types.ModuleType("afterglow.models")
        seen = {}
        mod.vision_mode = lambda app=None: seen.setdefault("app", app) or "glimmer"
        mod.vision_choice = lambda: {"id": "glimmer-mmproj", "runtime": "none"}
        sys.modules["afterglow.models"] = mod
        self.assertEqual(vision.mode(app="fake-app"), "glimmer")
        self.assertEqual(seen["app"], "fake-app")
        self.assertEqual(vision.mode(), "glimmer")

    def test_mode_defaults_to_none_without_models(self):
        sys.modules.pop("afterglow.models", None)
        with mock.patch("importlib.import_module", side_effect=ImportError("no models")):
            self.assertEqual(vision.mode(), "none")
            self.assertIsNone(vision.active_choice())
            self.assertIsNone(vision.describe(WARM_PNG))

    def test_describe_returns_none_for_none_and_glimmer(self):
        self.fake_models(mode="none")
        self.assertIsNone(vision.describe(WARM_PNG, "What is this?"))
        self.fake_models(mode="glimmer", choice={"id": "glimmer-mmproj", "runtime": "none"})
        self.assertIsNone(vision.describe(WARM_PNG, "What is this?"))

    def test_describe_validates_input(self):
        self.fake_models(mode="mac")
        for bad in (b"", "not-bytes", None, b"GIF89a...."):
            with self.assertRaises(ValueError, msg=f"{bad!r}"[:40]):
                vision.describe(bad)
        with self.assertRaises(ValueError):
            vision.describe(WARM_PNG + b"x" * vision.MAX_PNG_BYTES)
        with self.assertRaises(ValueError):
            vision.describe(WARM_PNG, question=3)

    def test_describe_mac_round_trip_through_fake_worker(self):
        tmp = self.tmpdir()
        w = self.worker(self.fake_worker(tmp))
        weights = tmp / "weights"
        weights.mkdir()
        self.fake_models(mode="mac", choice=self.mac_choice(), keep=True, weights=weights)
        with mock.patch.object(vlm_runtime, "worker_for", return_value=w) as wf:
            out = vision.describe(WARM_PNG, "What is this?")
            wf.assert_called_once_with(str(weights))
        self.assertEqual(out["text"], "thumbs up")
        self.assertEqual(out["items"], [{"label": "thumbs-up", "box": [0.42, 0.30, 0.08, 0.12]}])
        self.assertEqual(out["unknowns"], ["the small print"])
        self.assertTrue(w.status()["warm"])

    def test_on_demand_describe_stops_the_worker_after(self):
        tmp = self.tmpdir()
        w = self.worker(self.fake_worker(tmp))
        weights = tmp / "weights"
        weights.mkdir()
        self.fake_models(mode="mac", choice=self.mac_choice(), keep=False, weights=weights)
        with mock.patch.object(vlm_runtime, "worker_for", return_value=w):
            self.assertIsNotNone(vision.describe(WARM_PNG))
        self.assertFalse(w.status()["warm"])

    def test_worker_failure_answers_none_not_partial(self):
        tmp = self.tmpdir()
        w = self.worker(self.fake_worker(tmp), model="malformed")
        weights = tmp / "weights"
        weights.mkdir()
        self.fake_models(mode="mac", choice=self.mac_choice(), keep=True, weights=weights)
        with mock.patch.object(vlm_runtime, "worker_for", return_value=w):
            self.assertIsNone(vision.describe(WARM_PNG))

    def test_missing_local_weights_never_falls_back_to_repo(self):
        self.fake_models(mode="mac", choice=self.mac_choice(), keep=True, weights=None)
        with mock.patch.object(vlm_runtime, "worker_for",
                               side_effect=AssertionError("must not load")):
            self.assertIsNone(vision.describe(WARM_PNG))

    def test_route_validates_and_reports_availability(self):
        def post(doc):
            return routes.Request("POST", "/api/vision/describe", "",
                                  {"Content-Type": "application/json"},
                                  json.dumps(doc).encode())

        self.fake_models(mode="none")
        out = vision.vision_describe(None, post(
            {"png_b64": base64.b64encode(WARM_PNG).decode()}))
        self.assertEqual((out["available"], out["mode"]), (False, "none"))
        self.fake_models(mode="glimmer")
        out = vision.vision_describe(None, post(
            {"png_b64": base64.b64encode(WARM_PNG).decode(), "question": "What is this?"}))
        self.assertEqual((out["available"], out["mode"]), (False, "glimmer"))
        for bad in ({}, {"png_b64": 3}, {"png_b64": "!!!not-base64!!!"}):
            with self.assertRaises(ValueError, msg=f"{bad!r}"[:60]):
                vision.vision_describe(None, post(bad))
        self.assertIn(("POST", "/api/vision/describe"), vision.ROUTES)

    def test_route_mac_success_and_failure_shapes(self):
        def post(doc):
            return routes.Request("POST", "/api/vision/describe", "",
                                  {"Content-Type": "application/json"},
                                  json.dumps(doc).encode())

        body = {"png_b64": base64.b64encode(WARM_PNG).decode(), "question": "What is this?"}
        tmp = self.tmpdir()
        w = self.worker(self.fake_worker(tmp))
        weights = tmp / "weights"
        weights.mkdir()
        self.fake_models(mode="mac", choice=self.mac_choice(), keep=True, weights=weights)
        with mock.patch.object(vlm_runtime, "worker_for", return_value=w):
            out = vision.vision_describe(None, post(body))
        self.assertEqual((out["available"], out["ok"], out["mode"]), (True, True, "mac"))
        self.assertEqual(out["items"][0]["label"], "thumbs-up")
        w2 = self.worker(self.fake_worker(tmp), model="error")
        with mock.patch.object(vlm_runtime, "worker_for", return_value=w2):
            out = vision.vision_describe(None, post(body))
        self.assertEqual((out["available"], out["ok"]), (True, False))
        self.assertIn("bad pixels", out["diagnostic"])
        self.assertIn("bad pixels", vision.last_diagnostic())

    def test_reads_write_no_files(self):
        runs = Path(os.environ["AFTERGLOW_RUNS"])
        self.fake_models(mode="none")
        vision.mode()
        vision.active_choice()
        vision.describe(WARM_PNG)
        vlm_runtime.status()
        vlm_runtime.configure("vision")
        self.assertFalse(runs.exists() and any(runs.iterdir()))


if __name__ == "__main__":
    unittest.main()
