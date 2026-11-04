"""Qwen JSON-lines worker and whisper parent-watch wrapper.

Qwen: load once, silence warmup, {"ready": true}, then {"path","language"}
-> {"ok": true, "text": ...} / {"ok": false, ...}. Stdout is protocol only.
Exit on shutdown, EOF, or parent death (getppid watcher, mid-inference ok).
Whisper (--whisper): spawn server, print {"server_pid": N}, kill server when
the parent dies, forward SIGTERM/SIGINT.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave

DEFAULT_MODEL = "mlx-community/Qwen3-ASR-1.7B-8bit"


def normalize_language(lang):
    if lang is None:
        return None
    s = lang.strip().lower()
    if s in ("", "auto"):
        return None
    if s == "en":
        return "English"
    if s == "ja":
        return "Japanese"
    return lang.strip()


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
                    print("asr_worker: parent gone, exiting", file=sys.stderr, flush=True)
                    os._exit(0)
            except Exception:
                continue

    t = threading.Thread(target=_watch, daemon=True)
    t.start()
    return t


def _out(out, obj):
    out.write(json.dumps(obj, ensure_ascii=False) + "\n")
    out.flush()


def _silence(path):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 8000)


def _text(res):
    if isinstance(res, list):
        res = res[0] if res else {}
    if isinstance(res, dict):
        return str(res.get("text") or "")
    return str(getattr(res, "text", "") or "")


def run_qwen(args, out):
    start_parent_watcher(os.getppid())
    sys.stdout = sys.stderr
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    try:
        from mlx_audio.stt import load
        model = load(args.model)
    except Exception as e:
        try:
            _out(out, {"ready": False, "error": f"load failed: {e}"})
        except BrokenPipeError:
            pass
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1
    fd, tmp = tempfile.mkstemp(prefix="asr-warm-", suffix=".wav")
    os.close(fd)
    try:
        _silence(tmp)
        t0 = time.time()
        model.generate(tmp, verbose=False, max_tokens=16)
        print(f"asr_worker: warm ok in {time.time() - t0:.1f}s", file=sys.stderr, flush=True)
    except Exception as e:
        try:
            _out(out, {"ready": False, "error": f"warmup failed: {e}"})
        except BrokenPipeError:
            pass
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    try:
        _out(out, {"ready": True, "model": args.model})
    except BrokenPipeError:
        return 0
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
        path = doc.get("path")
        if not isinstance(path, str) or not os.path.isfile(path):
            try:
                _out(out, {"ok": False, "error": f"Audio file not found: {path}"})
            except BrokenPipeError:
                return 0
            continue
        try:
            lang = normalize_language(doc.get("language"))
            res = model.generate(path, language=lang, verbose=False)
            _out(out, {"ok": True, "text": _text(res).strip()})
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


def _kill(proc):
    try:
        proc.terminate()
        proc.wait(timeout=2)
        return
    except Exception:
        pass
    try:
        proc.kill()
        proc.wait(timeout=2)
    except Exception:
        pass


def run_whisper(args):
    parent = os.getppid()
    try:
        proc = subprocess.Popen([args.binary, "-m", args.model, "--host", args.host,
                                 "--port", str(args.port)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"wrap: spawn failed: {e!r}", file=sys.stderr, flush=True)
        return 1
    try:
        sys.stdout.write(json.dumps({"server_pid": proc.pid}) + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        _kill(proc)
        return 0

    def _term(signum, frame):
        _kill(proc)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    while True:
        time.sleep(0.2)
        if os.getppid() != parent:
            _kill(proc)
            return 0
        code = proc.poll()
        if code is not None:
            return code if 0 <= code < 128 else 1


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Afterglow persistent ASR worker")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--whisper", action="store_true")
    ap.add_argument("--binary", default=None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", default=None)
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.whisper:
        if not args.binary or not args.model or not args.port:
            print("wrap needs --binary, --model, --port", file=sys.stderr, flush=True)
            return 2
        return run_whisper(args)
    return run_qwen(args, sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
