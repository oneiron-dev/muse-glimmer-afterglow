"""Live smoke test: the explainer with Glimmer's own vision (vision mode "glimmer"). One explain request.

Run against a scratch app whose agent is a llama.cpp server with its vision projector loaded (never the real runs/):
  python3 app/tests/smoke_explainer_glimmer.py [--base http://127.0.0.1:8651]
Prints PASS or FAIL with the reason and exits 0 or 1. If the app's vision mode is not "glimmer" but the server reads
images, it chooses Glimmer's own vision in that app's Settings > Models first (the scratch app's runs only).
Checks: the picture reached Glimmer (sight "image"), the answer names the thumbs up in the 👍 crop, the figure is
pins or the picture with text, the crop is served as PNG, and the stored answer carries no model error.
Not a unit test (the file name keeps it out of `unittest discover`).
"""
import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CROP = Path(__file__).resolve().parent / "fixtures" / "explain-thumbs.png"  # research/vision-probe/crop-1-thumbs.png
QUESTION = "What is this emoji?"
OCR = [{"text": "Perfect, short and direct IS great.", "x": 0.1069, "y": 0.3806, "w": 0.8075, "h": 0.3254}]  # the fn helper's OCR


def call(base, path, body=None, timeout=180):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fail(why):
    print(f"FAIL: {why}")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8651")
    base = ap.parse_args().base.rstrip("/")
    try:
        models = call(base, "/api/models")
    except (OSError, ValueError) as e:
        fail(f"no app at {base}: {e}")
    if models.get("vision_mode") != "glimmer":
        sees = (models.get("agent_vision") or {}).get("available")
        if sees is not True:
            fail(f"vision mode is {models.get('vision_mode')!r} and the agent's server does not read images "
                 f"(agent_vision {models.get('agent_vision')!r}); is the projector loaded?")
        try:
            models = call(base, "/api/models/use", {"slot": "vision", "id": "glimmer-mmproj"})
        except urllib.error.HTTPError as e:
            fail(f"could not choose Glimmer's own vision: HTTP {e.code} {e.read()[:200]!r}")
        if models.get("vision_mode") != "glimmer":
            fail(f"chose Glimmer's own vision, but the mode is {models.get('vision_mode')!r}")
    t0 = time.perf_counter()
    try:
        made = call(base, "/api/explain", {"question": QUESTION, "crop_png_b64": base64.b64encode(CROP.read_bytes()).decode(),
                                           "ocr": OCR, "app_name": "Afterglow", "source": "ambient"})
        doc = call(base, f"/api/explain?id={made['id']}")
    except urllib.error.HTTPError as e:
        fail(f"POST /api/explain: HTTP {e.code} {e.read()[:300]!r}")
    secs = round(time.perf_counter() - t0, 1)
    rec = next((r for r in doc["records"] if r.get("role") == "assistant"), {})
    answer, body = doc["spec"]["answer"], doc["spec"]["body"]
    print(f"{made['id']} in {secs}s; sight {doc['sight']}; figure {body['type']}; answer: {answer}")
    if doc.get("sight") != "image":
        fail(f"the picture did not go to Glimmer (sight {doc.get('sight')!r})")
    if rec.get("errors"):
        fail(f"the model call reported {rec['errors']}")
    if "thumb" not in answer.lower() and "👍" not in answer:
        fail("the answer does not name the thumbs up")
    if body["type"] not in ("screenshot", "text"):
        fail(f"figure {body['type']!r}: with a circled area it should be pins or the picture")
    try:
        with urllib.request.urlopen(base + doc["crop_url"], timeout=30) as r:
            if r.headers.get("Content-Type") != "image/png" or r.read(8) != b"\x89PNG\r\n\x1a\n":
                fail("the crop is not served as PNG")
    except (OSError, TypeError) as e:
        fail(f"the crop could not be read: {e}")
    print(f"PASS: Glimmer read the picture and answered in {secs}s; window {base}{made['url']}")


if __name__ == "__main__":
    main()
