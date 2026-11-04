"""Settings -> Model: the GGUF header reader, the server probe (llama.cpp vs a plain OpenAI-compatible server), the
chat-only client mode, and the saved choice. Fake servers in-process; no model and no network."""
import json
import os
import struct
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import modelsetup  # noqa: E402
from afterglow.llm import GlimmerClient  # noqa: E402


def gguf_bytes(meta):
    """A tiny GGUF v3 file: the header, the given metadata, a token array (skipped by the reader), no tensors."""
    def s(x):
        b = x.encode()
        return struct.pack("<Q", len(b)) + b
    out = b"GGUF" + struct.pack("<IQQ", 3, 0, len(meta) + 1)
    for k, v in meta.items():
        if isinstance(v, str):
            out += s(k) + struct.pack("<I", 8) + s(v)
        else:
            out += s(k) + struct.pack("<I", 4) + struct.pack("<I", v)
    toks = [s(t) for t in ("<s>", "hello", "world")]
    out += s("tokenizer.ggml.tokens") + struct.pack("<IIQ", 9, 8, len(toks)) + b"".join(toks)
    return out


class FakeServer:
    """kind "llama.cpp" answers /props, /health, /lora-adapters; "lmstudio" answers /api/v0/models; both answer
    /v1/models and a streamed /v1/chat/completions, and record every chat body."""

    def __init__(self, kind, models=("muse-glimmer-30b",)):
        self.kind, self.models, self.bodies = kind, list(models), []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, code=200):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                if self.path == "/v1/models":
                    return self._json({"data": [{"id": m} for m in fake.models]})
                if fake.kind == "llama.cpp" and self.path == "/props":
                    return self._json({"default_generation_settings": {}, "model_path": "/m/Muse-Glimmer-30B.gguf"})
                if fake.kind == "llama.cpp" and self.path == "/health":
                    return self._json({"status": "ok"})
                if fake.kind == "llama.cpp" and self.path == "/lora-adapters":
                    return self._json([{"id": 0, "path": "/n/adapter.gguf", "scale": 0}])
                if fake.kind == "lmstudio" and self.path == "/api/v0/models":
                    return self._json({"data": []})
                return self._json({"error": "not found"}, 404)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n))
                fake.bodies.append(body)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for piece in ("Hel", "lo."):
                    ev = {"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                ev = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                self.wfile.write(f"data: {json.dumps(ev)}\n\ndata: [DONE]\n\n".encode())

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


class ModelSetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AFTERGLOW_RUNS"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("AFTERGLOW_RUNS", None)
        self.tmp.cleanup()

    def test_gguf_header_is_read_without_the_tensors(self):
        f = Path(self.tmp.name) / "Muse-Glimmer-30B-Q4_K_M.gguf"
        f.write_bytes(gguf_bytes({"general.architecture": "glimmer", "general.name": "Muse Glimmer 30B",
                                  "general.file_type": 15, "glimmer.context_length": 131072}))
        info = modelsetup.gguf_info(f)
        self.assertTrue(info["ok"])
        self.assertEqual((info["name"], info["arch"], info["quant"], info["context"]),
                         ("Muse Glimmer 30B", "glimmer", "Q4_K_M", 131072))
        self.assertTrue(info["glimmer"])
        bad = Path(self.tmp.name) / "notes.txt"
        bad.write_text("hello")
        self.assertFalse(modelsetup.gguf_info(bad)["ok"])
        self.assertFalse(modelsetup.gguf_info(Path(self.tmp.name) / "missing.gguf")["ok"])

    def test_probe_tells_llama_cpp_from_a_chat_only_server(self):
        llama, lms = FakeServer("llama.cpp"), FakeServer("lmstudio", models=("qwen-3-8b",))
        try:
            a = modelsetup.probe(llama.url + "/v1/")
            self.assertEqual((a["kind"], a["chat"], a["nights"], a["adapters"], a["glimmer"]),
                             ("llama.cpp", True, True, 1, True))
            b = modelsetup.probe(lms.url.removeprefix("http://"))
            self.assertEqual((b["kind"], b["chat"], b["nights"], b["glimmer"], b["model"]),
                             ("lmstudio", True, False, False, "qwen-3-8b"))
            self.assertIn("cannot use what your agent learns each night", " ".join(b["lines"]))
            self.assertFalse(modelsetup.probe("http://127.0.0.1:9")["chat"])
        finally:
            llama.close()
            lms.close()

    def test_a_chat_only_server_gets_no_slot_or_adapter_fields(self):
        lms = FakeServer("lmstudio")
        try:
            c = GlimmerClient("http://127.0.0.1:9", slot=1)
            c.use_server(lms.url, "lmstudio", "muse-glimmer-30b")
            self.assertTrue(c.health())
            self.assertEqual(c.adapters(), [])
            done = c.chat([{"role": "user", "content": "hi"}])
            self.assertEqual(done["message"]["content"], "Hello.")
            body = lms.bodies[-1]
            self.assertEqual(body["model"], "muse-glimmer-30b")
            for k in ("id_slot", "cache_prompt", "chat_template_kwargs", "lora"):
                self.assertNotIn(k, body)
        finally:
            lms.close()

    def test_the_choice_is_saved_under_settings(self):
        self.assertIsNone(modelsetup.saved())
        modelsetup.save({"source": "server", "url": "http://127.0.0.1:1234", "kind": "lmstudio"})
        doc = modelsetup.saved()
        self.assertEqual(doc["url"], "http://127.0.0.1:1234")
        self.assertTrue((Path(self.tmp.name) / "_settings" / "model.json").exists())

    def test_hardware_says_what_fits_in_plain_words(self):
        hw = modelsetup.hardware()
        for k in ("serve", "nights", "serve_line", "nights_line", "memory_line"):
            self.assertIn(k, hw)
        self.assertIn("24 GB", hw["nights_line"])

    def test_local_server_command_uses_the_serving_flags(self):
        cmd = modelsetup.local_server_command("/m/g.gguf", 18650, adapter="/n/adapter.gguf")
        self.assertEqual(cmd[1:3], ["-m", "/m/g.gguf"])
        for flag in ("-ngl", "-fa", "--jinja", "--lora-init-without-apply"):
            self.assertIn(flag, cmd)
        self.assertIn("/n/adapter.gguf:0", cmd)


if __name__ == "__main__":
    unittest.main()
