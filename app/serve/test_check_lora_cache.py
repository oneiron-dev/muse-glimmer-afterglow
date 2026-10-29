#!/usr/bin/env python3
"""Tests for check_lora_cache.py against a stand-in server (stdlib only; no GPU): cached-vs-uncached text at scale 1
is reported, never a failure; a change of scales that keeps the cached prompt still fails.

  python3 app/serve/test_check_lora_cache.py -v
"""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_lora_cache  # noqa: E402


class FakeServer:
    """llama-server's /completion with one slot: the cached prompt is dropped when the scales change; at scale 1
    the text depends on whether a cached prefix was reused (reuse_text) when vary is set."""

    def __init__(self, vary=False, keep_cache_on_change=False):
        self.vary, self.keep = vary, keep_cache_on_change
        self.last = None

    def __call__(self, endpoint, path, body=None, timeout=120):
        if path == "/lora-adapters":
            return [{"id": 0, "path": "/a/w-d3.gguf", "scale": 0.0}]
        if path == "/apply-template":
            return {"prompt": "<|start|>user<|message|>q<|end|><|start|>assistant"}
        if path.startswith("/slots/"):
            self.last = None
            return {}
        scales = tuple(x["scale"] for x in body.get("lora") or [])
        scale = scales[0] if scales else 0.0
        reused = body["cache_prompt"] and self.last is not None and (self.last == scale or self.keep)
        self.last = scale
        text = "base answer" if scale == 0 else ("adapter answer, reused" if self.vary and reused else "adapter answer")
        return {"content": text, "timings": {"cache_n": 70 if reused else 0, "prompt_n": 5 if reused else 75}}


def run(server):
    out = io.StringIO()
    with mock.patch.object(check_lora_cache, "call", server), mock.patch.object(sys, "argv", ["x"]), \
            contextlib.redirect_stdout(out):
        code = check_lora_cache.main()
    return code, out.getvalue()


class CheckLoraCacheTest(unittest.TestCase):
    def test_equal_answers_pass_and_are_reported(self):
        code, out = run(FakeServer())
        self.assertEqual(code, 0)
        self.assertIn("REPORT  cached and uncached answers at scale 1, adapter 0: 0 of 2 differ", out)

    def test_prompt_reuse_numerics_are_reported_not_failed(self):
        code, out = run(FakeServer(vary=True))
        self.assertEqual(code, 0)
        self.assertIn("adapter 0: 1 of 2 differ from the first", out)
        self.assertNotIn("FAIL", out)

    def test_a_scale_change_that_keeps_the_cache_still_fails(self):
        code, out = run(FakeServer(keep_cache_on_change=True))
        self.assertEqual(code, 1)
        self.assertIn("FAIL  scale 0 -> 1 drops the cached prompt", out)


if __name__ == "__main__":
    unittest.main()
