"""End-to-end explainer check on a scratch app: the five probe crops (research/vision-probe), each with the OCR the
fn helper would send (crops-ocr.json beside this file), POST /api/explain, then GET it and a headless 420x520 shot.

  python3 run_crops.py <label> [crop-file ...]      (scratch app on 127.0.0.1:8666; one Glimmer call per crop)
Writes <label>-results.json and <label>-<crop>-window.png beside this file.
"""
import base64
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE = Path("/Users/olety/Desktop/temp/meta-hack-20261014/builders/afterglow-ui-20261104/research/vision-probe")
DRIVE = Path("/Users/olety/Desktop/temp/meta-hack-20261014/builders/afterglow-ui-20261104/tools/drive_page.mjs")
BASE = "http://127.0.0.1:8666"


def call(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())


def shot(tid, name):
    steps = [{"wait": 1200}, {"shot": "window", "w": 420, "h": 520},
             {"eval": "JSON.stringify({img: [...document.images].map(i => i.naturalWidth), "
                      "overflowX: document.documentElement.scrollWidth > innerWidth, "
                      "marks: document.querySelectorAll('.ex-mark').length})"}]
    out = subprocess.run(["node", str(DRIVE), f"{BASE}/explain?id={tid}", str(HERE / name), json.dumps(steps)],
                         capture_output=True, text=True, timeout=120,
                         env={"CLAUDE_JOB_DIR": "/Users/olety/.claude/jobs/6650f9fe", "PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin"})
    return out.stdout.strip().splitlines()


def main():
    label, only = sys.argv[1], set(sys.argv[2:])
    crops = json.loads((PROBE / "crops.json").read_text())
    ocr = json.loads((HERE / "crops-ocr.json").read_text())
    mode = call("/api/models").get("vision_mode")
    results = []
    for c in crops:
        if only and c["file"] not in only:
            continue
        png = (PROBE / c["file"]).read_bytes()
        t0 = time.perf_counter()
        made = call("/api/explain", {"question": c["question"], "crop_png_b64": base64.b64encode(png).decode(),
                                     "ocr": ocr.get(c["file"], []), "app_name": "Afterglow", "source": "ambient"})
        total = round(time.perf_counter() - t0, 2)
        doc = call(f"/api/explain?id={made['id']}")
        rec = next(r for r in doc["records"] if r["role"] == "assistant")
        stem = c["file"].removesuffix(".png")
        page = shot(made["id"], f"{label}-{stem}")
        results.append({"file": c["file"], "question": c["question"], "expect": c["expect"], "mode": mode,
                        "sight": doc["sight"], "id": made["id"], "total_s": total, "ms": rec.get("ms"),
                        "answered": rec.get("answered"), "title": doc["title"], "answer": doc["spec"]["answer"],
                        "body": doc["spec"]["body"], "anchors": doc["anchors"], "errors": rec.get("errors"),
                        "page": page})
        print(f"{c['file']}: {total}s sight={doc['sight']} answer={doc['spec']['answer']!r}", flush=True)
    out = HERE / f"{label}-results.json"
    done = {r["file"] for r in results}
    old = [r for r in json.loads(out.read_text()) if r["file"] not in done] if out.exists() else []
    order = [c["file"] for c in crops]
    out.write_text(json.dumps(sorted(old + results, key=lambda r: order.index(r["file"])), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
