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
MAX_TOKENS = 384  # compact JSON for six observations fits; dense UI needed more than 256
MAX_PROMPT_PIXELS = 1024 * 1024

# A tiny valid PNG (tests use it for PNG bytes); the warm pass instead renders
# a 224x224 white image, since 1x1 crops can fail image processors.
WARM_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
    "AAAABJRU5ErkJggg=="
)
WARM_SIZE = 224

# Measured on five real crops (research/vision-probe, 4 Nov): this wording kept every emoji, number and control state
# and answered in 0.6-1.5 s on an M4 Max. Pretty-printed JSON ran out of tokens on dense UI; a one-item example made
# the model list only one item.
FACTS_PROMPT = (
    "List what is visible in this screen crop as ONE line of compact JSON with no spaces or line breaks: "
    '{"observations":[{"id":"v0","kind":"text|emoji|icon|chart|photo|control","text":"...","state":"",'
    '"box":[x,y,w,h]}],"unknowns":[]}. '
    "Put controls and their state first (disabled, selected, error, warning), then warnings and errors, then the rest. "
    "Copy every text exactly, with its emoji and numbers and units. Boxes are fractions of the crop, two decimals: "
    "x and y are the left and top edges, w and h the width and height. "
    "Each emoji and each icon is its own observation, even inside a line of text; describe icons by shape. "
    "For a photo, icon or chart, text is a short plain description of what it shows. "
    "At most 6 observations. Say unknown rather than guessing."
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


def _salvage(text):
    """The complete observations of a reply cut off at the token limit, or None.
    The prompt puts controls and warnings first, so the first items are the
    ones that matter; the cut-off tail is dropped."""
    key = text.find('"observations"')
    i = text.find("[", key) if key >= 0 else -1
    if i < 0:
        return None
    dec, obs, i = json.JSONDecoder(), [], i + 1
    while True:
        while i < len(text) and text[i] in " \t\r\n,":
            i += 1
        if i >= len(text) or text[i] != "{":
            break
        try:
            item, i = dec.raw_decode(text, i)
        except ValueError:
            break
        if isinstance(item, dict):
            obs.append(item)
    return {"observations": obs, "unknowns": []} if obs else None


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
                if os.environ.get("AFTERGLOW_VLM_DEBUG") == "1":  # local diagnosis only; the text is the screen's
                    print(f"vlm_worker: raw {gen_text!r}"[:1200], file=sys.stderr, flush=True)
                facts = _extract_json(gen_text or "")
                truncated = False
                if facts is None or not isinstance(facts.get("observations"), list):
                    facts = _salvage(gen_text or "")  # a busy crop can run past MAX_TOKENS
                    truncated = facts is not None
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
                      f"ms={ms} finish_reason={finish} text_len={len(gen_text or '')} truncated={truncated}",
                      file=sys.stderr, flush=True)
                _out(out, {"ok": True, "crop_id": cid, "observations": obs,
                           "unknowns": unk, "ms": ms, "finish_reason": finish, "truncated": truncated})
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
