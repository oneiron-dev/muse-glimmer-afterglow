import base64, json, pathlib, time, urllib.request
out = pathlib.Path(__file__).resolve().parent
request = {"question": "what is this screen for?", "app_name": "Afterglow Settings > Model", "source": "chat", "ocr": json.loads((out / "settings-ocr.json").read_text()), "crop_png_b64": base64.b64encode((out / "settings-model.png").read_bytes()).decode()}
start = time.monotonic()
req = urllib.request.Request("http://127.0.0.1:8666/api/explain", data=json.dumps(request).encode(), headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=120) as response:
    created = json.load(response)
with urllib.request.urlopen("http://127.0.0.1:8666/api/explain?id=" + created["id"]) as response:
    doc = json.load(response)
(out / "live-explain.json").write_text(json.dumps({"created": created, "elapsed_s": round(time.monotonic()-start, 2), "explanation": doc}, indent=2) + "\n")
print(json.dumps({"created": created, "spec": doc["spec"], "elapsed_s": round(time.monotonic()-start, 2)}, indent=2))
