"""One entry point for every text-generation call the night job makes (the "teacher").

`complete(teacher, messages, ...)` sends one chat call and logs its tokens. Teachers:
  glimmer    Muse Glimmer 30B on our llama-server (local; the product path). Uses slot 1.
  spark-sub  Muse Spark through the development subscription proxy (synthetic data only).
The Spark-vs-Glimmer teacher comparison is the `--teacher` flag of the callers.
Keys are read from environment variables set for one command; they are never logged.
"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


def serving_base_url():
    """The Glimmer server's proxy URL on the default serving box (app/serve/hosts.json)."""
    hosts = json.loads((Path(__file__).resolve().parent.parent / "app" / "serve" / "hosts.json").read_text())
    return f"http://{hosts['hosts'][hosts['default']]['tailnet']}:{hosts['proxy_port']}/v1"


TEACHERS = {
    "glimmer": {
        "base_url_env": "GLIMMER_BASE_URL",
        "base_url": serving_base_url(),
        "model": "muse-glimmer-30b",
        "expect_model": "muse-glimmer-30b",
        "key_env": None,
        "extra": {"id_slot": 1, "cache_prompt": True, "chat_template_kwargs": {"reasoning_strength": "minimal"}},
    },
    "spark-sub": {
        "base_url_env": "SPARK_SUB_BASE_URL",
        "base_url": "http://arch.tail87c1d7.ts.net:8317/v1",
        "model": "muse-spark-1.3-contributor-sub",
        "expect_model": "muse-spark-1.3-contributor",
        "key_env": "CLIPROXY_KEY",
        "extra": {},
    },
}


class TeacherError(RuntimeError):
    pass


def complete(teacher, messages, max_tokens=900, temperature=0.0, seed=20261027, log_path=None, purpose="", timeout=600):
    cfg = TEACHERS[teacher]
    base_url = os.environ.get(cfg["base_url_env"], cfg["base_url"]).rstrip("/")
    body = {"model": cfg["model"], "messages": messages, "max_tokens": max_tokens,
            "temperature": temperature, "seed": seed, "stream": False}
    body.update(cfg["extra"])
    headers = {"Content-Type": "application/json", "User-Agent": "afterglow-night/0.1"}
    if cfg["key_env"]:
        key = os.environ.get(cfg["key_env"])
        if not key:
            raise TeacherError(f"{cfg['key_env']} is not set")
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(f"{base_url}/chat/completions", data=json.dumps(body).encode(), headers=headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            reply = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise TeacherError(f"{teacher} HTTP {e.code}: {e.read()[:500].decode(errors='replace')}") from e
    latency = round(time.time() - t0, 2)
    if reply.get("model") != cfg["expect_model"]:
        raise TeacherError(f"{teacher} replied as model {reply.get('model')!r}, expected {cfg['expect_model']!r}; stopping")
    choice = reply["choices"][0]
    usage = reply.get("usage", {})
    if log_path:
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "teacher": teacher, "model": reply.get("model"),
                  "purpose": purpose, "prompt_tokens": usage.get("prompt_tokens"),
                  "completion_tokens": usage.get("completion_tokens"), "finish_reason": choice.get("finish_reason"),
                  "latency_s": latency}
        with open(log_path, "a") as f:
            f.write(json.dumps(record) + "\n")
    if not (choice["message"].get("content") or "").strip():
        # An empty answer (all tokens spent on reasoning, or a changed server template) must stop the run, not
        # pass as an item with zero rows.
        raise TeacherError(f"{teacher} returned empty content (finish {choice.get('finish_reason')}, "
                           f"{usage.get('completion_tokens')} completion tokens)")
    return choice["message"].get("content") or "", choice.get("finish_reason"), usage
