"""Explainer backend (lane L6): OCR contract, v1 spec validation and repair, routes, temporary chat.

All model calls use fake clients; no live server is needed.
"""
import base64
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import explainer, routes, threads  # noqa: E402
from afterglow.llm import GlimmerClient  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PNG_B64 = base64.b64encode(PNG).decode("ascii")


class FakeClient:
    """A GlimmerClient stand-in: canned spec text for create(), canned chunks for ask()."""

    def __init__(self, spec_text="", chunks=()):
        self.spec_text, self.chunks = spec_text, list(chunks)
        self.chat_calls, self.stream_calls = [], []

    def chat(self, messages, tools=None, **kw):
        self.chat_calls.append({"messages": messages, "tools": tools, **kw})
        return {"message": {"role": "assistant", "content": self.spec_text}, "finish_reason": "stop"}

    def stream_chat(self, messages, tools=None, **kw):
        self.stream_calls.append({"messages": messages, "tools": tools, **kw})
        for c in self.chunks:
            yield {"type": "content", "text": c}


class FakeSession:
    def __init__(self, world="w-test", day=1, history=()):
        self.world, self.day = world, int(day)
        self._history = list(history)

    def history(self):
        return self._history


class FakeApp:
    def __init__(self, client=None, history=()):
        self.client = client or FakeClient()
        self.session = FakeSession(history=history)
        self.lock = threading.Lock()


def req_post(body):
    return routes.Request("POST", "/api/x", "", {"Content-Type": "application/json"},
                          json.dumps(body).encode())


def req_get(query):
    return routes.Request("GET", "/api/x", query, {}, None)


def boxes(n=3):
    return [{"text": f"line {i} of the screen",
             "box": [(0.05 * i) % 0.8, (0.1 * i) % 0.8, 0.15 + 0.01 * (i % 5), 0.08]} for i in range(n)]


class OcrTest(unittest.TestCase):
    def test_fraction_list_keeps_stable_ids(self):
        o = explainer.normalize_ocr(boxes(2))
        self.assertEqual([b["id"] for b in o["boxes"]], ["b0", "b1"])
        self.assertEqual(o["boxes"][0]["box"], [0.0, 0.0, 0.15, 0.08])
        self.assertEqual(o["dropped"], 0)
        self.assertIsNone(o["width"])

    def test_pixel_envelope_converts_to_fractions(self):
        o = explainer.normalize_ocr({"width": 400, "height": 200,
                                      "boxes": [{"text": "Model", "box": [40, 20, 200, 40]}]})
        self.assertEqual(o["boxes"][0]["box"], [0.1, 0.1, 0.5, 0.2])
        self.assertEqual((o["width"], o["height"]), (400, 200))

    def test_fraction_envelope_with_dims_stays_fractional(self):
        o = explainer.normalize_ocr({"width": 400, "height": 200,
                                      "boxes": [{"text": "Model", "box": [0.1, 0.1, 0.5, 0.2]}]})
        self.assertEqual(o["boxes"][0]["box"], [0.1, 0.1, 0.5, 0.2])

    def test_malformed_boxes_drop_gracefully_and_cap_applies(self):
        o = explainer.normalize_ocr([{"text": "ok", "box": [0, 0, 0.5, 0.5]},
                                      "a string", None, 42, {"text": "no box"},
                                      {"text": "", "box": [0, 0, 0.1, 0.1]},
                                      {"text": "bad quad", "box": [0, 0, 1]},
                                      {"text": "bools", "box": [True, 0, 0.1, 0.1]},
                                      {"text": "flat", "box": [0.9, 0.9, 0.0, 0.1]},
                                      {"str": "alias", "bbox": [0, 0, 0.2, 0.2]}]
                                     + boxes(60))
        ids = [b["id"] for b in o["boxes"]]
        self.assertEqual(len(ids), explainer.OCR_MAX_BOXES)
        self.assertEqual(ids[:2], ["b0", "b1"])
        self.assertEqual(o["boxes"][1]["text"], "alias")
        self.assertGreater(o["dropped"], 60 - explainer.OCR_MAX_BOXES)

    def test_clamps_to_unit_square(self):
        o = explainer.normalize_ocr([{"text": "big", "box": [-0.5, -0.5, 3.0, 3.0]}])
        self.assertEqual(o["boxes"][0]["box"], [0.0, 0.0, 1.0, 1.0])

    def test_single_bare_box_and_bad_root(self):
        o = explainer.normalize_ocr({"text": "one", "box": [0, 0, 0.2, 0.2]})
        self.assertEqual([b["id"] for b in o["boxes"]], ["b0"])
        with self.assertRaises(ValueError):
            explainer.normalize_ocr("just a string")
        with self.assertRaises(ValueError):
            explainer.normalize_ocr({"width": 10, "boxes": "nope"})
        self.assertEqual(explainer.normalize_ocr(None)["boxes"], [])

    def test_ax_normalizes_string_and_dict(self):
        self.assertIsNone(explainer.normalize_ax(None))
        self.assertIsNone(explainer.normalize_ax("  "))
        self.assertEqual(explainer.normalize_ax("  Save button "), "Save button")
        self.assertEqual(explainer.normalize_ax({"role": "button", "name": "Save"}), "button Save")


class PromptTest(unittest.TestCase):
    def test_facts_anchors_and_no_crop_bytes(self):
        ocr = explainer.normalize_ocr(boxes(2))
        facts = explainer.build_facts("what is this?", ocr, "button Save")
        anchors = explainer.build_anchors(ocr)
        msgs = explainer.build_messages("what is this?", facts, anchors, "Afterglow", "Q: hi / A: yo",
                                        len(ocr["boxes"]), 2)
        self.assertEqual(msgs[0]["role"], "system")
        user = msgs[1]["content"]
        for needle in ('"q": "what is this?"', '"f0":', '"ax": "button Save', "App: Afterglow",
                        "Earlier reply: Q: hi / A: yo", "Return ONE JSON object only."):
            self.assertIn(needle, user)
        self.assertIn('"b0": "OCR:', user)
        self.assertNotIn('"ax": "UI:', user)  # ax is a fact, never a pin anchor
        self.assertNotIn(PNG_B64[:20], json.dumps(msgs))

    def test_no_anchors_says_so(self):
        ocr = explainer.normalize_ocr(None)
        msgs = explainer.build_messages("q", explainer.build_facts("q", ocr), explainer.build_anchors(ocr))
        self.assertIn("do not use screenshot", msgs[1]["content"])


class SpecTest(unittest.TestCase):
    def setUp(self):
        self.ocr = explainer.normalize_ocr(boxes(4))
        self.facts = explainer.build_facts("what is this screen for?", self.ocr)
        self.anchors = set(explainer.build_anchors(self.ocr))

    def check(self, raw, question="what is this screen for?"):
        return explainer.validate_and_repair(raw, self.ocr, facts=self.facts, anchors=self.anchors,
                                             question=question)

    def item(self, i="a", label="Label", detail="Detail words.", refs=("f0",)):
        return {"id": i, "label": label, "detail": detail, "refs": list(refs)}

    def test_valid_specs_pass_through(self):
        bodies = [
            {"type": "screenshot",
             "pins": [{"anchor": "b0", "item": self.item()}, {"anchor": "b3", "item": self.item("b")}]},
            {"type": "flow", "steps": [self.item("a"), self.item("b"), self.item("c")]},
            {"type": "cycle", "steps": [self.item("a"), self.item("b")],
             "outcomes": [{"when": "all pass", "item": self.item("c"), "resume": "repeat"},
                          {"when": "one fails", "item": self.item("d"), "resume": "stop"}]},
            {"type": "parts", "whole": self.item("w"), "parts": [self.item("a"), self.item("b")]},
            {"type": "compare", "left": "This", "right": "That",
             "rows": [{"id": "r1", "aspect": "Cost", "left": "low", "right": "high", "refs": ["f0"]}]},
            {"type": "text", "text": "Which part should I explain?"},
        ]
        for body in bodies:
            spec, info = self.check({"v": 1, "title": "T", "body": body})
            self.assertFalse(info["fallback"], body["type"])
            self.assertFalse(info["repaired"], (body["type"], info["errors"]))
            self.assertEqual(spec["body"]["type"], body["type"])

    def test_malformed_typed_objects_drop_parts_not_spec(self):
        spec, info = self.check({"v": 1, "title": "T", "body": {
            "type": "screenshot",
            "pins": [{"anchor": "b0", "item": self.item()},
                     {"anchor": "b9", "item": self.item("x")},  # unknown anchor
                     {"item": self.item("y")},  # missing anchor
                     {"anchor": "b1", "item": "not an object"},
                     "not an object at all", None]}})
        self.assertFalse(info["fallback"])
        self.assertTrue(info["repaired"])
        self.assertEqual([p["anchor"] for p in spec["body"]["pins"]], ["b0"])
        # wrong-typed fields and unknown keys are dropped with notes, never fatal
        spec, info = self.check({"v": "2", "title": "T", "zzz": 1, "body": {
            "type": "flow", "steps": [{"id": "a", "label": "A", "detail": "", "refs": ["f0"], "style": "x"},
                                       {"id": "b", "label": "B", "detail": "", "refs": ["f1"]}],
            "edges": [{"from": "a", "to": "ghost"}]}})
        self.assertFalse(info["fallback"])
        self.assertEqual([s["id"] for s in spec["body"]["steps"]], ["a", "b"])
        self.assertNotIn("edges", spec["body"])
        self.assertIn("v: must be 1", info["errors"])

    def test_lengths_ids_refs_repaired(self):
        spec, info = self.check({"v": 1, "title": "x" * 100, "body": {
            "type": "flow", "steps": [
                {"id": "Bad ID!", "label": "L" * 50, "detail": "D" * 200, "refs": ["nope"]},
                {"id": "bad-id", "label": "", "detail": "", "refs": []},
                {"id": "c", "label": "C", "detail": "", "refs": ["f0", "f0"]}]}})
        self.assertFalse(info["fallback"])
        steps = spec["body"]["steps"]
        self.assertEqual(len(spec["title"]), explainer.TITLE_MAX)
        self.assertEqual([s["id"] for s in steps], ["bad-id", "bad-id-2", "c"])
        self.assertEqual(len(steps[0]["label"]), explainer.LABEL_MAX)
        self.assertEqual(len(steps[0]["detail"]), explainer.DETAIL_MAX)
        self.assertEqual(steps[0]["refs"], ["q"])
        self.assertEqual(steps[2]["refs"], ["f0"])

    def test_empty_label_falls_back_to_a_short_slice(self):
        spec, info = self.check({"v": 1, "title": "T", "body": {
            "type": "flow", "steps": [
                {"id": "a", "label": "", "detail": "D" * 100, "refs": ["f0"]},
                {"id": "b", "label": "B", "detail": "", "refs": ["f0"]}]}})
        self.assertFalse(info["fallback"])
        self.assertLessEqual(len(spec["body"]["steps"][0]["label"]), explainer.LABEL_MAX)

    def test_ax_is_never_a_pin_anchor(self):
        spec, info = self.check({"v": 1, "title": "T", "body": {
            "type": "screenshot", "pins": [{"anchor": "ax", "item": self.item()}]}})
        self.assertTrue(info["fallback"])  # the ax pin drops; nothing usable remains

    def test_legacy_mistyped_lists_never_throw(self):
        for raw in [{"title": "T", "template": "pins", "pins": {"box": 0}},
                    {"title": "T", "template": "pins", "pins": 7},
                    {"title": "T", "template": "flow", "nodes": "abc", "steps": {"s": 1}},
                    {"title": "T", "template": "cycle", "nodes": 3, "outcomes": "x"},
                    {"title": "T", "template": "parts", "nodes": None},
                    {"title": "T", "template": "compare", "columns": 9},
                    {"title": "T", "template": "compare",
                     "columns": [{"title": "A", "points": {"p": 1}}, {"title": "B", "points": 5}]}]:
            spec, info = self.check(raw)  # must not raise
            self.assertEqual(spec["v"], 1)
            self.assertTrue(spec["title"])
            self.assertIn(spec["body"]["type"], explainer.TYPES)

    def test_counts_capped_and_cycle_guarded(self):
        spec, info = self.check({"v": 1, "title": "T", "body": {
            "type": "cycle", "steps": [self.item(f"s{i}") for i in range(6)],
            "outcomes": [{"when": f"w{i}", "item": self.item(f"o{i}"), "resume": "maybe"} for i in range(4)]}})
        self.assertFalse(info["fallback"])
        body = spec["body"]
        self.assertLessEqual(len(body["steps"]) + len(body["outcomes"]), 6)
        self.assertEqual(len(body["outcomes"]), 2)
        self.assertTrue(all(o["resume"] == "repeat" for o in body["outcomes"]))

    def test_unusable_specs_fall_back_and_never_empty(self):
        for raw in ["not json {{{", None, 42, [], {"v": 1}, {"v": 1, "title": "T", "body": {"type": "mystery"}},
                    {"v": 1, "title": "T", "body": {"type": "flow", "steps": [self.item()]}},
                    {"v": 1, "title": "T", "body": {"type": "screenshot", "pins": []}},
                    {"v": 1, "title": "T", "body": {"type": "text", "text": "  "}},
                    "```json\n{not json}\n```"]:
            spec, info = self.check(raw)
            self.assertTrue(info["fallback"], repr(raw)[:60])
            self.assertEqual(spec["v"], 1)
            self.assertTrue(spec["title"])
            self.assertEqual(spec["body"]["type"], "screenshot")
            self.assertTrue(1 <= len(spec["body"]["pins"]) <= 3)
        # fallback pins are the largest boxes
        spec, _ = self.check("{{{")
        areas = [b["box"][2] * b["box"][3] for b in self.ocr["boxes"]]
        biggest = max(range(len(areas)), key=areas.__getitem__)
        self.assertEqual(spec["body"]["pins"][0]["anchor"], f"b{biggest}")

    def test_flat_ocr_boxes_from_the_fn_helper_are_read(self):
        from afterglow.explainer import normalize_ocr
        got = normalize_ocr([{"text": "Model", "x": 0.05, "y": 0.04, "w": 0.2, "h": 0.04}])
        self.assertEqual(got["boxes"][0]["box"], [0.05, 0.04, 0.2, 0.04])

    def test_no_ocr_falls_back_to_text(self):
        spec, info = explainer.validate_and_repair("{{{", None, question="what is this?")
        self.assertTrue(info["fallback"])
        self.assertEqual(spec["body"]["type"], "text")
        self.assertLessEqual(len(spec["body"]["text"]), explainer.TEXT_MAX)

    def test_legacy_template_shapes_convert(self):
        spec, info = self.check({"title": "T", "template": "pins",
                                 "pins": [{"box": 0, "label": "A", "note": "note a"},
                                          {"box": 99, "label": "bad", "note": "x"}]})
        self.assertFalse(info["fallback"])
        self.assertEqual(spec["body"]["type"], "screenshot")
        self.assertEqual(spec["body"]["pins"][0]["anchor"], "b0")
        spec, info = self.check({"title": "T", "template": "flow",
                                 "nodes": [{"id": "a", "label": "One"}, {"id": "b", "label": "Two"}],
                                 "edges": [{"from": "a", "to": "ghost"}]})
        self.assertFalse(info["fallback"])
        self.assertEqual([s["label"] for s in spec["body"]["steps"]], ["One", "Two"])
        spec, info = self.check({"title": "T", "template": "compare",
                                 "columns": [{"title": "A", "points": ["a1", "a2"]},
                                             {"title": "B", "points": ["b1"]}]})
        self.assertFalse(info["fallback"])
        self.assertEqual((spec["body"]["left"], len(spec["body"]["rows"])), ("A", 2))
        spec, info = self.check({"title": "T", "template": "unknown-thing"})
        self.assertTrue(info["fallback"])

    def test_fenced_json_parses(self):
        spec, info = self.check('```json\n{"v": 1, "title": "T", "body": {"type": "text", "text": "hi"}}'
                                '\n```')
        self.assertFalse(info["fallback"])
        self.assertEqual(spec["body"]["text"], "hi")


class LlmHookTest(unittest.TestCase):
    def test_response_format_defaults_preserved(self):
        c = GlimmerClient(endpoint="http://127.0.0.1:9")
        c.kind = "openai"  # chat only: no adapter lookups
        msgs = [{"role": "user", "content": "hi"}]
        before = json.dumps(c.request_body(msgs))
        self.assertNotIn("response_format", before)
        body = c.request_body(msgs, response_format=explainer.RESPONSE_FORMAT)
        self.assertEqual(body.pop("response_format"), explainer.RESPONSE_FORMAT)
        self.assertEqual(json.dumps(body), before)


class RoutesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AFTERGLOW_RUNS"] = self.tmp.name
        self.spec = {"v": 1, "title": "Model screen",
                     "body": {"type": "screenshot", "pins": [
                         {"anchor": "b0", "item": {"id": "model", "label": "Model in use",
                                                  "detail": "Muse Glimmer 30B", "refs": ["f0"]}}]}}

    def tearDown(self):
        os.environ.pop("AFTERGLOW_RUNS", None)
        self.tmp.cleanup()

    def app(self, **kw):
        kw.setdefault("spec_text", json.dumps(self.spec))
        client = FakeClient(spec_text=kw.pop("spec_text"), chunks=kw.pop("chunks", ["Hello", " there"]))
        return FakeApp(client=client, history=kw.pop("history", ()))

    def test_create_get_crop_round_trip(self):
        app = self.app()
        out = explainer.post_explain(app, req_post({"question": "what is this screen for?",
                                                    "crop_png_b64": PNG_B64, "ocr": boxes(2),
                                                    "ax": {"role": "window", "name": "Settings"},
                                                    "app_name": "Afterglow"}))
        self.assertEqual(out, {"id": "t1", "url": "/explain?id=t1"})
        # exactly one model call, carrying the schema hook and low reasoning
        self.assertEqual(len(app.client.chat_calls), 1)
        call = app.client.chat_calls[0]
        self.assertEqual(call["response_format"], explainer.RESPONSE_FORMAT)
        self.assertEqual(call["reasoning"], "low")
        # thread files only: index, one jsonl, one crop
        day = Path(self.tmp.name) / "w-test" / "1" / "threads"
        self.assertEqual(sorted(p.name for p in day.iterdir()), ["index.json", "t1-crop.png", "t1.jsonl"])
        self.assertEqual((day / "t1-crop.png").read_bytes(), PNG)
        entry = threads.get("w-test", 1, "t1")
        self.assertEqual(entry["kind"], "explain")
        self.assertEqual([r["role"] for r in entry["records"]], ["user", "assistant"])
        self.assertEqual(entry["records"][1]["spec"], self.spec)
        # GET serves the question, spec, normalized OCR and crop link
        got = explainer.get_explain(app, req_get("id=t1"))
        self.assertEqual(got["id"], "t1")
        self.assertEqual(got["question"], "what is this screen for?")
        self.assertEqual(got["title"], "Model screen")
        self.assertEqual(got["spec"], self.spec)
        self.assertEqual(got["crop_url"], "/api/explain/crop?id=t1")
        self.assertTrue(got["has_crop"])
        self.assertEqual([b["id"] for b in got["ocr"]["boxes"]], ["b0", "b1"])
        self.assertEqual(got["ax"], "window Settings")
        self.assertEqual(got["app_name"], "Afterglow")
        self.assertEqual(got["source"], "ambient")
        self.assertIsNone(got["from_turn"])
        self.assertEqual(len(got["records"]), 2)
        # crop is JSON with a data URL (raw PNG is blocked by the host interface)
        crop = explainer.get_crop(app, req_get("id=t1"))
        self.assertEqual(crop["id"], "t1")
        self.assertTrue(crop["data_url"].startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(crop["data_url"].split(",", 1)[1]), PNG)

    def test_from_turn_chat_source_and_no_crop(self):
        hist = [{"turn": 3, "role": "user", "content": "which model is this?"},
                {"turn": 3, "role": "assistant", "content": "Muse Glimmer 30B."}]
        app = self.app(history=hist)
        out = explainer.post_explain(app, req_post({"question": "draw that reply", "from_turn": 3,
                                                    "source": "chat"}))
        self.assertEqual(out["id"], "t1")
        entry = threads.get("w-test", 1, "t1")
        self.assertEqual(entry["at_turn"], 3)
        self.assertEqual(entry["meta"]["source"], "chat")
        user_msg = app.client.chat_calls[0]["messages"][1]["content"]
        self.assertIn("Earlier reply: Q: which model is this? / A: Muse Glimmer 30B.", user_msg)
        got = explainer.get_explain(app, req_get("id=t1"))
        self.assertEqual(got["from_turn"], 3)
        self.assertIsNone(got["crop_url"])
        self.assertFalse(got["has_crop"])
        self.assertEqual(got["spec"]["body"]["type"], "text")  # no OCR: model spec unusable -> text
        body, status = explainer.get_crop(app, req_get("id=t1"))
        self.assertEqual((body, status), ({"error": "no crop saved for t1"}, 404))

    def test_bad_spec_from_model_falls_back_without_a_second_call(self):
        app = self.app(spec_text='{"v": 1, "title": "T", "body": {"type": "flow", "steps": []}}')
        out = explainer.post_explain(app, req_post({"question": "what?", "ocr": boxes(4)}))
        self.assertEqual(len(app.client.chat_calls), 1)  # one call only; repair is local
        got = explainer.get_explain(app, req_get(f"id={out['id']}"))
        self.assertEqual(got["spec"]["body"]["type"], "screenshot")
        self.assertEqual(len(got["spec"]["body"]["pins"]), 3)
        self.assertTrue(got["records"][1]["repaired"])

    def test_failed_model_call_still_stores_the_fallback_figure(self):
        class Down(FakeClient):
            def chat(self, messages, tools=None, **kw):
                raise ConnectionError("server is down")
        app = FakeApp(client=Down())
        out = explainer.post_explain(app, req_post({"question": "what is this?", "ocr": boxes(2)}))
        self.assertEqual(out, {"id": "t1", "url": "/explain?id=t1"})
        got = explainer.get_explain(app, req_get("id=t1"))
        self.assertIsNotNone(got["spec"])
        self.assertEqual(got["spec"]["body"]["type"], "screenshot")
        self.assertTrue(any("model call failed" in e for e in got["records"][1]["errors"]))

    def test_get_on_a_spec_less_thread_serves_a_fallback_not_null(self):
        app = self.app()
        threads.create("w-test", 1, "explain", "what is this?", meta={"source": "ambient"})
        threads.append("w-test", 1, "t1", {"role": "user", "content": "what is this?",
                                           "ocr": explainer.normalize_ocr(boxes(1))})
        got = explainer.get_explain(app, req_get("id=t1"))
        self.assertIsNotNone(got["spec"])
        self.assertEqual(got["spec"]["body"]["type"], "screenshot")
        self.assertTrue(got["title"])

    def test_bad_requests_are_refused(self):
        app = self.app()
        with self.assertRaises(ValueError):
            explainer.post_explain(app, req_post({"question": "  "}))
        with self.assertRaises(ValueError):
            explainer.post_explain(app, req_post({"question": "q", "source": "popup"}))
        with self.assertRaises(ValueError):
            explainer.post_explain(app, req_post({"question": "q", "crop_png_b64": "!!!not-base64!!!"}))
        with self.assertRaises(ValueError):
            explainer.post_explain(app, req_post({"question": "q", "crop_png_b64": base64.b64encode(b"GIF89a").decode()}))
        with self.assertRaises(ValueError):
            explainer.post_explain(app, req_post({"question": "q", "from_turn": "soon"}))
        with self.assertRaises(ValueError):
            explainer.get_explain(app, req_get(""))
        with self.assertRaises(ValueError):
            explainer.get_explain(app, req_get("id=t9"))
        threads.create("w-test", 1, "note", "not an explainer")
        with self.assertRaises(ValueError):
            explainer.get_explain(app, req_get("id=t1"))
        with self.assertRaises(ValueError):
            explainer.post_ask(app, req_post({"id": "t1", "text": "  "}))

    def test_busy_slot_is_409(self):
        app = self.app()
        app.lock.acquire()
        try:
            with self.assertRaises(RuntimeError):
                explainer.create(app, "what?")
        finally:
            app.lock.release()
        self.assertEqual(explainer.create(app, "what?")["id"], "t1")

    def test_ask_streams_content_then_done_and_keeps_spec(self):
        app = self.app()
        tid = explainer.post_explain(app, req_post({"question": "what is this?", "ocr": boxes(1)}))["id"]
        st = explainer.post_ask(app, req_post({"id": tid, "text": "and the second line?"}))
        self.assertIsInstance(st, routes.Stream)
        self.assertTrue(st.lock)  # the shared slot stays locked while the follow-up streams
        events = list(st.events)
        self.assertEqual([e["type"] for e in events], ["content", "content", "done"])
        self.assertEqual("".join(e.get("text", "") for e in events[:2]), "Hello there")
        self.assertEqual(events[-1], {"type": "done", "id": tid})
        recs = threads.get("w-test", 1, tid)["records"]
        self.assertEqual([r["role"] for r in recs], ["user", "assistant", "user", "assistant"])
        self.assertEqual(recs[0]["content"], "what is this?")  # question preserved
        self.assertEqual(recs[1]["spec"], self.spec)  # created spec preserved
        self.assertEqual(recs[3]["content"], "Hello there")

    def test_ask_unknown_thread_is_refused_before_streaming(self):
        app = self.app()
        with self.assertRaises(ValueError):
            explainer.post_ask(app, req_post({"id": "t9", "text": "hi"}))
        self.assertEqual(app.client.stream_calls, [])

    def test_routes_are_registered(self):
        t = routes.table()
        for key in [("POST", "/api/explain"), ("GET", "/api/explain"), ("GET", "/api/explain/crop"),
                    ("POST", "/api/explain/ask")]:
            self.assertIn(key, t)


if __name__ == "__main__":
    unittest.main()
