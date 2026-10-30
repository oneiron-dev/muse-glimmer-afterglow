"""Offline tests for night/context_distill.py against a stand-in llama-server (no GPU, no network):

  python3 -m unittest discover -s night/tests
"""
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import context_distill as cd  # noqa: E402

SPECIAL = {"<|start|>": 200006, "<|message|>": 200008 - 100, "<|eot|>": 200008, "<|end|>": 200007}
BY_ID = {v: k for k, v in SPECIAL.items()}
STUDENT = "/data/afterglow/adapters/w-test-d5.gguf"


def tokenize(text):
    ids, i = [], 0
    while i < len(text):
        for s, sid in SPECIAL.items():
            if text.startswith(s, i):
                ids.append(sid)
                i += len(s)
                break
        else:
            ids.append(1000 + ord(text[i]))
            i += 1
    return ids


def detokenize(ids):
    return "".join(BY_ID.get(t, chr(t - 1000) if t >= 1000 else "") for t in ids)


RECALL = (' to=functions.recall<|message|><atem:function_calls><atem:invoke name="recall"><atem:parameter name="query">'
          '{q}</atem:parameter></atem:invoke></atem:function_calls><|eot|>')


class Model:
    """The student's samples, chosen by what the prompt holds. Every request body is kept."""

    def __init__(self):
        self.bodies, self.chats, self.chat_query = [], [], None

    def completion(self, prompt):
        asked = prompt.rsplit("<|start|>user", 1)[-1]  # the last user message (the system prompt names tools too)
        q = next((w for w in ("Blind Guardian", "cut me", "inexact", "calendar") if w in asked), "")
        if "Answer in one short sentence." in prompt:  # the fact form: the header is forced
            return ("Jonas's lab", "limit") if q == "cut me" else ("Jonas's lab mate is Ana Ruiz.<|eot|>", "eos")
        if '{"notes": []}' in prompt:  # the second turn, after an empty recall
            return " to=self<|message|>none<|end|><|start|>assistant to=user<|message|>I have no note of that.<|eot|>", "eos"
        if q == "Blind Guardian":
            return RECALL.format(q=q), "eos"
        if q == "calendar":
            return RECALL.format(q=q).replace('name="recall"', 'name="calendar.add_event"'), "eos"
        if q == "inexact":
            return " to=user<|message|>odd<|eot|>", "eos"
        return " to=self<|message|>thinking<|end|><|start|>assistant to=user<|message|>I don't know.<|eot|>", "eos"


def serve(model, loaded):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(loaded if self.path == "/lora-adapters" else {})

        def do_POST(self):
            b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/apply-template":
                out = "<|start|>system<|message|>tools<|eot|>" if b.get("tools") else ""
                for m in b["messages"]:
                    extra = json.dumps(m["tool_calls"]) if m.get("tool_calls") else ""
                    out += f"<|start|>{m['role']}<|message|>{m.get('content') or ''}{extra}<|eot|>"
                out += f"<!--{json.dumps(b['chat_template_kwargs'], sort_keys=True)}-->"
                return self.reply({"prompt": out + "<|start|>assistant"})
            if self.path == "/completion":
                model.bodies.append(b)
                text, stop = model.completion(b["prompt"])
                return self.reply({"tokens": tokenize(text), "stop_type": stop})
            if self.path == "/detokenize":
                return self.reply({"content": detokenize(b["tokens"])})
            if self.path == "/tokenize":  # "odd" re-tokenizes differently: a non-canonical sample
                ids = tokenize(b["content"])
                return self.reply({"tokens": ids + [5] if "odd" in b["content"] else ids})
            if self.path == "/v1/chat/completions":
                model.chats.append(b)
                q = model.chat_query or "Blind Guardian"
                return self.reply({"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "call_0", "type": "function", "function": {"name": "recall", "arguments": json.dumps({"query": q})}}]}}]})
            self.send_error(404)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return srv


class ContextDistillTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.runs, worlds, self.out = tmp / "runs", tmp / "worlds", tmp / "out"
        env = mock.patch.dict(os.environ, {"AFTERGLOW_RUNS": str(self.runs), "AFTERGLOW_WORLDS": str(worlds)})
        env.start()
        self.addCleanup(env.stop)
        (worlds / "w-test").mkdir(parents=True)
        (worlds / "w-test" / "world.json").write_text(json.dumps({"world": "w-test", "persona": {"name": "Jonas Heller"}}))
        day = self.runs / "w-test" / "6"
        (day / "night" / "rewrites").mkdir(parents=True)
        (day / "notes.json").write_text(json.dumps({"notes": [
            {"id": "N0001", "key": "lab mate", "value": "Ana Ruiz", "status": "active"},
            {"id": "N0002", "key": "lab mate", "value": "Old Name", "status": "superseded", "superseded_by": "N0001"}]}))
        (day / "session.json").write_text(json.dumps({"date": "2026-11-01"}))
        rewrites = [{"item_id": "N0001", "form": "qa_forward", "user": "Who is my lab mate?"},
                    {"item_id": "N0001", "form": "qa_forward", "user": "Who is my lab mate, again?"},
                    {"item_id": "N0003", "form": "qa_reverse", "user": "Who is Ana Ruiz?"},
                    {"item_id": "N0004", "form": "qa_forward", "user": "cut me"}]
        (day / "night" / "rewrites" / "rewrites.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rewrites))
        self.pools = tmp / "pools.jsonl"
        pool = [{"item_id": "kl-KO-1", "form": "kl_obscure", "user": "Who replaced Blind Guardian's guitarist?"},
                {"item_id": "kl-KK-1", "form": "kl_known", "user": "What is the capital of Norway?"},
                {"item_id": "kl-KP-1", "form": "kl_people", "user": "Who is the painter Ada Lovelace?"},
                {"item_id": "kl-KS-1", "form": "kl_swapped", "user": "What is Ada's lab mate?"}]
        self.pools.write_text("".join(json.dumps(r) + "\n" for r in pool))
        self.model = Model()
        self.loaded = [{"id": 0, "path": "/data/afterglow/adapters/w-other.gguf", "scale": 1.0},
                       {"id": 1, "path": STUDENT, "scale": 1.0}]
        self.srv = serve(self.model, self.loaded)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def run_cd(self, *extra):
        argv = ["--world", "w-test", "--day", "6", "--server", f"http://127.0.0.1:{self.srv.server_port}",
                "--student-adapter", STUDENT, "--student-sha256", "ab" * 32, "--pools", str(self.pools),
                "--out-dir", str(self.out), *extra]
        with redirect_stdout(io.StringIO()):
            return cd.main(argv)

    def rows(self):
        return {r["item_id"]: r for r in map(json.loads, (self.out / "cd-rows.jsonl").read_text().splitlines())}

    def test_fact_rows_force_the_header_and_teach_with_the_notes(self):
        report = self.run_cd("--forms", "cd_fact")
        r = self.rows()["cd-cd_fact-N0001"]
        self.assertTrue(r["raw"]["forced_header"])
        self.assertTrue(r["raw"]["prompt"].endswith(
            "Who is my lab mate? Answer in one short sentence.<|eot|>"
            '<!--{"current_date": "2026-10-27", "knowledge_cutoff": "2026-01-04", "reasoning_strength": "minimal"}-->'
            + cd.ANSWER_PREFIX))
        self.assertNotIn("Ana Ruiz", r["raw"]["prompt"])  # the student has no notes
        self.assertEqual(r["raw"]["completion"], "Jonas's lab mate is Ana Ruiz.<|eot|>")  # the generated text only
        self.assertEqual(r["raw"]["completion_ids"], tokenize(r["raw"]["completion"]))
        t = r["teacher"]["prompt"]
        self.assertIn("Notes I keep about Jonas:\n- lab mate: Ana Ruiz\n\nWho is my lab mate? Answer in one short sentence.", t)
        self.assertNotIn("Old Name", t)  # superseded notes are not taught
        self.assertTrue(t.endswith(cd.ANSWER_PREFIX))
        self.assertEqual(r["teacher"]["notes"], 1)
        self.assertEqual((r["kind"], r["form"], r["valid"]), ("context_distill", "cd_fact", True))
        self.assertEqual(r["student"]["adapter_sha256"], "ab" * 32)
        # one question per item, qa_forward only; the cut sample is dropped and counted
        self.assertEqual(report["by_form"]["cd_fact"], {"asked": 2, "kept": 1, "after_recall": 0, "dropped": {"cut": 1}})

    def test_samples_are_on_policy_with_the_student_adapter_on_slot_1(self):
        self.run_cd("--forms", "cd_fact")
        b = next(x for x in self.model.bodies if "lab mate" in x["prompt"])
        self.assertEqual(b["lora"], [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 1.0}])
        self.assertEqual({k: b[k] for k in ("temperature", "top_k", "top_p", "min_p", "id_slot", "cache_prompt")},
                         {"temperature": 1.0, "top_k": 0, "top_p": 1.0, "min_p": 0.0, "id_slot": 1, "cache_prompt": False})
        self.assertEqual(b["seed"], cd.seed_for("w-test", 6, "cd_fact", "N0001"))

    def test_agent_rows_pair_the_served_form_with_the_day_context(self):
        self.run_cd("--forms", "cd_fact_agent,cd_known")
        rows = self.rows()
        r = rows["cd-cd_known-kl-KK-1"]
        self.assertTrue(r["raw"]["completion"].startswith(cd.HEADER_START))
        self.assertEqual(r["raw"]["completion_ids"], tokenize(r["raw"]["completion"][len(cd.HEADER_START):]))
        self.assertFalse(r["raw"]["prompt"].endswith(cd.HEADER_START))
        self.assertNotIn("forced_header", r["raw"])
        self.assertNotIn("Context for today", r["raw"]["prompt"])
        self.assertIn("Context for today, not a request: today is Sunday 2026-11-01.\n\nYour notes from earlier days "
                      "(the latest value of each fact):\n- lab mate: Ana Ruiz", r["teacher"]["prompt"])
        self.assertTrue(r["teacher"]["prompt"].endswith(r["raw"]["prompt"][r["raw"]["prompt"].rindex("<|start|>user"):]))
        self.assertIn("cd-cd_fact_agent-N0001", rows)

    def test_a_recall_first_sample_becomes_the_second_turn_in_both_prompts(self):
        report = self.run_cd("--forms", "cd_obscure")
        r = self.rows()["cd-cd_obscure-kl-KO-1"]
        self.assertTrue(r["after_recall"])
        for p in (r["raw"]["prompt"], r["teacher"]["prompt"]):
            self.assertIn('"name": "recall"', p)
            self.assertIn('{"notes": []}', p)
        self.assertIn("Your notes from earlier days", r["teacher"]["prompt"])
        self.assertIn("I have no note of that.", r["raw"]["completion"])
        self.assertEqual(self.model.chats[0]["seed"], self.model.bodies[0]["seed"])  # the parse of the same sample
        self.assertEqual(report["by_form"]["cd_obscure"]["after_recall"], 1)

    def test_bad_samples_are_dropped_and_counted(self):
        pool = [{"item_id": "a", "form": "kl_people", "user": "calendar"},
                {"item_id": "b", "form": "kl_people", "user": "inexact"},
                {"item_id": "c", "form": "kl_people", "user": "Blind Guardian"}]
        self.pools.write_text("".join(json.dumps(r) + "\n" for r in pool))
        self.model.chat_query = "something else"
        report = self.run_cd("--forms", "cd_people")
        self.assertEqual(report["by_form"]["cd_people"]["dropped"],
                         {"another tool": 1, "inexact tokens": 1, "chat parse differs from the sample": 1})
        self.assertFalse((self.out / "cd-rows.jsonl").exists())

    def test_a_chat_parse_must_be_the_sampled_calls_exactly(self):
        pool = [{"item_id": "c", "form": "kl_people", "user": "Blind Guardian"},
                {"item_id": "d", "form": "kl_people", "user": "Blind Guardian twice"}]
        self.pools.write_text("".join(json.dumps(r) + "\n" for r in pool))
        self.model.chat_query = "Guardian"  # a substring of the sampled query is not the same call
        report = self.run_cd("--forms", "cd_people")
        self.assertEqual(report["by_form"]["cd_people"]["dropped"], {"chat parse differs from the sample": 2})
        self.assertEqual(cd.call_query({"function": {"name": "recall", "arguments": '{"query": "foo'}}), None)
        self.assertEqual(cd.call_query({"function": {"name": "recall", "arguments": '"foo"'}}), None)
        self.assertEqual(cd.call_query({"function": {"name": "recall", "arguments": '{"query": "foo"}'}}), "foo")

    def test_a_reply_that_quotes_call_markup_is_a_reply(self):
        self.model.completion = lambda prompt: (
            ' to=user<|message|>Try <atem:invoke name="recall"> to look things up.<|eot|>', "eos")
        report = self.run_cd("--forms", "cd_known")
        r = self.rows()["cd-cd_known-kl-KK-1"]
        self.assertFalse(r["after_recall"])
        self.assertEqual((report["by_form"]["cd_known"]["kept"], self.model.chats), (1, []))

    def test_a_row_written_before_a_crash_is_not_written_again(self):
        self.run_cd("--forms", "cd_known")
        (self.out / "cd-asked.jsonl").unlink()  # the crash came between the row and its asked line
        n = len(self.model.bodies)
        self.run_cd("--forms", "cd_known,cd_known")
        self.assertEqual((len(self.rows()), len(self.model.bodies)), (1, n))
        self.assertEqual(len((self.out / "cd-rows.jsonl").read_text().splitlines()), 1)

    def test_the_report_is_written_when_the_server_fails_mid_run(self):
        def down(prompt):
            raise ConnectionResetError("server gone")

        self.model.completion = down
        with self.assertRaises(Exception):
            self.run_cd("--forms", "cd_known")
        report = json.loads((self.out / "cd-report.json").read_text())
        self.assertIn("RemoteDisconnected", report["error"])  # the server dropped the request
        self.assertEqual(report["asked"], 0)

    def test_slot_and_temperature_options_reach_the_server_and_the_report(self):
        report = self.run_cd("--forms", "cd_fact", "--slot", "-1", "--temperature", "0", "--base-sha256", "cd" * 32)
        b = next(x for x in self.model.bodies if "lab mate" in x["prompt"])
        self.assertEqual((b["id_slot"], b["temperature"], b["top_k"]), (-1, 0.0, 0))
        self.assertEqual((report["slot"], report["base_sha256"], report["sampler_host"], report["sampler"]["temperature"]),
                         (-1, "cd" * 32, "127.0.0.1", 0.0))
        self.assertEqual(self.rows()["cd-cd_fact-N0001"]["student"]["temperature"], 0.0)

    def test_base_student_samples_with_every_adapter_off(self):
        self.run_cd("--forms", "cd_fact", "--student-adapter", "none")
        b = next(x for x in self.model.bodies if "lab mate" in x["prompt"])
        self.assertEqual(b["lora"], [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 0.0}])

    def test_with_no_notes_the_teacher_prompt_is_the_student_s(self):
        (self.runs / "w-test" / "6" / "notes.json").write_text(json.dumps({"notes": []}))
        self.run_cd("--forms", "cd_fact")
        r = self.rows()["cd-cd_fact-N0001"]
        self.assertEqual(r["teacher"]["prompt"], r["raw"]["prompt"])

    def test_the_student_is_found_by_path_or_the_run_stops(self):
        self.loaded[1]["path"] = "/data/afterglow/adapters/w-test-d4.gguf"
        with self.assertRaises(SystemExit):
            self.run_cd("--forms", "cd_fact")

    def test_a_deadline_stops_cleanly_and_a_rerun_resumes(self):
        report = self.run_cd("--forms", "cd_fact,cd_known", "--stop-at", "00:00")
        self.assertEqual((report["asked"], report["stopped_at_deadline"]), (0, True))
        self.assertEqual(self.run_cd("--forms", "cd_fact,cd_known")["asked"], 3)
        n = len(self.model.bodies)
        again = self.run_cd("--forms", "cd_fact,cd_known")
        self.assertEqual((again["asked"], len(self.model.bodies)), (3, n))  # nothing asked twice

    def test_swapped_is_not_a_form(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            self.run_cd("--forms", "cd_swapped")
        self.run_cd("--forms", "cd_people")
        self.assertNotIn("cd-cd_people-kl-KS-1", self.rows())  # the swapped pool row is never read


if __name__ == "__main__":
    unittest.main()
