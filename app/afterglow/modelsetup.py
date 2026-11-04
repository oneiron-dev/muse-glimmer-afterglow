"""Model setup for a local-first app: what this machine can run, and where the agent's model comes from.

Three sources, one saved choice (runs/_settings/model.json):
  server  any OpenAI-compatible URL (llama.cpp, LM Studio, Ollama). Chat works on every one; the nightly updates
          need a server that applies the night's adapter on each request, which today is llama.cpp's llama-server.
  gguf    a model file on this machine; the app starts llama-server on it (when llama-server is installed).
  hf      the official GGUF from Hugging Face (meta-models/Muse-Glimmer-30B-GGUF), downloaded to the models folder,
          checked against its published sha256, then used as a gguf.
The sizes are measured: the Q4_K_M file is 16.8 GB and serving it takes about 18 GB with its context; a night's
training peaked at 20.2 GiB on a 24 GB GPU (night 9), so the nights need a 24 GB NVIDIA GPU.
"""
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import paths

SERVE_GB = 18.0          # Glimmer 30B Q4_K_M with its context, to serve
NIGHT_GPU_GB = 24        # the GPU class the nights need
NIGHT_PEAK_GIB = 20.2    # night 9's measured training peak
HF_REPO = "meta-models/Muse-Glimmer-30B-GGUF"
HF_FILES = {  # the files the app offers, with what each is for; sizes and hashes come from the Hub
    "Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf": "Recommended. The file Afterglow is measured on.",
    "Muse-Glimmer-30B-KQuant-Dynamic-Q4_K_XL.gguf": "A little more exact, 3 GB larger.",
}
LOCAL_PORT = 18650


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def settings_file():
    return paths.runs_root() / "_settings" / "model.json"


def saved():
    f = settings_file()
    return json.loads(f.read_text()) if f.exists() else None


def save(doc):
    f = settings_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({**doc, "set_at": now_iso()}, indent=1) + "\n")
    return saved()


def models_dir():
    return Path(os.environ.get("AFTERGLOW_MODELS") or Path.home() / "Models" / "Afterglow")


# --- hardware --------------------------------------------------------------------------------------------------

def _run(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def nvidia_gpus():
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"])
    gpus = []
    for line in (out or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3 and parts[1].isdigit():
            # GB as the makers label them (GiB): a 24 GB card reports about 24564 MiB
            gpus.append({"name": parts[0], "memory_gb": round(int(parts[1]) / 1024),
                         "used_gb": round(int(parts[2] or 0) / 1024, 1)})
    return gpus


def hardware():
    """This machine, and what it can do with Glimmer: serve it, train its nights, or neither (plain words)."""
    system = platform.system()
    total = None
    if system == "Darwin":
        m = _run(["sysctl", "-n", "hw.memsize"])
        total = int(m) / 2**30 if m and m.isdigit() else None  # GB as Apple labels it (64 GB = 64 GiB)
        chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.machine()
    else:
        chip = platform.processor() or platform.machine()
        try:
            kb = int(re.search(r"MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text()).group(1))
            total = kb / 2**20
        except (OSError, AttributeError, ValueError):
            pass
    apple = system == "Darwin" and platform.machine() == "arm64"
    gpus = nvidia_gpus()
    best = max((g["memory_gb"] for g in gpus), default=0)
    out = {"system": system, "chip": chip, "memory_gb": round(total) if total else None, "apple_silicon": apple,
           "gpus": gpus, "serve_gb": SERVE_GB, "night_gpu_gb": NIGHT_GPU_GB, "night_peak_gib": NIGHT_PEAK_GIB}
    if gpus:
        name = max(gpus, key=lambda g: g["memory_gb"])["name"]
        out["serve"] = best >= SERVE_GB
        out["nights"] = best >= 24
        out["memory_line"] = f"{name}, {best:g} GB of video memory"
    elif apple and total:
        usable = total * (0.75 if total >= 36 else 0.67)  # macOS lets the GPU use about this much unified memory
        out["usable_gb"] = round(usable, 1)
        out["serve"] = usable >= SERVE_GB
        out["nights"] = False
        out["memory_line"] = f"{chip}, {total:.0f} GB unified memory (the GPU may use about {usable:.0f} GB)"
    else:
        out["serve"] = False
        out["nights"] = False
        out["memory_line"] = f"{chip}, no GPU found that can run the model"
    out["serve_line"] = ("This machine can serve Glimmer 30B (it needs about 18 GB)." if out["serve"] else
                         "This machine cannot serve Glimmer 30B: it needs about 18 GB of GPU memory. "
                         "Connect to a server on another machine.")
    out["nights_line"] = ("This machine can run the nightly training (night 9 peaked at 20.2 GiB on a 24 GB GPU)."
                          if out["nights"] else
                          "The nightly training needs a 24 GB NVIDIA GPU (night 9 peaked at 20.2 GiB). "
                          "It can run on another machine; this one serves and chats.")
    return out


# --- a server ---------------------------------------------------------------------------------------------------

def _get(url, timeout=4):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _try(url, timeout=4):
    try:
        return _get(url, timeout)
    except (OSError, ValueError, urllib.error.URLError):
        return None


def normalise_url(url):
    url = (url or "").strip().rstrip("/")
    if not url:
        raise ValueError("a server URL is needed, like http://127.0.0.1:1234")
    if not re.match(r"^https?://", url):
        url = "http://" + url
    return url.removesuffix("/v1")


def probe(url):
    """What a server at url is, and what it can do: {"ok", "url", "kind", "label", "models", "model", "glimmer",
    "chat", "nights", "adapters", "lines": [plain sentences]}. Read only."""
    url = normalise_url(url)
    out = {"url": url, "ok": False, "chat": False, "nights": False, "models": [], "model": None, "glimmer": False,
           "adapters": None, "lines": []}
    models = _try(url + "/v1/models")
    ids = [m.get("id") for m in (models or {}).get("data") or [] if m.get("id")]
    props = _try(url + "/props")
    lora = _try(url + "/lora-adapters")
    if isinstance(props, dict) and ("default_generation_settings" in props or "build_info" in props):
        kind, label = "llama.cpp", "llama.cpp server"
    elif _try(url + "/api/v0/models") is not None:
        kind, label = "lmstudio", "LM Studio"
    elif isinstance(_try(url + "/api/version"), dict):
        kind, label = "ollama", "Ollama"
    elif models is not None:
        kind, label = "openai", "OpenAI-compatible server"
    else:
        out["lines"].append(f"No server answered at {url}. Check the address and that the server is running.")
        return out
    out.update(kind=kind, label=label, models=ids, ok=bool(ids) or kind == "llama.cpp", chat=True)
    glim = [i for i in ids if "glimmer" in i.lower()]
    out["model"] = (glim or ids or [None])[0]
    out["glimmer"] = bool(glim) or (kind == "llama.cpp" and isinstance(props, dict)
                                    and "glimmer" in json.dumps(props.get("model_path", "")).lower())
    if kind == "llama.cpp" and isinstance(lora, list):
        out["nights"] = True
        out["adapters"] = len(lora)
    out["lines"].append(f"{label} at {url}" + (f", serving {out['model']}." if out["model"] else "."))
    out["lines"].append("Chat works." if out["glimmer"] else
                        "Chat works, but this server does not serve Muse Glimmer. Afterglow is measured on Glimmer "
                        "30B, and the nights train Glimmer adapters.")
    if out["nights"]:
        out["lines"].append("Nightly updates work: this server applies an adapter on each request"
                            + (f" ({out['adapters']} loaded now)." if out["adapters"] else "."))
    else:
        out["lines"].append(f"Nightly updates do not reach your agent here: {label} cannot apply the night's adapter "
                            "on each request, so your agent answers from the base model. Use llama.cpp's "
                            "llama-server for the nights.")
    return out


# --- a model file -----------------------------------------------------------------------------------------------

FILE_TYPES = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S",
              12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K",
              32: "BF16"}
_SCALAR = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}


def gguf_metadata(path, want=("general.", "context_length", "block_count")):
    """The GGUF header's key-value metadata (the keys matching `want`), without reading the tensors; arrays are
    skipped. Raises ValueError for a file that is not GGUF."""
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            raise ValueError("not a GGUF file")
        version, = struct.unpack("<I", f.read(4))
        if version < 2:
            raise ValueError(f"GGUF version {version} is too old")
        _tensors, n_kv = struct.unpack("<QQ", f.read(16))

        def string():
            n, = struct.unpack("<Q", f.read(8))
            return f.read(n).decode("utf-8", "replace")

        def value(t):
            if t in _SCALAR:
                fmt = _SCALAR[t]
                return struct.unpack(fmt, f.read(struct.calcsize(fmt)))[0]
            if t == 8:
                return string()
            if t == 9:
                et, n = struct.unpack("<IQ", f.read(12))
                if et in _SCALAR:
                    f.seek(struct.calcsize(_SCALAR[et]) * n, 1)
                else:
                    for _ in range(n):
                        value(et)
                return None
            raise ValueError(f"unknown GGUF value type {t}")

        meta = {"gguf_version": version}
        for _ in range(n_kv):
            key = string()
            t, = struct.unpack("<I", f.read(4))
            v = value(t)
            if v is not None and any(w in key for w in want):
                meta[key] = v
        return meta


def gguf_info(path):
    """A model file in plain terms: {"ok", "path", "name", "arch", "quant", "size_gb", "glimmer", "lines"}."""
    p = Path(os.path.expanduser(str(path or "").strip()))
    out = {"path": str(p), "ok": False, "lines": []}
    if not p.is_file():
        out["lines"].append("No file at that path.")
        return out
    try:
        meta = gguf_metadata(p)
    except (OSError, ValueError, struct.error) as e:
        out["lines"].append(f"Not a model file Afterglow can use: {e}.")
        return out
    arch = meta.get("general.architecture")
    ft = meta.get("general.file_type")
    quant = FILE_TYPES.get(ft) or (re.search(r"(Q\d_K_[SMLX]+|Q\d_\d|Q\d_K|F16|BF16|IQ\d\w*)", p.name) or [None])[0]
    size = p.stat().st_size / 1e9
    name = meta.get("general.name") or p.stem
    glimmer = "glimmer" in (name + p.name).lower()
    out.update(ok=True, name=name, arch=arch, quant=quant, size_gb=round(size, 1), glimmer=glimmer,
               context=next((v for k, v in meta.items() if k.endswith("context_length")), None))
    out["lines"].append(f"{name}" + (f", {quant}" if quant else "") + f", {size:.1f} GB.")
    if not glimmer:
        out["lines"].append("This is not Muse Glimmer. Afterglow is measured on Glimmer 30B, and the nights train "
                            "Glimmer adapters.")
    return out


def find_local_ggufs(limit=20):
    """Glimmer GGUF files already on this machine, in the folders model apps use (LM Studio, llama.cpp, the Hugging
    Face cache, the models folder). Shallow and quick; never follows into the whole disk."""
    home = Path.home()
    roots = [models_dir(), home / ".lmstudio" / "models", home / ".cache" / "lm-studio" / "models",
             home / "Library" / "Caches" / "llama.cpp", home / ".cache" / "llama.cpp",
             home / ".cache" / "huggingface" / "hub", home / "models", home / "Models"]
    seen, out = set(), []
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirs, files in os.walk(root):
            if dirpath.count(os.sep) - str(root).count(os.sep) > 4:
                dirs[:] = []
                continue
            for fn in files:
                if fn.lower().endswith(".gguf") and "glimmer" in fn.lower() and not fn.startswith(("mmproj", "dflash")):
                    fp = Path(dirpath) / fn
                    real = fp.resolve()
                    if real in seen or not real.is_file():
                        continue
                    seen.add(real)
                    out.append({"path": str(fp), "name": fn, "size_gb": round(real.stat().st_size / 1e9, 1)})
                    if len(out) >= limit:
                        return out
    return out


def llama_server_binary():
    for c in (os.environ.get("AFTERGLOW_LLAMA_SERVER"), shutil.which("llama-server"),
              "/opt/homebrew/bin/llama-server", "/usr/local/bin/llama-server"):
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return c
    return None


def local_mmproj_path():
    """The saved Glimmer vision file for the owned local server, or None. Reads the saved vision choice
    (runs/_settings/models.json) and checks the file exists. Called by local_server_command on every start,
    including the next app launch, so a saved choice survives restarts. Read only; never downloads or probes."""
    try:
        from . import models
        v = models.vision_choice()
        if v and v["id"] == "glimmer-mmproj":
            p = models.model_path(v)
            return str(p) if p else None
    except Exception:
        pass
    return None


def local_server_command(model_path, port=LOCAL_PORT, adapter=None, mmproj=None):
    """The llama-server command for a model file, with the flags the project serves with (all layers on the GPU,
    flash attention, one slot, a 16k context); with the night's adapter loaded at scale 0, set per request; and
    with the saved vision projector (--mmproj) when Glimmer's own vision is selected and on disk. Pass mmproj=False
    to skip the projector, or a path to use that file instead of the saved one."""
    cmd = [llama_server_binary() or "llama-server", "-m", str(model_path), "-ngl", "99", "-fa", "on", "-np", "1",
           "-c", "16384", "--jinja", "--no-webui", "--host", "127.0.0.1", "--port", str(port)]
    if adapter:
        cmd += ["--lora-scaled", f"{adapter}:0", "--lora-init-without-apply"]
    mp = local_mmproj_path() if mmproj is None else (str(mmproj) if mmproj else None)
    if mp:
        cmd += ["--mmproj", mp]
    return cmd


class LocalServer:
    """The llama-server this app started for a model file (one at a time), with its log under runs/_settings."""

    def __init__(self):
        self.proc = None
        self.cmd = None
        self.started_at = None

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, model_path, port=LOCAL_PORT, adapter=None, mmproj=None):
        if not llama_server_binary():
            raise RuntimeError("llama-server is not installed. Install llama.cpp (on a Mac: brew install llama.cpp), "
                               "then start again.")
        self.stop()
        log = settings_file().parent / "llama-server.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        self.cmd = local_server_command(model_path, port, adapter, mmproj)
        self.proc = subprocess.Popen(self.cmd, stdout=open(log, "ab"), stderr=subprocess.STDOUT,
                                     start_new_session=True)
        self.started_at = now_iso()
        return {"url": f"http://127.0.0.1:{port}", "command": self.cmd, "log": str(log), "pid": self.proc.pid}

    def stop(self):
        if self.running():
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def status(self):
        return {"running": self.running(), "command": self.cmd, "started_at": self.started_at,
                "pid": self.proc.pid if self.running() else None}


# --- Hugging Face ------------------------------------------------------------------------------------------------

_hf_cache = {"at": 0, "files": None}


def hf_files():
    """The official repo's offered files with size and sha256, from the Hub (cached 10 minutes)."""
    if _hf_cache["files"] is not None and time.time() - _hf_cache["at"] < 600:
        return _hf_cache["files"]
    tree = _get(f"https://huggingface.co/api/models/{HF_REPO}/tree/main", timeout=15)
    files = []
    for f in tree:
        if f.get("path") in HF_FILES:
            files.append({"file": f["path"], "size_gb": round(f.get("size", 0) / 1e9, 2), "size": f.get("size", 0),
                          "sha256": (f.get("lfs") or {}).get("oid"), "note": HF_FILES[f["path"]],
                          "url": f"https://huggingface.co/{HF_REPO}/resolve/main/{f['path']}"})
    files.sort(key=lambda x: list(HF_FILES).index(x["file"]))
    _hf_cache.update(at=time.time(), files=files)
    return files


class Download:
    """One file from the official repo into the models folder: resumable (.part + Range), then its sha256 is checked
    against the Hub's before the file takes its name. Progress is read by the app while it runs."""

    def __init__(self):
        self.state = {"state": "idle"}
        self._stop = threading.Event()
        self._thread = None

    def start(self, file, dest_dir=None, opener=urllib.request.urlopen):
        if self._thread and self._thread.is_alive():
            raise RuntimeError("a download is already running")
        entry = next((f for f in hf_files() if f["file"] == file), None)
        if not entry:
            raise ValueError(f"{file} is not one of the offered files")
        dest = Path(dest_dir or models_dir())
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / file
        if target.exists() and target.stat().st_size == entry["size"]:
            self.state = {"state": "done", "file": file, "path": str(target), "done": entry["size"],
                          "total": entry["size"], "verified": None, "line": "Already downloaded."}
            return self.state
        part = target.with_suffix(target.suffix + ".part")
        have = part.stat().st_size if part.exists() else 0
        free = shutil.disk_usage(dest).free
        if free < entry["size"] - have + 2e9:
            raise RuntimeError(f"Not enough free disk space in {dest}: {entry['size_gb']} GB is needed, "
                               f"{free / 1e9:.1f} GB is free.")
        self._stop.clear()
        self.state = {"state": "downloading", "file": file, "path": str(target), "done": have,
                      "total": entry["size"], "started_at": now_iso(), "rate_mb_s": None}
        self._thread = threading.Thread(target=self._run, args=(entry, part, target, have, opener), daemon=True)
        self._thread.start()
        return self.state

    def _run(self, entry, part, target, have, opener):
        try:
            req = urllib.request.Request(entry["url"], headers={"Range": f"bytes={have}-"} if have else {})
            t0, got = time.time(), 0
            with opener(req, timeout=60) as r, open(part, "ab" if have else "wb") as out:
                if have and getattr(r, "status", 206) != 206:  # the server ignored the range: start over
                    out.truncate(0)
                    have = 0
                    self.state["done"] = 0
                while not self._stop.is_set():
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    self.state["done"] = have + got
                    self.state["rate_mb_s"] = round(got / 1e6 / max(time.time() - t0, 1e-3), 1)
            if self._stop.is_set():
                self.state.update(state="paused", line="Paused. Start again to resume where it stopped.")
                return
            self.state.update(state="verifying", line="Checking the file against the published checksum.")
            h = hashlib.sha256()
            with open(part, "rb") as f:
                for block in iter(lambda: f.read(1 << 22), b""):
                    h.update(block)
            if entry.get("sha256") and h.hexdigest() != entry["sha256"]:
                part.unlink(missing_ok=True)
                self.state.update(state="failed", line="The file did not match the published checksum; it was "
                                                       "deleted. Download it again.")
                return
            part.rename(target)
            self.state.update(state="done", verified=bool(entry.get("sha256")), path=str(target),
                              line="Downloaded and checked against the published checksum.")
        except (OSError, urllib.error.URLError) as e:
            self.state.update(state="failed", line=f"The download stopped: {e}. Start again to resume.")

    def pause(self):
        self._stop.set()
        return self.state
