"""Settings > Models: defaults, fit maths, states, vision opt-in, hooks. Fake hardware and Hub; no network."""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import model_catalog, model_download, models, modelsetup, routes  # noqa: E402

MAC64 = {"system": "Darwin", "chip": "Apple M4 Max", "memory_gb": 64, "apple_silicon": True,
         "gpus": [], "usable_gb": 48.0}
MAC16 = {"system": "Darwin", "chip": "Apple M4", "memory_gb": 16, "apple_silicon": True,
         "gpus": [], "usable_gb": 10.7}
NVIDIA = {"system": "Linux", "chip": "x86_64", "memory_gb": 64, "apple_silicon": False,
          "gpus": [{"name": "RTX 4090", "memory_gb": 24, "used_gb": 0}]}


class FakeServer:
    def __init__(self, running=False, cmd=None):
        self._running, self.cmd = running, cmd

    def running(self):
        return self._running


class FakeApp:
    def __init__(self, hw=None, source="server", url="http://127.0.0.1:9", path=None,
                 local_server=None, use_model=None):
        self._hw = hw or MAC64
        self.model_source = {"source": source, "url": url}
        if path:
            self.model_source["path"] = path
        self.client = mock.Mock(endpoint=url)
        self.local_server = local_server if local_server is not None else FakeServer()
        self._use_model = use_model

    def hardware(self):
        return self._hw

    def __getattr__(self, name):
        if name == "use_model":
            if self._use_model is None:
                raise AttributeError("no use_model")
            return self._use_model
        raise AttributeError(name)


class PropsServer:
    def __init__(self, vision=True):
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = json.dumps({"modalities": {"vision": outer.vision}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.vision = vision
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


def post(path, body):
    return routes.Request("POST", path, "", {"Content-Type": "application/json"},
                          json.dumps(body).encode())


def get(path):
    return routes.Request("GET", path, "", {}, None)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runs = Path(self.tmp.name) / "runs"
        self.mdir = Path(self.tmp.name) / "models"
        self.hub = Path(self.tmp.name) / "hub"
        for d in (self.runs, self.mdir, self.hub):
            d.mkdir(parents=True)
        self._env = {k: os.environ.get(k) for k in ("AFTERGLOW_RUNS", "AFTERGLOW_MODELS", "HF_HUB_CACHE")}
        os.environ.update(AFTERGLOW_RUNS=str(self.runs), AFTERGLOW_MODELS=str(self.mdir),
                          HF_HUB_CACHE=str(self.hub))
        self._dl, models.DOWNLOADS = models.DOWNLOADS, model_download.DownloadManager()
        self._cache = dict(models._vision_probe_cache)
        models._vision_probe_cache.update(at=0.0, url=None, value=None)
        self._legacy, models.WHISPER_LEGACY = models.WHISPER_LEGACY, self.mdir / "no-legacy.bin"

    def tearDown(self):
        models.DOWNLOADS = self._dl
        models._vision_probe_cache.update(self._cache)
        models.WHISPER_LEGACY = self._legacy
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def seed_lane(self, cid, files):
        """Weights plus a verified manifest in lane layout (as a download would leave)."""
        c = model_catalog.by_id(cid)
        if c["single_file"]:
            fp = self.mdir / c["files"][0].rsplit("/", 1)[-1]
            fp.write_bytes(files["."])
            man = {"choice": cid, "repo": c["repo"], "revision": "t",
                   "files": [{"path": c["files"][0], "size": len(files["."])}]}
            (self.mdir / (fp.name + ".json")).write_text(json.dumps(man))
            return fp
        d = self.mdir / cid
        for rel, data in files.items():
            fp = d / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(data)
        man = {"choice": cid, "repo": c["repo"], "revision": "t",
               "files": [{"path": rel, "size": len(data)} for rel, data in files.items()]}
        (d / ".afterglow-manifest.json").write_text(json.dumps(man))
        return d

    def seed_hub(self, repo, revision, files):
        d = self.hub / ("models--" + repo.replace("/", "--")) / "snapshots" / revision
        for rel, data in files.items():
            fp = d / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(data)
        return d


class DefaultsTest(Base):
    def test_nothing_saved_keeps_todays_behavior(self):
        self.assertIsNone(models.saved())
        self.assertIsNone(models.speech_choice())
        self.assertIsNone(models.vision_choice())
        self.assertEqual(models.vision_mode(), "none")
        self.assertEqual(models.circle(), "text")
        self.assertTrue(models.keep_loaded("speech"))  # warm like today until explicitly off
        self.assertFalse(models.keep_loaded("vision"))
        self.assertFalse((self.runs / "_settings" / "models.json").exists())
        models.save({"keep_loaded": {"speech": False}})
        self.assertFalse(models.keep_loaded("speech"))

    def test_implicit_speech_is_the_current_default(self):
        with mock.patch.object(models, "_asr_venv_ok", return_value=False):
            app = FakeApp(hw=MAC64)
            pay = models.payload(app)
            # nothing runnable: the Qwen default is the next download
            self.assertEqual(pay["slots"]["speech"]["selected"], "qwen3-asr-1.7b-8bit")
            self.assertTrue(pay["slots"]["speech"]["implicit"])
            self.assertIsNone(models.speech_choice())  # saved stays None: no override
        (self.mdir / "no-legacy.bin").write_bytes(b"legacy")
        with mock.patch.object(models, "_asr_venv_ok", return_value=False):
            self.assertEqual(models.effective_selected("speech"), "whisper-large-v3")

    def test_get_writes_no_files(self):
        app = FakeApp(hw=MAC64)
        before = {p for p in self.tmp and Path(self.tmp.name).rglob("*")}
        models.payload(app)
        models.models_get(app, get("/api/models"))
        after = {p for p in Path(self.tmp.name).rglob("*")}
        self.assertEqual(before, after)


class FitTest(Base):
    def test_mac_fit_and_recommendations(self):
        self.seed_hub("mlx-community/Qwen3-ASR-1.7B-8bit", "r",
                      {"config.json": b"{}", "model.safetensors": b"w"})
        with mock.patch.object(models, "_asr_venv_ok", return_value=True):
            app = FakeApp(hw=MAC64)
            pay = models.payload(app)
            sp = {c["id"]: c for c in pay["slots"]["speech"]["choices"]}
            self.assertEqual(sp["qwen3-asr-1.7b-8bit"]["fit"], "fits")
            self.assertTrue(sp["qwen3-asr-1.7b-8bit"]["recommended"])
            vi = {c["id"]: c for c in pay["slots"]["vision"]["choices"]}
            self.assertEqual(vi["qwen3.5-4b-mac"]["fit"], "fits")
            # remote with no vision: nothing to download, so no recommendation there
            self.assertEqual(pay["slots"]["vision"]["selected"], None)
            mem = pay["memory"]
            self.assertEqual(mem["total_gb"], 48.0)
            self.assertNotIn("agent", [s["id"] for s in mem["segments"]])

    def test_remote_glimmer_counts_zero_but_local_counts_its_file(self):
        remote = FakeApp(hw=NVIDIA)
        self.assertEqual(models.used_local_gb(remote), 0.0)
        gguf = self.mdir / "Glimmer-Q4_K_M.gguf"
        gguf.write_bytes(b"x")
        with open(gguf, "r+b") as f:  # sparse: exact bytes, no disk cost
            f.truncate(16756683904)
        app = FakeApp(hw=MAC64, source="gguf", path=str(gguf),
                      local_server=FakeServer(True, ["llama-server", "-m", str(gguf)]))
        self.assertEqual(models.local_agent_gb(app), 18.0)
        with open(gguf, "r+b") as f:
            f.truncate(19653960832)
        app = FakeApp(hw=MAC64, source="gguf", path=str(gguf))
        self.assertEqual(models.local_agent_gb(app), 20.9)
        app = FakeApp(hw=MAC64, source="gguf", path=str(self.mdir / "missing.gguf"))
        self.assertEqual(models.local_agent_gb(app), 18.0)  # unknown file: the constant

    def test_nvidia_recommends_whisper_and_apple_rows_are_too_big(self):
        app = FakeApp(hw=NVIDIA)
        pay = models.payload(app)
        sp = {c["id"]: c for c in pay["slots"]["speech"]["choices"]}
        self.assertEqual(sp["qwen3-asr-1.7b-8bit"]["fit"], "too_big")
        self.assertEqual(sp["whisper-large-v3-turbo"]["fit"], "fits")
        rec = [c["id"] for c in pay["slots"]["speech"]["choices"] if c["recommended"]]
        self.assertEqual(rec, ["whisper-large-v3"])
        vi = {c["id"]: c for c in pay["slots"]["vision"]["choices"]}
        self.assertEqual(vi["qwen3.5-4b-mac"]["fit"], "too_big")

    def test_remote_unavailable_keeps_zero_cost_fit(self):
        app = FakeApp(hw=MAC64)  # remote, unreachable: available None -> unavailable
        pay = models.payload(app)
        g = next(c for c in pay["slots"]["vision"]["choices"] if c["id"] == "glimmer-mmproj")
        self.assertEqual(g["state"], "unavailable")
        self.assertEqual(g["local_gb"], 0.0)
        self.assertEqual(g["fit"], "fits")  # zero local cost, not a hardware failure

    def test_tight_band_between_fits_and_too_big(self):
        hw = {"memory_gb": 8.0, "apple_silicon": True, "usable_gb": 8.0, "gpus": []}
        app = FakeApp(hw=hw)
        c = model_catalog.by_id("qwen3.5-2b-mac")  # 3 GB on an 8 GB budget
        self.assertEqual(models.fit_for(c, app, hw, 2.0)["fit"], "fits")     # 5 <= 6
        self.assertEqual(models.fit_for(c, app, hw, 4.5)["fit"], "tight")    # 7.5 <= 8
        self.assertEqual(models.fit_for(c, app, hw, 5.5)["fit"], "too_big")  # 8.5 > 8


class VisionOptInTest(Base):
    def test_no_auto_on_when_remote_sees(self):
        srv = PropsServer(vision=True)
        try:
            app = FakeApp(hw=MAC64, url=srv.url)
            self.assertEqual(models.agent_vision(app)["available"], True)
            self.assertIsNone(models.vision_choice())
            self.assertEqual(models.vision_mode(app), "none")
            self.assertEqual(models.circle(app), "text")
            pay = models.payload(app)
            self.assertIsNone(pay["slots"]["vision"]["selected"])
            g = next(c for c in pay["slots"]["vision"]["choices"] if c["id"] == "glimmer-mmproj")
            self.assertEqual(g["state"], "downloaded")  # one press to opt in, nothing to fetch
            self.assertTrue(g["recommended"])
        finally:
            srv.close()

    def test_explicit_none_stays_none(self):
        srv = PropsServer(vision=True)
        try:
            app = FakeApp(hw=MAC64, url=srv.url)
            models.save({"vision": None})
            self.assertEqual(models.vision_mode(app), "none")
        finally:
            srv.close()

    def test_remote_opt_in_needs_no_download(self):
        srv = PropsServer(vision=True)
        try:
            app = FakeApp(hw=MAC64, url=srv.url)
            with mock.patch.object(models, "_notify_configured") as n:
                out = models.use_post(app, post("/api/models/use", {"slot": "vision", "id": "glimmer-mmproj"}))
            n.assert_called_once_with("vision")
            self.assertEqual(out["slots"]["vision"]["selected"], "glimmer-mmproj")
            self.assertEqual(models.vision_mode(app), "glimmer")
        finally:
            srv.close()

    def test_remote_without_vision_refuses(self):
        srv = PropsServer(vision=False)
        try:
            app = FakeApp(hw=MAC64, url=srv.url)
            av = models.agent_vision(app)
            self.assertEqual((av["available"], av["hint"]), (False, "--mmproj <file>"))
            with self.assertRaisesRegex(ValueError, "does not load"):
                models.use_post(app, post("/api/models/use", {"slot": "vision", "id": "glimmer-mmproj"}))
        finally:
            srv.close()

    def test_local_claims_available_only_from_the_command(self):
        fp = self.seed_lane("glimmer-mmproj", {".": b"m"})
        gguf = self.mdir / "G.gguf"
        gguf.write_bytes(b"g")
        stopped = FakeApp(hw=MAC64, source="gguf", path=str(gguf), local_server=FakeServer(False, None))
        av = models.agent_vision(stopped)
        self.assertEqual((av["available"], models.vision_mode(stopped)), (False, "none"))
        models.save({"vision": "glimmer-mmproj"})
        self.assertEqual(models.vision_mode(stopped), "none")  # file alone never confirms
        nocmd = FakeApp(hw=MAC64, source="gguf", path=str(gguf),
                        local_server=FakeServer(True, ["llama-server", "-m", str(gguf)]))
        self.assertFalse(models.agent_vision(nocmd)["available"])
        yes = FakeApp(hw=MAC64, source="gguf", path=str(gguf),
                      local_server=FakeServer(True, ["llama-server", "-m", str(gguf), "--mmproj", str(fp)]))
        self.assertTrue(models.agent_vision(yes)["available"])
        self.assertEqual(models.vision_mode(yes), "glimmer")


class StateTest(Base):
    def test_use_remove_roundtrip(self):
        self.seed_lane("qwen3.5-2b-mac", {"config.json": b"{}", "model.safetensors": b"w"})
        app = FakeApp(hw=MAC64)
        with mock.patch.object(models, "_notify_configured") as n:
            out = models.use_post(app, post("/api/models/use", {"slot": "vision", "id": "qwen3.5-2b-mac"}))
            n.assert_called_once_with("vision")
        self.assertEqual(out["slots"]["vision"]["selected"], "qwen3.5-2b-mac")
        self.assertTrue((self.runs / "_settings" / "models.json").exists())
        with mock.patch.object(models, "_notify_configured") as n:
            out = models.remove_post(app, post("/api/models/remove", {"id": "qwen3.5-2b-mac"}))
            n.assert_called_once_with("vision")
        self.assertIsNone(out["slots"]["vision"]["selected"])
        self.assertFalse((self.mdir / "qwen3.5-2b-mac").exists())

    def test_use_requires_download(self):
        app = FakeApp(hw=MAC64)
        with self.assertRaisesRegex(ValueError, "Download it first"):
            models.use_post(app, post("/api/models/use", {"slot": "vision", "id": "qwen3.5-4b-mac"}))

    def test_keep_loaded_roundtrip_notifies(self):
        app = FakeApp(hw=MAC64)
        with mock.patch.object(models, "_notify_keep_loaded") as n:
            out = models.keep_loaded_post(app, post("/api/models/keep-loaded", {"slot": "speech", "on": True}))
            n.assert_called_once_with("speech", True)
        self.assertTrue(out["slots"]["speech"]["keep_loaded"])
        self.assertTrue(models.keep_loaded("speech"))

    def test_remove_keeps_another_apps_copy(self):
        self.seed_lane("whisper-large-v3-turbo", {".": b"w"})
        (self.mdir / "no-legacy.bin").write_bytes(b"other")
        app = FakeApp(hw=MAC64)
        with mock.patch.object(models, "is_downloaded", return_value=True):
            out = models.remove_post(app, post("/api/models/remove", {"id": "whisper-large-v3-turbo"}))
        self.assertIn("note", out)

    def test_download_validates_and_gates_runtime(self):
        app = FakeApp(hw=MAC64)
        with self.assertRaisesRegex(ValueError, "unknown model"):
            models.download_post(app, post("/api/models/download", {"id": "nope"}))
        with self.assertRaisesRegex(ValueError, "another machine"):
            models.download_post(app, post("/api/models/download", {"id": "glimmer-mmproj"}))
        fake_vlm = mock.Mock()
        fake_vlm.ensure_runtime.return_value = {"ready": False, "line": "Install uv first."}
        # Patch the package attribute: `from . import vlm_runtime` binds it, and in a
        # combined run the real module is already imported, so sys.modules alone would not shadow it.
        with mock.patch("afterglow.vlm_runtime", fake_vlm, create=True):
            with self.assertRaisesRegex(RuntimeError, "Install uv"):
                models.download_post(app, post("/api/models/download", {"id": "qwen3.5-4b-mac"}))
        fake_vlm.ensure_runtime.return_value = {"ready": True, "line": "ready"}
        with mock.patch("afterglow.vlm_runtime", fake_vlm, create=True), \
                mock.patch.object(models.DOWNLOADS, "start", return_value={"state": "downloading"}) as st:
            models.download_post(app, post("/api/models/download", {"id": "qwen3.5-4b-mac"}))
            st.assert_called_once()

    def test_loopback_is_remote_unless_owned(self):
        app = FakeApp(hw=MAC64, url="http://127.0.0.1:1234")
        self.assertFalse(models.agent_local(app))
        self.assertTrue(models.agent_local(FakeApp(hw=MAC64, source="gguf")))

    def test_payload_shape(self):
        pay = models.payload(FakeApp(hw=MAC64))
        self.assertEqual(sorted(pay), ["agent_vision", "circle", "hardware", "memory", "slots", "vision_mode"])
        for slot in ("speech", "vision"):
            self.assertEqual(sorted(pay["slots"][slot]),
                             ["choices", "implicit", "keep_loaded", "saved", "selected"])
            for c in pay["slots"][slot]["choices"]:
                for k in ("state", "fit", "recommended", "progress", "line", "local_gb"):
                    self.assertIn(k, c)
                self.assertIn(c["state"], ("not_downloaded", "downloading", "paused", "downloaded",
                                           "in_use", "unavailable", "failed"))
                self.assertIn(c["fit"], ("fits", "tight", "too_big"))
        self.assertEqual(sorted(pay["agent_vision"]), ["available", "hint", "line", "local"])


class RestartTest(Base):
    def test_glimmer_use_restarts_only_the_owned_server(self):
        fp = self.seed_lane("glimmer-mmproj", {".": b"m"})
        gguf = self.mdir / "G.gguf"
        gguf.write_bytes(b"g")
        use_model = mock.Mock()
        app = FakeApp(hw=MAC64, source="gguf", path=str(gguf),
                      local_server=FakeServer(True, ["llama-server", "-m", str(gguf)]),
                      use_model=use_model)
        with mock.patch.object(models, "_notify_configured"):
            out = models.use_post(app, post("/api/models/use", {"slot": "vision", "id": "glimmer-mmproj"}))
        use_model.assert_called_once_with({"source": "gguf", "path": str(gguf)}, persist=False)
        self.assertTrue(out.get("restarted"))
        # already matching: no restart
        use_model.reset_mock()
        app2 = FakeApp(hw=MAC64, source="gguf", path=str(gguf),
                       local_server=FakeServer(True, ["llama-server", "-m", str(gguf),
                                                      "--mmproj", str(fp)]),
                       use_model=use_model)
        with mock.patch.object(models, "_notify_configured"):
            out = models.use_post(app2, post("/api/models/use", {"slot": "vision", "id": "glimmer-mmproj"}))
        use_model.assert_not_called()
        self.assertNotIn("restarted", out)

    def test_nondefault_port_and_adapter_preserved(self):
        import struct
        fp = self.seed_lane("glimmer-mmproj", {".": b"m"})
        gguf = self.mdir / "G.gguf"
        gguf.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, 0))
        start = mock.Mock(return_value={"url": "http://127.0.0.1:18699"})
        server = mock.Mock()
        server.running.return_value = True
        server.cmd = ["llama-server", "-m", str(gguf), "--port", "18699",
                      "--lora-scaled", "/n/a.gguf:0"]
        server.start = start
        app = FakeApp(hw=MAC64, source="gguf", path=str(gguf), local_server=server)
        with mock.patch.object(models, "_notify_configured"):
            out = models.use_post(app, post("/api/models/use", {"slot": "vision", "id": "glimmer-mmproj"}))
        start.assert_called_once_with(str(gguf), 18699, "/n/a.gguf")
        self.assertTrue(out.get("restarted"))
        self.assertTrue(app.model_source.get("starting"))

    def test_remote_use_never_restarts(self):
        srv = PropsServer(vision=True)
        try:
            use_model = mock.Mock()
            app = FakeApp(hw=MAC64, url=srv.url, use_model=use_model)
            with mock.patch.object(models, "_notify_configured"):
                models.use_post(app, post("/api/models/use", {"slot": "vision", "id": "glimmer-mmproj"}))
            use_model.assert_not_called()
        finally:
            srv.close()


class MmprojHookTest(Base):
    def test_next_start_honors_the_saved_projector(self):
        fp = self.seed_lane("glimmer-mmproj", {".": b"m"})
        models.save({"vision": "glimmer-mmproj"})
        cmd = modelsetup.local_server_command("/m/g.gguf")
        self.assertIn("--mmproj", cmd)
        self.assertEqual(cmd[cmd.index("--mmproj") + 1], str(fp))
        models.save({"vision": None})
        self.assertNotIn("--mmproj", modelsetup.local_server_command("/m/g.gguf"))


if __name__ == "__main__":
    unittest.main()
