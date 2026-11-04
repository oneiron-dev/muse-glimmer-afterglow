"""End-to-end explainer check on a scratch app (127.0.0.1:8666), beside the five probe crops (run_crops.py):
  1. the Settings > Models page, shot headless; the form column cropped with sips; OCR boxes from the page's text
     (DOM ranges stand in for the fn helper's OCR); "What is this screen for, and can I train here?"
  2. the same crop through POST /api/ambient with a spoken question (macOS say, WAV), as the fn helper sends it
  3. an explainer of the chat's reply (from_turn 1) and a general question, both without a crop
  4. two follow-ups on the screen explanation, timing the first streamed word, then 420x520 shots of every window
  python3 run_screen.py <label>     (about 6 Glimmer calls; writes <label>-screen.json and shots beside this file)
"""
import base64
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DRIVE = "/Users/olety/Desktop/temp/meta-hack-20261014/builders/afterglow-ui-20261104/tools/drive_page.mjs"
OCR_JS = (HERE / "ocr_range.js").read_text().strip()
BASE = "http://127.0.0.1:8666"
TMP = Path("/Users/olety/.claude/jobs/6650f9fe/tmp")
ENV = {"CLAUDE_JOB_DIR": "/Users/olety/.claude/jobs/6650f9fe", "PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin"}


def call(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read()), round(time.perf_counter() - t0, 2)


def drive(url, prefix, steps):
    out = subprocess.run(["node", DRIVE, url, str(prefix), json.dumps(steps)], capture_output=True, text=True,
                         timeout=180, env=ENV)
    return out.stdout.strip().splitlines()


def ask(tid, text):
    """POST /api/explain/ask; the events with the seconds each arrived."""
    req = urllib.request.Request(BASE + "/api/explain/ask", data=json.dumps({"id": tid, "text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    t0, events = time.perf_counter(), []
    with urllib.request.urlopen(req, timeout=600) as r:
        for line in r:
            line = line.decode().strip()
            if line.startswith("data:"):
                ev = json.loads(line[5:])
                ev["at_s"] = round(time.perf_counter() - t0, 2)
                events.append(ev)
    return events


def screen_crop():
    """Settings > Models shot, its form column as the crop, and the page's text boxes inside it (crop pixels)."""
    lines = drive(f"{BASE}/settings?section=model", HERE / "settings",
                  [{"wait": 2500}, {"eval": OCR_JS}, {"shot": "models", "w": 1440, "h": 1000}])
    page = json.loads(next(l for l in lines if l.startswith("eval: "))[6:])
    col = page["formCol"] or page["mainRect"]
    x, y = round(col["x"]), round(max(col["y"], 0))
    w, h = round(col["w"]), round(min(col["h"], page["height"] - y, 640))
    crop = HERE / "settings-crop.png"
    subprocess.run(["sips", "-c", str(h), str(w), "--cropOffset", str(y), str(x), str(HERE / "settings-models.png"),
                    "--out", str(crop)], capture_output=True, check=True)
    boxes = [{"text": b["text"], "box": [b["box"][0] - x, b["box"][1] - y, b["box"][2], b["box"][3]]}
             for b in page["boxes"]
             if b["box"][0] >= x and b["box"][1] >= y and b["box"][0] + b["box"][2] <= x + w + 1
             and b["box"][1] + b["box"][3] <= y + h + 1]
    ocr = {"width": w, "height": h, "boxes": boxes}
    (HERE / "settings-ocr.json").write_text(json.dumps(ocr, ensure_ascii=False, indent=1))
    return crop.read_bytes(), ocr


def window(tid, name):
    return drive(f"{BASE}/explain?id={tid}", HERE / name,
                 [{"wait": 1200}, {"shot": "window", "w": 420, "h": 520},
                  {"eval": "JSON.stringify({overflowX: document.documentElement.scrollWidth > innerWidth, "
                           "marks: document.querySelectorAll('.ex-mark').length, "
                           "chat: document.querySelectorAll('.ex-message').length})"},
                  {"shot": "full", "w": 420, "h": 520, "full": True}])[-3:]


def explained(tid):
    doc, _ = call(f"/api/explain?id={tid}")
    rec = next(r for r in doc["records"] if r["role"] == "assistant")
    return {"id": tid, "sight": doc["sight"], "title": doc["title"], "answer": doc["spec"]["answer"],
            "body": doc["spec"]["body"], "ms": rec.get("ms"), "errors": rec.get("errors")}


def main():
    label = sys.argv[1]
    mode = call("/api/models")[0].get("vision_mode")
    png, ocr = screen_crop()
    out = {"mode": mode, "ocr_boxes": len(ocr["boxes"])}
    made, secs = call("/api/explain", {"question": "What is this screen for, and can I train here?",
                                       "crop_png_b64": base64.b64encode(png).decode(), "ocr": ocr,
                                       "app_name": "Afterglow", "ax": "Settings, Models"})
    out["screen"] = {"total_s": secs, **explained(made["id"])}
    wav = TMP / "question.wav"
    subprocess.run(["say", "-o", str(wav), "--data-format=LEI16@16000", "What is this part for?"], check=True)
    amb, secs = call("/api/ambient", {"audio_b64": base64.b64encode(wav.read_bytes()).decode(), "mime": "audio/wav",
                                      "crop_png_b64": base64.b64encode(png).decode(), "ocr": ocr,
                                      "app_name": "Afterglow", "ax": "Settings, Models"})
    out["ambient"] = {"total_s": secs, "result": amb, **(explained(amb["id"]) if amb.get("mode") == "explain" else {})}
    made, secs = call("/api/explain", {"question": "Explain this reply", "source": "chat", "from_turn": 1})
    out["chat_reply"] = {"total_s": secs, **explained(made["id"])}
    made, secs = call("/api/explain", {"question": "How does a LoRA adapter change what a model says?",
                                       "source": "chat"})
    out["concept"] = {"total_s": secs, **explained(made["id"])}
    tid = out["screen"]["id"]
    out["followups"] = []
    for text in ("What would I need to train here?", "Show me the steps to add vision instead"):
        events = ask(tid, text)
        first = next((e["at_s"] for e in events if e["type"] == "content"), None)
        out["followups"].append({"text": text, "first_word_s": first, "total_s": events[-1]["at_s"],
                                 "kinds": [e["type"] for e in events],
                                 "answer": "".join(e["text"] for e in events if e["type"] == "content"),
                                 "final": next((e["text"] for e in events if e["type"] == "answer"), None),
                                 "spec": next((e["spec"] for e in events if e["type"] == "spec"), None)})
    for key in ("screen", "ambient", "chat_reply", "concept"):
        if out[key].get("id"):
            out[key]["page"] = window(out[key]["id"], f"{label}-{key}")
    (HERE / f"{label}-screen.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    main()
