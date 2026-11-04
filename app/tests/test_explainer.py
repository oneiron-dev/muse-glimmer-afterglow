"""Explainer backend: inputs, vision sight, prompt, spec repair, routes and the temporary chat.

All model and vision calls use fakes; no live server is needed.
"""
import base64
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import explainer, routes, threads  # noqa: E402
from afterglow.llm import GlimmerClient  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PNG_B64 = base64.b64encode(PNG).decode("ascii")


class FakeClient:
    """A GlimmerClient stand-in: canned spec text for chat(), the same text in pieces for stream_chat()."""

    def __init__(self, spec_text="", followup_text=""):
        self.spec_text, self.followup_text = spec_text, followup_text
        self.chat_calls, self.stream_calls = [], []

    def chat(self, messages, tools=None, **kw):
        self.chat_calls.append({"messages": messages, "tools": tools, **kw})
        return {"message": {"role": "assistant", "content": self.spec_text}, "finish_reason": "stop"}

    def stream_chat(self, messages, tools=None, **kw):
        self.stream_calls.append({"messages": messages, "tools": tools, **kw})
        t = self.followup_text
        for i in range(0, len(t), 7):
            yield {"type": "content", "text": t[i:i + 7]}
        yield {"type": "done", "message": {"content": t}}


class FakeVision:
    """vision.mode / vision.describe stand-in."""

    def __init__(self, mode="none", result=None):
        self._mode, self.result, self.calls = mode, result, []

    def mode(self, app=None):
        return self._mode

    def describe(self, png, question=None):
        self.calls.append(question)
        return self.result


SEEN = {"text": "Send (disabled); 送信先が未確認です", "items": [], "ms": 640, "unknowns": ["the small icon"],
        "observations": [
            {"id": "v0", "kind": "button", "text": "Send", "label": "Send button", "state": "disabled",
             "box": [0.7, 0.8, 0.2, 0.1]},
            {"id": "v1", "kind": "text", "text": "送信先が未確認です", "label": "", "box": [0.1, 0.6, 0.6, 0.08]},
            {"id": "v2", "kind": "text", "text": "No box here", "label": "", "box": None},
            {"id": "v3", "kind": "button", "text": "Download", "label": "", "state": "selected", "box": None}]}


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
    return routes.Request("POST", "/api/x", "", {"Content-Type": "application/json"}, json.dumps(body).encode())


def req_get(query):
    return routes.Request("GET", "/api/x", query, {}, None)


def boxes(n=3):
    return [{"text": f"line {i} of the screen", "box": [0.05 * i, 0.1 * i, 0.15 + 0.01 * i, 0.08]} for i in range(n)]


def item(label, detail="One short sentence."):
    return {"label": label, "detail": detail}


class with_vision:
    """Patch explainer's vision module for one block."""

    def __init__(self, fake):
        self.fake = fake

    def __enter__(self):
        self.saved = explainer._vision
        explainer._vision = lambda: self.fake
        return self.fake

    def __exit__(self, *exc):
        explainer._vision = self.saved


class InputsTest(unittest.TestCase):
    def test_fraction_list_and_flat_helper_boxes(self):
        o = explainer.normalize_ocr(boxes(2) + [{"text": "Send", "x": 0.5, "y": 0.5, "w": 0.1, "h": 0.05}])
        self.assertEqual([b["id"] for b in o["boxes"]], ["b0", "b1", "b2"])
        self.assertEqual(o["boxes"][2]["box"], [0.5, 0.5, 0.1, 0.05])
        self.assertEqual(o["dropped"], 0)

    def test_pixel_envelope_converts_to_fractions(self):
        o = explainer.normalize_ocr({"width": 400, "height": 200, "boxes": [{"text": "Model", "box": [40, 20, 200, 40]}]})
        self.assertEqual(o["boxes"][0]["box"], [0.1, 0.1, 0.5, 0.2])

    def test_malformed_boxes_drop_and_cap(self):
        o = explainer.normalize_ocr([{"text": "ok", "box": [0, 0, 0.1, 0.1]}, {"text": "", "box": [0, 0, 1, 1]},
                                     {"text": "bad", "box": [0, "x", 1, 1]}, "nope", {"text": "zero", "box": [0, 0, 0, 1]}])
        self.assertEqual([b["text"] for b in o["boxes"]], ["ok"])
        self.assertEqual(o["dropped"], 4)
        many = explainer.normalize_ocr([{"text": f"t{i}", "box": [0, 0, 0.1, 0.1]} for i in range(60)])
        self.assertEqual(len(many["boxes"]), explainer.OCR_MAX_BOXES)
        with self.assertRaises(ValueError):
            explainer.normalize_ocr("text")

    def test_place_in_words(self):
        self.assertEqual(explainer.place([0.8, 0.4, 0.1, 0.1]), "at middle right")
        self.assertEqual(explainer.place([0.0, 0.0, 0.2, 0.1]), "at top left")
        self.assertEqual(explainer.place([0.4, 0.8, 0.2, 0.1]), "at bottom centre")
        self.assertEqual(explainer.place([0.04, 0.25, 0.92, 0.5]), "across the middle")
        self.assertEqual(explainer.place(None), "")

    def test_ax_one_line(self):
        self.assertEqual(explainer.normalize_ax({"role": "button", "name": "Send", "value": None}), "button Send")
        self.assertEqual(explainer.normalize_ax({"role": "AXButton", "title": "送信 Send", "value": None,
                                                  "app_name": "Mail"}), "AXButton 送信 Send")
        self.assertIsNone(explainer.normalize_ax("  "))


class LookTest(unittest.TestCase):
    def test_no_crop_is_none_and_vision_is_not_asked(self):
        with with_vision(FakeVision("mac", SEEN)) as v:
            self.assertEqual(explainer.look(None, None, "q"), ("none", None))
            self.assertEqual(v.calls, [])

    def test_modes(self):
        with with_vision(FakeVision("none")):
            self.assertEqual(explainer.look(None, PNG, "q"), ("words", None))
        with with_vision(FakeVision("glimmer")) as v:
            self.assertEqual(explainer.look(None, PNG, "q"), ("image", None))
            self.assertEqual(v.calls, [])
        with with_vision(FakeVision("mac", SEEN)) as v:
            sight, seen = explainer.look(None, PNG, "Can I send this now?")
            self.assertEqual(sight, "described")
            self.assertEqual(v.calls, ["Can I send this now?"])
            self.assertEqual([i["id"] for i in seen["items"]], ["v0", "v1", "v2", "v3"])
            self.assertEqual(seen["items"][3]["text"], "Download")  # "selected" is not reliable, so it is left out
            self.assertEqual(seen["items"][0]["text"], "Send button: Send (disabled)")
            self.assertIsNone(seen["items"][2]["box"])
        with with_vision(FakeVision("mac", None)):
            self.assertEqual(explainer.look(None, PNG, "q"), ("words", {"failed": True, "items": [], "unknowns": []}))

    def test_missing_vision_module_reads_words(self):
        with with_vision(None):
            self.assertEqual(explainer.look(None, PNG, "q")[0], "words")

    def test_anchors_join_ocr_and_vision_boxes(self):
        with with_vision(FakeVision("mac", SEEN)):
            _, seen = explainer.look(None, PNG, "q")
        anchors = explainer.anchors_of(explainer.normalize_ocr(boxes(2)), seen)
        self.assertEqual([a["id"] for a in anchors], ["b0", "b1", "v0", "v1"])  # v2 has no box: a fact, not a pin
        ocr = explainer.normalize_ocr([{"text": "請求書の送信 / Send invoice", "box": [0.05, 0.05, 0.6, 0.06]},
                                       {"text": "送信先が未確認です", "box": [0.12, 0.62, 0.5, 0.05]},
                                       {"text": "送信 Send", "box": [0.7, 0.85, 0.15, 0.06]}])
        snapped = {a["id"]: a["box"] for a in explainer.anchors_of(ocr, seen)}
        self.assertEqual(snapped["v1"], [0.12, 0.62, 0.5, 0.05])  # the vision line takes the exact OCR box
        self.assertEqual(snapped["v0"], [0.7, 0.85, 0.15, 0.06])  # "Send button: Send (disabled)" matches "送信 Send"
        facts = explainer.build_facts("q", explainer.normalize_ocr(boxes(2)), seen, "button Send")
        self.assertEqual(sorted(facts), ["ax", "b0", "b1", "q", "unclear", "v0", "v1", "v2", "v3"])


class PromptTest(unittest.TestCase):
    def msgs(self, sight="words", n=3, **kw):
        ocr = explainer.normalize_ocr(boxes(n))
        return explainer.build_messages("what is this?", explainer.build_facts("what is this?", ocr),
                                        explainer.anchors_of(ocr, None), sight=sight, **kw)

    def test_words_prompt_lists_facts_and_pins_without_pixels(self):
        m = self.msgs()
        user = m[1]["content"]
        self.assertIsInstance(user, str)
        self.assertIn('"b0": "line 0 of the screen; at top left"', user)
        self.assertIn("pins: b0 b1 b2", user)
        self.assertIn("only its words", user)
        self.assertNotIn(PNG_B64, json.dumps(m))
        self.assertLessEqual(explainer.messages_tokens(m), explainer.PROMPT_TOKEN_BUDGET)

    def test_image_sight_attaches_the_crop(self):
        m = self.msgs("image", image_b64=PNG_B64)
        parts = m[1]["content"]
        self.assertEqual(parts[0], {"type": "image_url", "image_url": {"url": "data:image/png;base64," + PNG_B64}})
        self.assertIn("you see it", parts[1]["text"])

    def test_no_crop_forbids_screenshot_and_has_no_pins_line(self):
        user = self.msgs("none", n=0)[1]["content"]
        self.assertIn("Do not use screenshot", user)
        self.assertNotIn("pins:", user)

    def test_budget_drops_trailing_lines_but_keeps_three(self):
        ocr = explainer.normalize_ocr([{"text": "word " * 39, "box": [0, 0, 0.1, 0.1]} for _ in range(24)])
        m = explainer.build_messages("q", explainer.build_facts("q", ocr), explainer.anchors_of(ocr, None),
                                     sight="words")
        self.assertLessEqual(explainer.messages_tokens(m), explainer.PROMPT_TOKEN_BUDGET)
        self.assertIn('"b2"', m[1]["content"])
        self.assertNotIn('"b23"', m[1]["content"])
        self.assertNotIn("b23", m[1]["content"].split("pins:")[1])

    def test_followup_prompt_carries_the_figure_and_rules(self):
        current = {"v": 2, "title": "T", "answer": "A", "body": {"type": "text", "text": "drawn"}}
        m = self.msgs(current=current, followup="and the red line?", history=["user: what is this?"])
        self.assertIn("redraw", m[0]["content"])
        self.assertIn('Current figure: {"type": "text", "text": "drawn"}', m[1]["content"])
        self.assertTrue(m[1]["content"].startswith("Question: and the red line?"))
        self.assertTrue(m[1]["content"].endswith("Question: and the red line?"))
        self.assertIn('"q": "what is this?"', m[1]["content"])
        self.assertIn("Earlier in this window:\nuser: what is this?", m[1]["content"])

    def test_system_prompt_fits_with_room_for_facts(self):
        self.assertLess(explainer.estimate_tokens(explainer.SYSTEM_PROMPT + explainer.FOLLOWUP_RULES), 650)

    def test_figure_types(self):
        self.assertEqual(explainer.figure_types(True, True), ("screenshot", "text"))
        self.assertEqual(explainer.figure_types(True, True, followup=True), explainer.TYPES)
        for args in ((True, False), (False, True), (False, False, True)):
            self.assertNotIn("screenshot", explainer.figure_types(*args))
        rf = explainer.response_format(types=("list", "text"))
        self.assertEqual(rf["json_schema"]["schema"]["properties"]["body"]["properties"]["type"]["enum"], ["list", "text"])
        self.assertEqual(explainer.BODY_SCHEMA["properties"]["type"]["enum"], list(explainer.TYPES))  # not mutated

    def test_schema_puts_the_answer_first(self):
        props = explainer.response_format(followup=True)["json_schema"]["schema"]["properties"]
        self.assertEqual(list(props), ["answer", "answered", "redraw", "title", "body"])
        self.assertNotIn("redraw", explainer.response_format()["json_schema"]["schema"]["properties"])


class RepairTest(unittest.TestCase):
    anchors = [{"id": "b0", "text": "Model", "box": [0, 0, 0.5, 0.1]},
               {"id": "b1", "text": "Needs a 24 GB NVIDIA GPU", "box": [0, 0.2, 0.9, 0.3]},
               {"id": "v0", "text": "Send (disabled)", "box": [0.7, 0.8, 0.2, 0.1]}]

    def fix(self, body, answer="You can chat here, but this Mac cannot train.", **kw):
        kw.setdefault("has_crop", True)
        kw.setdefault("sight", "described")
        raw = {"answer": answer, "answered": kw.pop("answered", True), "title": kw.pop("title", "Model screen"),
               "body": body}
        return explainer.repair(json.dumps(raw), self.anchors, question="what is this?", **kw)

    def test_each_figure_passes_and_gets_ids(self):
        bodies = [
            {"type": "screenshot", "pins": [{"anchor": "b1", **item("Training")}, {"anchor": "v0", **item("Send")}]},
            {"type": "list", "items": [item("One"), item("Two"), item("Three")]},
            {"type": "flow", "steps": [item("Talk"), item("Sleep"), item("Train")]},
            {"type": "cycle", "steps": [item("Train"), item("Check")],
             "outcomes": [{"when": "Better", "resume": "stop", **item("Keep")},
                          {"when": "Worse", "resume": "repeat", **item("Undo")}]},
            {"type": "compare", "left": "Mac", "right": "PC", "rows": [{"aspect": "Training", "left": "No", "right": "Yes"}]},
            {"type": "text", "text": "Nothing to draw."},
        ]
        for body in bodies:
            spec, info = self.fix(body)
            self.assertEqual((info["errors"], info["fallback"]), ([], False), body["type"])
            self.assertEqual(spec["body"]["type"], body["type"])
            self.assertEqual(spec["v"], 2)
        spec, _ = self.fix(bodies[0])
        self.assertEqual(spec["body"]["pins"][0], {"anchor": "b1", "id": "i1", "label": "Training",
                                                   "detail": "One short sentence."})

    def test_pins_need_known_unique_anchors(self):
        spec, info = self.fix({"type": "screenshot", "pins": [
            {"anchor": "b9", **item("Ghost")}, {"anchor": "b0", **item("Model")}, {"anchor": "b0", **item("Again")}]})
        self.assertEqual([p["anchor"] for p in spec["body"]["pins"]], ["b0"])
        self.assertEqual(len(info["errors"]), 2)

    def test_pins_without_a_crop_become_a_list(self):
        spec, info = self.fix({"type": "screenshot", "pins": [{"anchor": "b0", **item("Model")},
                                                              {"anchor": "b1", **item("GPU")}]}, has_crop=False)
        self.assertEqual(spec["body"], {"type": "list", "items": [
            {"id": "i1", "label": "Model", "detail": "One short sentence."},
            {"id": "i2", "label": "GPU", "detail": "One short sentence."}]})
        self.assertFalse(info["fallback"])

    def test_place_words_leave_item_text(self):
        spec, _ = self.fix({"type": "screenshot", "pins": [
            {"anchor": "v0", "label": "Send at bottom right", "detail": "Send is disabled at bottom left of the screen."}]})
        self.assertEqual(spec["body"]["pins"][0]["label"], "Send")
        self.assertEqual(spec["body"]["pins"][0]["detail"], "Send is disabled.")
        spec, _ = self.fix({"type": "text", "text": "x"}, answer="The button on the right is a calendar icon at middle right.")
        self.assertEqual(spec["answer"], "The button on the right is a calendar icon.")

    def test_caps_trims_and_dashes(self):
        spec, _ = self.fix({"type": "list", "items": [item(f"Point {i} " + "x" * 40, "d — " * 40) for i in range(8)]},
                           answer="Yes — you can chat " + "word " * 80, title="T" * 80)
        items = spec["body"]["items"]
        self.assertEqual(len(items), explainer.LIST_MAX)
        self.assertTrue(all(len(i["label"]) <= explainer.LABEL_MAX and len(i["detail"]) <= explainer.DETAIL_MAX
                            for i in items))
        self.assertTrue(spec["answer"].startswith("Yes, you can chat"))
        self.assertTrue(spec["answer"].endswith("…"))  # one long sentence, cut at a word
        self.assertNotIn("—", json.dumps(spec, ensure_ascii=False))
        self.assertLessEqual(len(spec["answer"]), explainer.ANSWER_MAX)
        self.assertEqual(len(spec["title"]), explainer.TITLE_MAX)

    def test_answers_end_on_a_whole_sentence(self):
        long = "This is the Models screen for choosing what runs on your computer. You cannot train here. " + \
               "It shows nightly updates and the current model is Muse Glimmer 30B with no update loaded yet " * 3
        spec, _ = self.fix({"type": "text", "text": "x"}, answer=long)
        self.assertEqual(spec["answer"], "This is the Models screen for choosing what runs on your computer. "
                                         "You cannot train here.")
        self.assertEqual(explainer.clip_sentences("送信できません。" * 50, 20), "送信できません。送信できません。")

    def test_short_cycle_becomes_a_flow(self):
        spec, info = self.fix({"type": "cycle", "steps": [item("Train"), item("Check")],
                               "outcomes": [{"when": "Better", "resume": "stop", **item("Keep")}]})
        self.assertEqual([s["label"] for s in spec["body"]["steps"]], ["Train", "Check", "Keep"])
        self.assertFalse(info["fallback"])

    def test_unusable_bodies_fall_back_never_empty(self):
        spec, info = self.fix({"type": "flow", "steps": []})
        self.assertTrue(info["fallback"])
        self.assertEqual([p["anchor"] for p in spec["body"]["pins"]], ["b1", "b0", "v0"])  # largest first
        spec, _ = self.fix({"type": "map"}, has_crop=False)
        self.assertEqual(spec["body"]["type"], "list")
        spec, _ = explainer.repair("not json", [], question="what?")
        self.assertEqual(spec["body"], {"type": "text", "text": explainer.EMPTY_FIGURE_TEXT})
        self.assertEqual(spec["answer"], explainer.NO_ANSWER)
        self.assertEqual(spec["title"], "what?")

    def test_words_only_and_unanswered_says_so_plainly(self):
        spec, info = self.fix({"type": "text", "text": "x"}, answer="The facts do not show it.", answered=False,
                              sight="words")
        self.assertEqual(spec["answer"], explainer.WORDS_ONLY_ANSWER)
        self.assertFalse(info["answered"])
        spec, _ = self.fix({"type": "text", "text": "x"}, answered=False, sight="words", seen={"failed": True})
        self.assertEqual(spec["answer"], explainer.VISION_FAILED_ANSWER)
        spec, _ = self.fix({"type": "text", "text": "x"}, answer="The picture is too small to read.",
                           answered=False, sight="described")
        self.assertEqual(spec["answer"], "The picture is too small to read.")
        spec, _ = self.fix({"type": "text", "text": "x"}, answer="It sends the invoice.", sight="words")
        self.assertEqual(spec["answer"], "It sends the invoice.")
        spec, _ = self.fix({"type": "text", "text": "x"}, answer="The screen does not say what training needs.",
                           answered=False, sight="words", followup=True)
        self.assertEqual(spec["answer"], "The screen does not say what training needs.")  # follow-ups keep theirs

    def test_copy_has_no_internal_terms(self):
        for line in (explainer.WORDS_ONLY_ANSWER, explainer.VISION_FAILED_ANSWER, explainer.NO_ANSWER,
                     explainer.EMPTY_FIGURE_TEXT):
            for word in ("crop", "OCR", "fact", "anchor", "model", "—"):
                self.assertNotIn(word, line)

    def test_fenced_json_parses(self):
        spec, info = explainer.repair('```json\n{"answer": "Hi.", "answered": true, "title": "T", '
                                      '"body": {"type": "text", "text": "hi"}}\n```', [])
        self.assertEqual((spec["answer"], info["fallback"]), ("Hi.", False))


class PartialAnswerTest(unittest.TestCase):
    def test_escaped_emoji_join_and_wait_for_their_second_half(self):
        full = '{"answer": "It is a thumbs up \\ud83d\\udc4d, sent.", "answered": true}'
        self.assertEqual(json.loads(full)["answer"], "It is a thumbs up 👍, sent.")
        seen = [explainer.partial_answer(full[:i]) for i in range(len(full) + 1)]
        self.assertEqual(seen[-1], "It is a thumbs up 👍, sent.")
        for a, b in zip(seen, seen[1:]):
            self.assertTrue(b.startswith(a), (a, b))
            b.encode("utf-8")  # never a lone surrogate (web.py sends each event as UTF-8)
        self.assertEqual(explainer.partial_answer('{"answer": "a \\ud83d b \\udc4d c"'), "a  b  c")


    def test_decodes_as_it_grows(self):
        full = json.dumps({"answer": 'No: the "Send" button\nis off – 送信', "answered": True})
        seen = [explainer.partial_answer(full[:i]) for i in range(len(full) + 1)]
        self.assertEqual(seen[0], "")
        self.assertEqual(seen[-1], 'No: the "Send" button\nis off – 送信')
        for a, b in zip(seen, seen[1:]):
            self.assertTrue(b.startswith(a), (a, b))


class LlmHookTest(unittest.TestCase):
    def test_response_format_defaults_preserved(self):
        c = GlimmerClient(endpoint="http://127.0.0.1:9")
        c.kind = "openai"  # chat only: no adapter lookups
        msgs = [{"role": "user", "content": "hi"}]
        before = json.dumps(c.request_body(msgs))
        self.assertNotIn("response_format", before)
        body = c.request_body(msgs, response_format=explainer.response_format())
        self.assertEqual(body.pop("response_format"), explainer.response_format())
        self.assertEqual(json.dumps(body), before)


class FakeLlama(FakeClient):
    """A llama.cpp server: the template and completion endpoints for Glimmer's answer channel."""
    kind, slot = "llama.cpp", 1

    def __init__(self, spec_text="", followup_text="", template=True):
        super().__init__(spec_text, followup_text)
        self.template, self.posts = template, []

    def scale_fields(self):
        return {"lora": [{"id": 0, "scale": 0.5}]}

    def post(self, path, body, timeout=None, headers=None):
        self.posts.append((path, body))
        if path == "/apply-template":
            if not self.template:
                raise OSError("HTTP 404")
            return {"prompt": "<|start|>user<|message|>q<|end|><|start|>assistant"}
        followup = "redraw" in json.dumps(body.get("json_schema"))
        return {"content": self.followup_text if followup else self.spec_text, "stop_type": "eos"}


class RoutesTest(unittest.TestCase):
    spec = {"answer": "You can chat here, but training needs a 24 GB NVIDIA GPU.", "answered": True,
            "title": "Model screen", "body": {"type": "screenshot", "pins": [
                {"anchor": "b0", "label": "Model in use", "detail": "Muse Glimmer 30B."},
                {"anchor": "b1", "label": "Training", "detail": "Needs a 24 GB NVIDIA GPU."}]}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"AFTERGLOW_RUNS": self.tmp.name})
        self.env.start()
        self.vision = with_vision(FakeVision("none"))
        self.vision.__enter__()

    def tearDown(self):
        self.vision.__exit__()
        self.env.stop()
        self.tmp.cleanup()

    def app(self, spec=None, followup=None, history=()):
        return FakeApp(FakeClient(json.dumps(spec or self.spec), json.dumps(followup) if followup else ""),
                       history=history)

    def test_create_get_crop_round_trip(self):
        app = self.app()
        out = explainer.post_explain(app, req_post({"question": "what is this screen for?", "crop_png_b64": PNG_B64,
                                                    "ocr": boxes(2), "ax": {"role": "window", "name": "Settings"},
                                                    "app_name": "Afterglow"}))
        self.assertEqual(out, {"id": "t1", "world": "w-test", "day": 1, "url": "/explain?id=t1&world=w-test&day=1"})
        (call,) = app.client.chat_calls
        self.assertEqual(call["reasoning"], "low")
        schema = call["response_format"]["json_schema"]["schema"]
        self.assertEqual(schema["properties"]["body"]["properties"]["type"]["enum"], ["screenshot", "text"])
        self.assertIn("Figure: screenshot", call["messages"][1]["content"])
        self.assertNotIn(PNG_B64, json.dumps(call["messages"]))  # mode none: words only
        got = explainer.get_explain(app, req_get("id=t1"))
        self.assertEqual(got["spec"]["answer"], self.spec["answer"])
        self.assertEqual([p["anchor"] for p in got["spec"]["body"]["pins"]], ["b0", "b1"])
        self.assertEqual(got["crop_url"], "/api/explain/crop?id=t1&world=w-test&day=1")
        self.assertEqual([a["id"] for a in got["anchors"]], ["b0", "b1"])
        self.assertEqual(got["sight"], "words")
        raw = explainer.get_crop(app, req_get("id=t1"))
        self.assertIsInstance(raw, routes.Raw)
        self.assertEqual((raw.body, raw.content_type), (PNG, "image/png"))

    def test_mac_vision_facts_reach_the_prompt_and_the_pins(self):
        spec = {**self.spec, "body": {"type": "screenshot", "pins": [{"anchor": "v0", **item("Send")}]}}
        app = self.app(spec)
        with with_vision(FakeVision("mac", SEEN)) as v:
            tid = explainer.create(app, "Can I send this now?", crop_png_b64=PNG_B64)["id"]
        self.assertEqual(v.calls, ["Can I send this now?"])
        user = app.client.chat_calls[0]["messages"][1]["content"]
        self.assertIn('"v0": "Send button: Send (disabled); at bottom right"', user)
        self.assertIn('"v2": "No box here"', user)
        self.assertIn("pins: v0 v1", user)
        got = explainer.get_explain(app, req_get(f"id={tid}"))
        self.assertEqual(got["sight"], "described")
        self.assertEqual(got["anchors"][0], {"id": "v0", "text": "Send button: Send (disabled)",
                                             "box": [0.7, 0.8, 0.2, 0.1]})
        self.assertEqual(got["spec"]["body"]["pins"][0]["anchor"], "v0")

    def test_glimmer_vision_sends_the_image(self):
        app = self.app()
        with with_vision(FakeVision("glimmer")):
            explainer.create(app, "what is this?", crop_png_b64=PNG_B64, ocr=boxes(2))
        content = app.client.chat_calls[0]["messages"][1]["content"]
        self.assertEqual(content[0]["image_url"]["url"], "data:image/png;base64," + PNG_B64)

    def test_words_only_unanswered_gets_the_vision_line(self):
        app = self.app({**self.spec, "answered": False, "answer": "There is no emoji.", "title": "No emoji visible",
                        "body": {"type": "text", "text": "The screen text shows no emoji."}})
        tid = explainer.create(app, "What is this emoji?", crop_png_b64=PNG_B64, ocr=boxes(1))["id"]
        spec = explainer.get_explain(app, req_get(f"id={tid}"))["spec"]
        self.assertEqual(spec["answer"], explainer.WORDS_ONLY_ANSWER)
        self.assertEqual(spec["title"], "What is this emoji?")  # never the model's guess about the picture
        self.assertEqual(spec["body"], {"type": "text", "text": ""})  # the picture alone

    def test_chat_reply_without_a_crop_uses_a_list(self):
        history = [{"role": "user", "turn": 1, "content": "which model is this?"},
                   {"role": "assistant", "turn": 1, "content": "Muse Glimmer 30B."}]
        app = self.app(history=history)
        tid = explainer.create(app, "draw that reply", source="chat", from_turn="1")["id"]
        user = app.client.chat_calls[0]["messages"][1]["content"]
        self.assertIn('"turn": "Q: which model is this? / A: Muse Glimmer 30B."', user)
        self.assertIn("Picture: none", user)
        enum = app.client.chat_calls[0]["response_format"]["json_schema"]["schema"]["properties"]["body"]["properties"]["type"]["enum"]
        self.assertNotIn("screenshot", enum)
        got = explainer.get_explain(app, req_get(f"id={tid}"))
        self.assertEqual(got["spec"]["body"]["type"], "list")  # pins without a crop become a list
        self.assertIsNone(got["crop_url"])
        self.assertEqual(got["source"], "chat")

    def test_a_spec_cut_at_the_token_limit_is_noted(self):
        class Cut(FakeClient):
            def chat(self, messages, tools=None, **kw):
                self.chat_calls.append(kw)
                return {"message": {"content": '{"answer": "Half'}, "finish_reason": "length"}
        app = FakeApp(Cut())
        tid = explainer.create(app, "what is this?", crop_png_b64=PNG_B64, ocr=boxes(3))["id"]
        rec = threads.get("w-test", 1, tid)["records"][1]
        self.assertEqual(rec["errors"][0], "the model stopped at its token limit")
        self.assertEqual(app.client.chat_calls[0]["max_tokens"], explainer.SPEC_MAX_TOKENS)

    def test_failed_model_call_stores_the_fallback(self):
        class Down(FakeClient):
            def chat(self, messages, tools=None, **kw):
                raise OSError("mel is down")
        app = FakeApp(Down())
        tid = explainer.create(app, "what is this?", crop_png_b64=PNG_B64, ocr=boxes(3))["id"]
        rec = threads.get("w-test", 1, tid)["records"][1]
        self.assertEqual(rec["spec"]["answer"], explainer.NO_ANSWER)
        self.assertEqual(rec["spec"]["body"]["type"], "screenshot")
        self.assertIn("mel is down", rec["errors"][0])

    def test_old_or_missing_spec_serves_a_fallback(self):
        app = self.app()
        threads.create("w-test", 1, "explain", "what is this?", meta={"source": "ambient"})
        threads.append("w-test", 1, "t1", {"role": "user", "content": "what is this?",
                                           "ocr": explainer.normalize_ocr(boxes(2))})
        got = explainer.get_explain(app, req_get("id=t1"))
        self.assertEqual(got["spec"]["body"]["type"], "list")
        threads.append("w-test", 1, "t1", {"role": "assistant", "content": "old",
                                           "spec": {"v": 1, "title": "Old", "body": {"type": "parts"}}})
        self.assertEqual(explainer.get_explain(app, req_get("id=t1"))["spec"]["v"], 2)

    def test_bad_requests_are_refused(self):
        app = self.app()
        for body in ({"question": "  "}, {"question": "q", "source": "popup"},
                     {"question": "q", "crop_png_b64": "!!!not-base64!!!"},
                     {"question": "q", "crop_png_b64": base64.b64encode(b"GIF89a").decode()},
                     {"question": "q", "from_turn": "soon"}):
            with self.assertRaises(ValueError, msg=body):
                explainer.post_explain(app, req_post(body))
        for q in ("", "id=t9"):
            with self.assertRaises(ValueError):
                explainer.get_explain(app, req_get(q))
        threads.create("w-test", 1, "note", "not an explainer")
        with self.assertRaises(ValueError):
            explainer.get_explain(app, req_get("id=t1"))
        with self.assertRaises(ValueError):
            explainer.post_ask(app, req_post({"id": "t1", "text": "hi"}))
        self.assertEqual(app.client.chat_calls, [])

    def test_busy_slot_is_409_before_vision_runs(self):
        app = self.app()
        with with_vision(FakeVision("mac", SEEN)) as v:
            with app.lock:
                with self.assertRaises(RuntimeError):
                    explainer.create(app, "what?", crop_png_b64=PNG_B64)
            self.assertEqual(v.calls, [])
            self.assertEqual(explainer.create(app, "what?")["id"], "t1")
        self.assertFalse(app.lock.locked())

    def test_followup_streams_the_answer_and_redraws(self):
        followup = {"answer": "Training runs at night on a 24 GB NVIDIA GPU.", "answered": True, "redraw": True,
                    "title": "How training runs", "body": {"type": "flow", "steps": [item("Day"), item("Night")]}}
        app = self.app(followup=followup)
        tid = explainer.create(app, "what is this?", crop_png_b64=PNG_B64, ocr=boxes(2))["id"]
        st = explainer.post_ask(app, req_post({"id": tid, "text": "how does training run?"}))
        self.assertIsInstance(st, routes.Stream)
        self.assertTrue(st.lock)
        events = list(st.events)
        kinds = [e["type"] for e in events]
        self.assertGreater(kinds.count("content"), 2)  # the answer arrives in pieces
        self.assertEqual(kinds[-2:], ["spec", "done"])
        self.assertNotIn("answer", kinds)
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "content"), followup["answer"])
        spec = events[-2]["spec"]
        self.assertEqual((spec["title"], spec["body"]["type"]), ("How training runs", "flow"))
        self.assertEqual(spec["answer"], self.spec["answer"])  # the window's first answer stays on top
        (call,) = app.client.stream_calls
        self.assertIn("Current figure:", call["messages"][1]["content"])
        recs = threads.get("w-test", 1, tid)["records"]
        self.assertEqual([r["role"] for r in recs], ["user", "assistant", "user", "assistant"])
        self.assertEqual(recs[3]["content"], followup["answer"])
        got = explainer.get_explain(app, req_get(f"id={tid}"))
        self.assertEqual(got["spec"]["body"]["type"], "flow")

    def test_followup_without_redraw_keeps_the_figure(self):
        followup = {"answer": "It is the model — your agent.", "answered": True, "redraw": False, "title": "x",
                    "body": {"type": "text", "text": "x"}}
        app = self.app(followup=followup)
        tid = explainer.create(app, "what is this?", crop_png_b64=PNG_B64, ocr=boxes(2))["id"]
        events = list(explainer.post_ask(app, req_post({"id": tid, "text": "and the first one?"})).events)
        self.assertNotIn("spec", [e["type"] for e in events])
        self.assertEqual(events[-2], {"type": "answer", "text": "It is the model, your agent."})
        self.assertEqual(explainer.get_explain(app, req_get(f"id={tid}"))["spec"]["body"]["type"], "screenshot")

    def test_followup_failure_answers_plainly(self):
        class Down(FakeClient):
            def stream_chat(self, messages, tools=None, **kw):
                raise OSError("mel is down")
                yield
        app = FakeApp(Down(json.dumps(self.spec)))
        tid = explainer.create(app, "what is this?")["id"]
        events = list(explainer.post_ask(app, req_post({"id": tid, "text": "more?"})).events)
        self.assertEqual(events, [{"type": "answer", "text": explainer.NO_ANSWER}, {"type": "done", "id": tid}])

    def test_first_answer_reasons_and_followups_use_the_answer_channel(self):
        followup = {"answer": "You need a 24 GB NVIDIA GPU.", "answered": True, "redraw": False, "title": "x",
                    "body": {"type": "text", "text": "x"}}
        app = FakeApp(FakeLlama(json.dumps(self.spec), json.dumps(followup)))
        tid = explainer.create(app, "what is this?", crop_png_b64=PNG_B64, ocr=boxes(2))["id"]
        self.assertEqual((len(app.client.chat_calls), app.client.posts), (1, []))
        self.assertEqual(app.client.chat_calls[0]["reasoning"], "low")
        events = list(explainer.post_ask(app, req_post({"id": tid, "text": "what would I need?"})).events)
        self.assertEqual(events, [{"type": "content", "text": followup["answer"]}, {"type": "done", "id": tid}])
        self.assertEqual(app.client.stream_calls, [])
        (tpl, tpl_body), (path, body) = app.client.posts
        self.assertEqual((tpl, path), ("/apply-template", "/completion"))
        self.assertEqual(tpl_body["messages"][0]["content"], explainer.SYSTEM_PROMPT + "\n\n" + explainer.FOLLOWUP_RULES)
        self.assertTrue(body["prompt"].endswith(explainer.ANSWER_OPEN))
        self.assertIn("redraw", json.dumps(body["json_schema"]))
        self.assertEqual((body["id_slot"], body["n_predict"], body["lora"]), (1, explainer.SPEC_MAX_TOKENS, [{"id": 0, "scale": 0.5}]))

    def test_followups_without_a_template_endpoint_or_with_an_image_stream_the_chat_call(self):
        followup = json.dumps({"answer": "Yes.", "answered": True, "redraw": False, "title": "x",
                               "body": {"type": "text", "text": "x"}})
        app = FakeApp(FakeLlama(json.dumps(self.spec), followup, template=False))
        tid = explainer.create(app, "what is this?", ocr=boxes(2))["id"]
        list(explainer.post_ask(app, req_post({"id": tid, "text": "more?"})).events)
        self.assertEqual(len(app.client.stream_calls), 1)
        app = FakeApp(FakeLlama(json.dumps(self.spec), followup))
        with with_vision(FakeVision("glimmer")):
            tid = explainer.create(app, "what is this?", crop_png_b64=PNG_B64)["id"]
        list(explainer.post_ask(app, req_post({"id": tid, "text": "more?"})).events)
        self.assertEqual((len(app.client.stream_calls), app.client.posts), (1, []))  # images go through chat
        self.assertEqual(app.client.stream_calls[0]["messages"][1]["content"][0]["type"], "image_url")

    def test_followup_never_trades_the_picture_for_a_list(self):
        followup = {"answer": "It shows memory.", "answered": True, "redraw": True, "title": "Memory",
                    "body": {"type": "list", "items": [item("Memory")]}}
        app = self.app(followup=followup)
        tid = explainer.create(app, "what is this?", crop_png_b64=PNG_B64, ocr=boxes(2))["id"]
        events = list(explainer.post_ask(app, req_post({"id": tid, "text": "and memory?"})).events)
        self.assertNotIn("spec", [e["type"] for e in events])
        self.assertEqual(explainer.get_explain(app, req_get(f"id={tid}"))["spec"]["body"]["type"], "screenshot")

    def test_followup_with_an_escaped_emoji_streams_as_utf8(self):
        followup = '{"answer": "Yes, a thumbs up \\ud83d\\udc4d.", "answered": true, "redraw": false, "title": "x", ' \
                   '"body": {"type": "text", "text": "x"}}'
        app = FakeApp(FakeClient(json.dumps(self.spec), followup))
        tid = explainer.create(app, "what is this?", crop_png_b64=PNG_B64, ocr=boxes(2))["id"]
        events = list(explainer.post_ask(app, req_post({"id": tid, "text": "which emoji?"})).events)
        for ev in events:  # exactly what web.py does with each event
            json.dumps(ev, ensure_ascii=False).encode()
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "content"), "Yes, a thumbs up 👍.")
        self.assertEqual(threads.get("w-test", 1, tid)["records"][-1]["content"], "Yes, a thumbs up 👍.")

    def test_urls_and_requests_name_their_day(self):
        app = self.app()
        made = explainer.create(app, "what is this?", crop_png_b64=PNG_B64, ocr=boxes(2))
        app.session = FakeSession(world="w-other", day=3)  # the person opens another profile and day
        threads.create("w-other", 3, "explain", "another explainer")  # it has its own t1
        got = explainer.get_explain(app, req_get("id=t1&world=w-test&day=1"))
        self.assertEqual((got["question"], got["world"], got["day"]), ("what is this?", "w-test", 1))
        raw = explainer.get_crop(app, req_get("id=t1&world=w-test&day=1"))
        self.assertEqual(raw.body, PNG)
        with self.assertRaises(RuntimeError):  # 409: a follow-up runs only on the open day
            explainer.post_ask(app, req_post({"id": made["id"], "world": "w-test", "day": 1, "text": "more?"}))
        self.assertEqual(len(threads.get("w-test", 1, "t1")["records"]), 2)
        self.assertEqual(threads.get("w-other", 3, "t1")["records"], [])
        for bad in ("id=t1&world=../w-test&day=1", "id=t1&world=w-test&day=x", "id=t1&world=w-test&day=0",
                    "id=t1&world=w.test&day=1"):
            with self.assertRaises(ValueError, msg=bad):
                explainer.get_explain(app, req_get(bad))

    def test_a_switch_before_the_stream_starts_writes_nothing(self):
        app = self.app()
        tid = explainer.create(app, "what is this?", ocr=boxes(2))["id"]
        st = explainer.post_ask(app, req_post({"id": tid, "world": "w-test", "day": 1, "text": "more?"}))
        app.session = FakeSession(world="w-test", day=2)  # switched between the request and the stream lock
        threads.create("w-test", 2, "dictation", "a dictation")  # day 2's own t1
        events = list(st.events)
        self.assertEqual(events, [{"type": "error", "message": explainer.OTHER_DAY}, {"type": "done", "id": tid}])
        self.assertEqual(threads.get("w-test", 2, "t1")["records"], [])
        self.assertEqual(len(threads.get("w-test", 1, tid)["records"]), 2)
        self.assertEqual(app.client.stream_calls, [])

    def test_a_switch_during_vision_saves_nothing(self):
        app = self.app()

        class Switching(FakeVision):
            def describe(self, png, question=None):
                app.session = FakeSession(world="w-other", day=1)
                return SEEN

        with with_vision(Switching("mac")):
            with self.assertRaises(RuntimeError) as caught:
                explainer.create(app, "Can I send this now?", crop_png_b64=PNG_B64)
        self.assertEqual(str(caught.exception), explainer.SWITCHED)
        self.assertFalse(app.lock.locked())
        self.assertEqual(app.client.chat_calls, [])
        for world in ("w-test", "w-other"):
            self.assertFalse(threads.folder(world, 1).exists())

    def test_routes_are_registered(self):
        t = routes.table()
        for key in [("POST", "/api/explain"), ("GET", "/api/explain"), ("GET", "/api/explain/crop"),
                    ("POST", "/api/explain/ask")]:
            self.assertIn(key, t)


if __name__ == "__main__":
    unittest.main()
