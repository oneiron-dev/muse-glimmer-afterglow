"""VLM JSON-lines worker: load once, warm image pass, {"ready": true}, then facts per crop.

Owns every MLX import in this child process only (it runs under the vlm venv,
never the app interpreter). Stdout is protocol only. Exits on shutdown, EOF,
or parent death (getppid watcher, mid-inference ok).

Verified against the mlx-vlm 0.7.11 wheel: load(path_or_repo) takes a local
path or a repo id; prompts are built with prompt_utils.apply_chat_template
(processor, model.config, text, num_images=1, enable_thinking=False) because
generate does not template; generate(model, processor, prompt, image_path,
enable_thinking=False, max_tokens, temperature) returns .text.
"""
import argparse
import base64
import json
import os
import sys
import tempfile
import threading
import time

DEFAULT_MODEL = "mlx-community/Qwen3.5-4B-MLX-4bit"
MAX_TOKENS = 256
MAX_PROMPT_PIXELS = 1024 * 1024

# A tiny valid PNG (tests use it for PNG bytes); the warm pass instead renders
# a 224x224 white image, since 1x1 crops can fail image processors.
WARM_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
    "AAAABJRU5ErkJggg=="
)
WARM_SIZE = 224

FACTS_PROMPT = (
    "Look at this screenshot crop and report only plain visible facts as compact JSON "
    "(no extra spaces or newlines). No opinions, no guesses. Say unknown rather than guessing.\n"
    "List every visible text including emoji, the icons, a chart's values, labels and units, "
    "and the layout. Each item gets a box as fractions of the crop: [x, y, width, height], "
    "origin top-left. x and y are the left and top edges (0 to 1). width is the right edge "
    "minus x, height is the bottom edge minus y. Both sizes are positive, and x plus width "
    "and y plus height must stay at or below 1.\n"
    'Shape: {"observations": [{"id": "v0", "kind": "text|emoji|icon|chart|layout|object", '
    '"text": "exact visible text or emoji", "label": "short name", "box": [x, y, w, h]}], '
    '"unknowns": ["what you could not read"]}. Number the ids v0, v1, and so on in order, '
    "with no gaps. At most 6 observations. Answer with the JSON object only."
)


def start_parent_watcher(parent_pid, interval=0.5):
    try:
        os.getppid()
    except Exception:
        return None

    def _watch():
        while True:
            time.sleep(interval)
            try:
                if os.getppid() != parent_pid:
                    print("vlm_worker: parent gone, exiting", file=sys.stderr, flush=True)
                    os._exit(0)
            except Exception:
                continue

    t = threading.Thread(target=_watch, daemon=True)
    t.start()
    return t


def _out(out, obj):
    out.write(json.dumps(obj, ensure_ascii=False) + "\n")
    out.flush()


def _extract_json(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        doc = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def _warm_image(folder):
    """A plain 224x224 white PNG for the warm image pass."""
    warm = os.path.join(folder, "warm.png")
    try:
        from PIL import Image
        Image.new("RGB", (WARM_SIZE, WARM_SIZE), (255, 255, 255)).save(warm)
    except Exception:
        with open(warm, "wb") as f:
            f.write(base64.b64decode(WARM_PNG_B64))
    return warm


def _fit_pixels(path, folder):
    """An aspect-preserving copy at most 1 megapixel (fractions are unchanged
    by resize). Falls back to the original path when PIL is missing."""
    try:
        from PIL import Image
        img = Image.open(path)
        w, h = img.size
        if w * h <= MAX_PROMPT_PIXELS:
            return path
        scale = (MAX_PROMPT_PIXELS / (w * h)) ** 0.5
        small = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.BICUBIC)
        out = os.path.join(folder, "small.png")
        small.save(out)
        return out
    except Exception:
        return path


def run(args, out):
    start_parent_watcher(os.getppid())
    sys.stdout = sys.stderr
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    # Never fetch weights implicitly: the app downloads through Download first.
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        from mlx_vlm import generate, load
        from mlx_vlm.prompt_utils import apply_chat_template
    except Exception as e:
        try:
            _out(out, {"ready": False, "error": f"mlx-vlm is not installed: {e}"})
        except BrokenPipeError:
            pass
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1
    try:
        model, processor = load(args.model)
    except Exception as e:
        try:
            _out(out, {"ready": False, "error": f"load failed: {e}"})
        except BrokenPipeError:
            pass
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="vlm-warm-") as folder:
        try:
            warm = _warm_image(folder)
            t0 = time.time()
            prompt = apply_chat_template(
                processor, model.config,
                "Report one JSON fact: {\"observations\": [], \"unknowns\": [\"warmup\"]}.",
                num_images=1, enable_thinking=False)
            res = generate(model, processor, prompt, warm,
                           max_tokens=32, temperature=0.0, enable_thinking=False)
            _ = res.text if hasattr(res, "text") else str(res)
            print(f"vlm_worker: warm ok in {time.time() - t0:.1f}s", file=sys.stderr, flush=True)
        except Exception as e:
            try:
                _out(out, {"ready": False, "error": f"warmup failed: {e}"})
            except BrokenPipeError:
                pass
            import traceback
            traceback.print_exc(file=sys.stderr)
            return 1
    try:
        _out(out, {"ready": True, "model": args.model})
    except BrokenPipeError:
        return 0
    with tempfile.TemporaryDirectory(prefix="vlm-crops-") as folder:
        for raw in sys.stdin:
            if not raw.strip():
                continue
            try:
                doc = json.loads(raw)
            except ValueError:
                try:
                    _out(out, {"ok": False, "error": "Invalid JSON request"})
                except BrokenPipeError:
                    return 0
                continue
            if not isinstance(doc, dict):
                try:
                    _out(out, {"ok": False, "error": "Request must be an object"})
                except BrokenPipeError:
                    return 0
                continue
            if doc.get("shutdown") is True or doc.get("command") == "shutdown":
                return 0
            cid = doc.get("crop_id")
            path = doc.get("path")
            if not isinstance(cid, str) or not cid:
                try:
                    _out(out, {"ok": False, "error": "crop_id must be text"})
                except BrokenPipeError:
                    return 0
                continue
            if not isinstance(path, str) or not os.path.isfile(path):
                try:
                    _out(out, {"ok": False, "error": f"Image file not found: {path}"})
                except BrokenPipeError:
                    return 0
                continue
            question = doc.get("question")
            text = FACTS_PROMPT if not question else FACTS_PROMPT + f"\nQuestion: {question}"
            t0 = time.monotonic()
            try:
                prompt = apply_chat_template(processor, model.config, text, num_images=1,
                                             enable_thinking=False)
                res = generate(model, processor, prompt, _fit_pixels(path, folder),
                               max_tokens=MAX_TOKENS, temperature=0.0, enable_thinking=False)
                gen_text = res.text if hasattr(res, "text") else str(res)
                finish = getattr(res, "finish_reason", None)
                facts = _extract_json(gen_text or "")
                ms = int((time.monotonic() - t0) * 1000)
                if facts is None or not isinstance(facts.get("observations"), list):
                    print(f"vlm_worker: crop {cid} invalid_json finish_reason={finish} "
                          f"text_len={len(gen_text or '')} ms={ms}", file=sys.stderr, flush=True)
                    _out(out, {"ok": False,
                               "error": f"invalid_json finish_reason={finish} "
                                        f"text_len={len(gen_text or '')}"})
                    continue
                obs, unk = facts.get("observations"), facts.get("unknowns", [])
                print(f"vlm_worker: crop {cid} ok obs={len(obs)} unknowns={len(unk) if isinstance(unk, list) else '?'} "
                      f"ms={ms} finish_reason={finish} text_len={len(gen_text or '')}",
                      file=sys.stderr, flush=True)
                _out(out, {"ok": True, "crop_id": cid, "observations": obs,
                           "unknowns": unk, "ms": ms, "finish_reason": finish})
            except BrokenPipeError:
                return 0
            except Exception as e:
                import traceback
                traceback.print_exc(file=sys.stderr)
                try:
                    _out(out, {"ok": False, "error": f"{type(e).__name__}: {e}"[:500]})
                except BrokenPipeError:
                    return 0
    return 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Afterglow persistent VLM worker")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    return ap.parse_args(argv)


def main(argv=None):
    return run(parse_args(argv), sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
