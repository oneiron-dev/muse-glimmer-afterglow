"""Settings > Models: speech and vision slots beside the agent.

Saved choices live in runs/_settings/models.json (created only by an explicit
save: Use or Keep loaded). With nothing saved the helpers keep the app exactly
as before: speech_choice() None (dictation keeps its hard-coded default),
vision None means none, circle "text".

Vision is strictly opt-in: nothing saved, or an explicit None, means no vision,
even when a remote server could see. The GET payload reports the saved id per
slot, except speech which also shows the implicit dictation default when nothing
is saved (payload fields per slot: selected, saved, implicit, keep_loaded,
choices). Runtime code uses speech_choice()/vision_choice() (saved only): None
means "no override".

Importing this module spawns nothing, writes nothing, and hits no network.
GET /api/models is read only: it never writes files, spawns workers, installs
runtimes, or starts downloads. Only POST /api/models/download installs the Mac
vision runtime (via a lazy vlm_runtime import, and the request fails when the
runtime reports not ready) and starts weights.

Ownership, not loopback: a local agent means the app's own modelsetup.LocalServer
(the saved source is "gguf"), even if it is stopped. A server URL on 127.0.0.1
that the app did not start (LM Studio, Ollama) is remote: never restarted or
reconfigured. Only app.local_server may be restarted, and only through the
existing Agent flow (app.use_model); switching vision restarts it so its
--mmproj matches the saved choice, preserving its model.

Runtime notifications (all lazy; a missing lane module is skipped):
  vision Download -> vlm_runtime.ensure_runtime(), ready False fails the request
  vision Use/Remove/keep/download-done -> vlm_runtime.configure("vision")
  vision keep -> vlm_runtime.set_keep_loaded("vision", on)
  speech Use/Remove/download-done -> dictation.configure_speech("speech")
  speech keep -> dictation.set_speech_keep_loaded("speech", on)

For the runtime agent (vlm_runtime/vision/asr hooks):
  saved() -> the settings doc or None
  speech_choice() -> saved catalog dict or None (no override)
  vision_choice() -> saved catalog dict or None
  vision_mode(app=None) -> "none" | "glimmer" | "mac"
  keep_loaded(slot) -> bool
  model_path(choice) -> Path to existing usable weights, or None
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import model_catalog, model_download, paths

DOWNLOADS = model_download.DownloadManager()
VISION_PROBE_TIMEOUT = 2.0
_vision_probe_cache = {"at": 0.0, "url": None, "value": None}
WHISPER_LEGACY = Path("/Users/olety/.cache/hyperframes/whisper/models/ggml-large-v3.bin")
ASR_VENV_PYTHON = Path("/Users/olety/.cache/afterglow/asr-venv/bin/python")
LOCAL_SERVE_OVERHEAD_GB = 1.2  # context and buffers above the GGUF bytes


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def settings_file():
    return paths.runs_root() / "_settings" / "models.json"


def saved():
    f = settings_file()
    try:
        doc = json.loads(f.read_text()) if f.exists() else None
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def save(doc):
    f = settings_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({**doc, "set_at": now_iso()}, indent=1) + "\n")
    return saved()


def get_choice(choice_id):
    return model_catalog.by_id(choice_id)


def _selected_id(slot):
    """The saved id for a slot, or None."""
    doc = saved() or {}
    v = doc.get(slot)
    c = get_choice(v) if isinstance(v, str) else None
    return c["id"] if c and c["slot"] == slot else None


def speech_choice():
    """The saved speech catalog dict, or None (dictation keeps its default)."""
    sid = _selected_id("speech")
    return get_choice(sid) if sid else None


def vision_choice():
    """The saved vision catalog dict, or None (no vision)."""
    vid = _selected_id("vision")
    return get_choice(vid) if vid else None


def keep_loaded(slot):
    """Whether the slot's model stays resident (runtime agent reads this).
    Speech defaults warm like today when the flag is missing; vision defaults off."""
    if slot not in ("speech", "vision"):
        raise ValueError("slot must be speech or vision")
    kl = (saved() or {}).get("keep_loaded", {})
    if isinstance(kl, dict) and slot not in kl:
        return slot == "speech"
    return bool(kl.get(slot))


# --- where weights live ------------------------------------------------------------------------------------------

def hf_hub_root():
    return Path(os.environ.get("HF_HUB_CACHE") or Path.home() / ".cache" / "huggingface" / "hub")


def _hub_snapshot_complete(snap):
    """A usable multi-file HF snapshot: config plus weights, no broken links."""
    try:
        if not (snap / "config.json").is_file():
            return False
        weights = [p for p in snap.iterdir()
                   if p.suffix in (".safetensors", ".bin") and p.is_file() and p.stat().st_size > 0]
        if not weights:
            return False
        for p in snap.iterdir():
            if p.is_symlink() and not p.exists():
                return False
        return True
    except OSError:
        return False


def hf_snapshot(repo, revision=None):
    """An existing complete HF hub snapshot dir for a repo, or None. Prefers the
    pinned revision, else the fullest complete snapshot. No network, no writes."""
    safe = "models--" + repo.replace("/", "--")
    snaps = hf_hub_root() / safe / "snapshots"
    if not snaps.is_dir():
        return None
    if revision:
        hit = snaps / revision
        if hit.is_dir() and _hub_snapshot_complete(hit):
            return hit
        return None
    try:
        revs = [p for p in snaps.iterdir() if p.is_dir()]
    except OSError:
        return None
    good = [p for p in revs if _hub_snapshot_complete(p)]
    if not good:
        return None
    def score(p):
        try:
            return sum(1 for x in p.iterdir() if x.is_file())
        except OSError:
            return 0
    good.sort(key=score)
    return good[-1]


def _hf_single_file(repo, filename, size):
    """A top-level file of exact size in any snapshot of an HF repo, or None."""
    safe = "models--" + repo.replace("/", "--")
    snaps = hf_hub_root() / safe / "snapshots"
    try:
        if not snaps.is_dir():
            return None
        for rev in snaps.iterdir():
            cand = rev / filename
            try:
                if cand.is_file() and cand.stat().st_size == size:
                    return cand
            except OSError:
                pass
    except OSError:
        pass
    return None


def model_path(choice):
    """Existing usable weights for a choice, or None. No network, no writes.

    Lane-owned paths under models_dir() count only with a verified manifest
    (model_download completion); nothing else in our layout is trusted.
    Externally managed copies also count: complete HF hub snapshots for repos,
    the exact-size projector beside the text model in the HF cache, and the
    whisper.cpp fallback another app keeps. Single-file choices return the file;
    repos return their dir.
    """
    if isinstance(choice, str):
        choice = get_choice(choice)
    if not choice:
        return None
    from . import modelsetup
    try:
        root = modelsetup.models_dir()
    except Exception:
        root = None
    if root is not None and DOWNLOADS.is_downloaded(choice):
        if choice.get("single_file"):
            return root / choice["files"][0].rsplit("/", 1)[-1]
        return root / choice["id"]
    if choice.get("single_file"):
        if choice["id"] == "glimmer-mmproj":
            hit = _hf_single_file(choice["repo"], model_catalog.MMPROJ_FILE, model_catalog.MMPROJ_SIZE)
            if hit is not None:
                return hit
        if choice["id"] == "whisper-large-v3":
            try:
                if WHISPER_LEGACY.is_file() and WHISPER_LEGACY.stat().st_size > 0:
                    return WHISPER_LEGACY
            except OSError:
                pass
        return None
    snap = hf_snapshot(choice["repo"], choice.get("revision"))
    return snap


def is_downloaded(choice):
    if isinstance(choice, str):
        choice = get_choice(choice)
    return model_path(choice) is not None if choice else False


# --- the agent's server ------------------------------------------------------------------------------------------

def agent_local(app):
    """True when the agent runs on the app's own LocalServer (saved source gguf).
    Loopback URLs the app did not start are remote."""
    try:
        return (getattr(app, "model_source", None) or {}).get("source") == "gguf"
    except Exception:
        return False


def _cmd_arg(cmd, flag):
    try:
        i = list(cmd).index(flag)
        return cmd[i + 1]
    except (ValueError, IndexError, TypeError):
        return None


def _local_cmd(app):
    try:
        return list(getattr(getattr(app, "local_server", None), "cmd", None) or [])
    except Exception:
        return []


def _local_running(app):
    try:
        return bool(getattr(app, "local_server", None).running())
    except Exception:
        return False


def local_agent_gb(app):
    """What the owned agent costs here: its actual file bytes plus context, or
    SERVE_GB when the file is unknown. Variants differ (the XL file serves ~21)."""
    from . import modelsetup
    try:
        p = (getattr(app, "model_source", None) or {}).get("path")
        if p and Path(p).is_file():
            return round(Path(p).stat().st_size / 1e9 + LOCAL_SERVE_OVERHEAD_GB, 1)
    except Exception:
        pass
    return float(modelsetup.SERVE_GB)


def _props_vision(url, timeout=VISION_PROBE_TIMEOUT):
    """A llama-server's /props modalities.vision, or None when unknown. Read only."""
    try:
        req = urllib.request.Request(url.rstrip("/") + "/props", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            doc = json.loads(r.read())
        mod = (doc.get("modalities") or {}) if isinstance(doc, dict) else {}
        v = mod.get("vision")
        return True if v is True else False if v is False else None
    except Exception:
        return None


def agent_endpoint(app):
    try:
        return getattr(getattr(app, "client", None), "endpoint", None)
    except Exception:
        return None


def agent_vision(app):
    """{"local", "available", "line", "hint"}. Read only; the remote probe is a
    short cached GET of /props (no writes, no workers). Local availability is
    claimed only when the running owned server's command loads the ready file."""
    local = agent_local(app)
    if local:
        ready = model_path("glimmer-mmproj")
        if ready is None:
            return {"local": True, "available": False, "hint": "Press Download on Glimmer's own vision.",
                    "line": "Download the add-on to let your agent see pictures."}
        running, cmd = _local_running(app), _local_cmd(app)
        confirmed = running and _cmd_arg(cmd, "--mmproj") == str(ready)
        starting = bool((getattr(app, "model_source", None) or {}).get("starting"))
        if confirmed:
            line = ("Your agent's server is starting with its vision file." if starting
                    else "Your agent loads its vision projector.")
            return {"local": True, "available": True, "hint": None, "line": line}
        if not running:
            if starting:
                return {"local": True, "available": False, "hint": None,
                        "line": "Your agent's server is starting."}
            return {"local": True, "available": False, "hint": "Start it from the Agent slot.",
                    "line": "Your agent is stopped. Start it under Agent, then come back here."}
        if _selected_id("vision") == "glimmer-mmproj":
            return {"local": True, "available": False, "hint": "Press Use on Glimmer's own vision.",
                    "line": "Press Use to restart your agent with the add-on."}
        return {"local": True, "available": False, "hint": "Press Use on Glimmer's own vision.",
                "line": "The add-on is downloaded. Press Use to turn it on."}
    url = agent_endpoint(app)
    if not url:
        return {"local": False, "available": None, "hint": None,
                "line": "Set up your agent first, under Agent."}
    now = time.time()
    if _vision_probe_cache["url"] == url and now - _vision_probe_cache["at"] < 30:
        avail = _vision_probe_cache["value"]
    else:
        avail = _props_vision(url)
        _vision_probe_cache.update(at=now, url=url, value=avail)
    if avail is True:
        return {"local": False, "available": True, "hint": None,
                "line": "Turned on where your agent runs."}
    if avail is False:
        return {"local": False, "available": False, "hint": "--mmproj <file>",
                "line": "Not turned on yet on the computer that runs your agent."}
    return {"local": False, "available": None, "hint": None,
            "line": "Could not reach your agent to check."}


# --- effective selection -----------------------------------------------------------------------------------------

def _asr_venv_ok():
    try:
        from . import asr_runtime
        return asr_runtime.ASR_PYTHON.is_file()
    except Exception:
        try:
            return ASR_VENV_PYTHON.is_file()
        except Exception:
            return False


def _implicit_speech_id():
    """The dictation default when nothing is saved: Qwen3-ASR while runnable
    (venv plus weights), else the whisper fallback while on disk, else the
    Qwen default as the next download."""
    q = get_choice("qwen3-asr-1.7b-8bit")
    w = get_choice("whisper-large-v3")
    if _asr_venv_ok() and is_downloaded(q):
        return q["id"]
    if is_downloaded(w):
        return w["id"]
    return q["id"]


def effective_selected(slot, app=None):
    """The active id: saved, else the implicit speech default. Vision has no
    implicit choice: unsaved or explicit None means none, never auto-on."""
    sid = _selected_id(slot)
    if sid:
        return sid
    if slot == "speech":
        return _implicit_speech_id()
    return None


def _effective_choice(slot, app):
    eid = effective_selected(slot, app)
    return get_choice(eid) if eid else None


def vision_mode(app=None):
    """How the circle sees: "mac" (warm on-device model), "glimmer" (the agent
    itself reads the image), or "none". Saved only, never auto-on. With an app,
    Glimmer's file counts only when the server confirms it (local command, or
    remote /props); without an app the saved opt-in alone decides."""
    v = vision_choice()
    if v and v["runtime"] == "mlx-vlm" and is_downloaded(v):
        return "mac"
    if v and v["id"] == "glimmer-mmproj":
        if app is None:
            return "glimmer"
        return "glimmer" if agent_vision(app)["available"] is True else "none"
    return "none"


def circle(app=None):
    return "vision" if vision_mode(app) != "none" else "text"


# --- fit ----------------------------------------------------------------------------------------------------------------

def total_gpu_gb(hw):
    """Usable GPU GB: Apple unified usable, else best NVIDIA VRAM, else system RAM."""
    try:
        if hw.get("apple_silicon") and hw.get("usable_gb"):
            return float(hw["usable_gb"])
        gpus = hw.get("gpus") or []
        if gpus:
            return float(max(g.get("memory_gb", 0) for g in gpus))
        if hw.get("memory_gb"):
            return float(hw["memory_gb"])
    except (TypeError, ValueError):
        pass
    return None


def needs_met(choice, hw):
    need = choice.get("needs", "any")
    if need == "apple-silicon":
        return bool(hw.get("apple_silicon"))
    if need == "cuda":
        return bool(hw.get("gpus"))
    return True


def local_memory_gb(choice, app=None):
    """What a choice costs on this computer when in use (0 when remote)."""
    if choice["id"] == "glimmer-mmproj":
        if app is not None and not agent_local(app):
            return 0.0  # runs on the remote server
        return float(choice["memory_gb"])
    return float(choice["memory_gb"])


def used_local_gb(app):
    """Local GB in use: the owned agent plus effective speech and saved vision."""
    used = 0.0
    if agent_local(app):
        used += local_agent_gb(app)
        v = vision_choice()
        if v and v["id"] == "glimmer-mmproj" and is_downloaded(v):
            used += float(v["memory_gb"])  # inside the agent's server, not a second model
    s = _effective_choice("speech", app)
    if s and is_downloaded(s):
        used += float(s["memory_gb"])
    v = vision_choice()
    if v and v["runtime"] == "mlx-vlm" and is_downloaded(v):
        used += float(v["memory_gb"])
    return round(used, 1)


def fit_for(choice, app, hw, used_excluding_slot=None):
    """{"fit": "fits"|"tight"|"too_big", "projected_gb"} for one choice.

    Counts only models that run on this computer. Thresholds: fits with 2 GB to
    spare, tight up to the limit, too big beyond it or when needs are unmet.
    """
    if used_excluding_slot is None:
        used_excluding_slot = 0.0
    if not needs_met(choice, hw):
        return {"fit": "too_big", "projected_gb": round(used_excluding_slot + local_memory_gb(choice, app), 1)}
    total = total_gpu_gb(hw)
    if total is None:
        return {"fit": "fits", "projected_gb": round(used_excluding_slot + local_memory_gb(choice, app), 1)}
    proj = used_excluding_slot + local_memory_gb(choice, app)
    if proj <= total - 2.0:
        fit = "fits"
    elif proj <= total:
        fit = "tight"
    else:
        fit = "too_big"
    return {"fit": fit, "projected_gb": round(proj, 1)}


def _slot_used_excluding(app, slot):
    """Local GB in use without the slot's selection (for fit maths)."""
    used = local_agent_gb(app) if agent_local(app) else 0.0
    s = _effective_choice("speech", app)
    v = vision_choice()
    if slot != "speech" and s and is_downloaded(s):
        used += float(s["memory_gb"])
    if slot != "vision":
        if v and v["runtime"] == "mlx-vlm" and is_downloaded(v):
            used += float(v["memory_gb"])
        if v and v["id"] == "glimmer-mmproj" and agent_local(app) and is_downloaded(v):
            used += float(v["memory_gb"])
    return used


def recommended_id(slot, app, hw):
    """Best choice that fits here (catalog order), or None. Never a choice this
    machine cannot run, and never Glimmer's file for a remote server without vision."""
    base = _slot_used_excluding(app, slot)
    fellback = None
    for c in model_catalog.for_slot(slot):
        if c["id"] == "glimmer-mmproj" and not agent_local(app):
            if agent_vision(app)["available"] is not True:
                continue  # nothing to download for a remote server
            return c["id"]  # remote already sees: one press to opt in
        if not needs_met(c, hw):
            continue
        fit = fit_for(c, app, hw, base)["fit"]
        if fit == "fits":
            return c["id"]
        if fit == "tight" and fellback is None:
            fellback = c["id"]
    return fellback


def memory_bar(app, hw):
    """{"total_gb", "used_gb", "free_gb", "segments": [{id, label, gb}]}."""
    total = total_gpu_gb(hw)
    segs = []
    if agent_local(app):
        gb = local_agent_gb(app)
        label = "Your agent"
        v = vision_choice()
        if v and v["id"] == "glimmer-mmproj" and is_downloaded(v):
            gb += float(v["memory_gb"])
            label = "Your agent, with vision"
        segs.append({"id": "agent", "label": label, "gb": round(gb, 1)})
    s = _effective_choice("speech", app)
    if s and is_downloaded(s):
        segs.append({"id": f"speech:{s['id']}", "label": "Speech", "gb": s["memory_gb"]})
    v = vision_choice()
    if v and v["runtime"] == "mlx-vlm" and is_downloaded(v):
        segs.append({"id": f"vision:{v['id']}", "label": "Vision", "gb": v["memory_gb"]})
    used = round(sum(sg["gb"] for sg in segs), 1)
    if total is None:
        return {"total_gb": None, "used_gb": used, "free_gb": None, "segments": segs}
    free = round(max(total - used, 0.0), 1)
    return {"total_gb": round(total, 1), "used_gb": used, "free_gb": free,
            "segments": [*segs, {"id": "free", "label": "Free", "gb": free}]}


# --- per-choice state ----------------------------------------------------------------------------------------------------

def choice_state(choice, app):
    """State plus progress for GET: not_downloaded | downloading | paused |
    downloaded | in_use | unavailable | failed. Read only."""
    cid = choice["id"]
    if cid == "glimmer-mmproj" and not agent_local(app):
        avail = agent_vision(app)["available"]
        if avail is True:
            if _selected_id("vision") == cid:
                return {"state": "in_use", "progress": None,
                        "line": "Turned on where your agent runs."}
            return {"state": "downloaded", "progress": None,
                    "line": "Your agent can see pictures. Press Use to turn it on."}
        if avail is False:
            return {"state": "unavailable", "progress": None,
                    "line": "Not turned on yet on the computer that runs your agent."}
        return {"state": "unavailable", "progress": None,
                "line": "Could not reach your agent to check."}
    dl = DOWNLOADS.state(cid)
    if dl.get("state") in ("downloading", "paused", "failed"):
        total, done = dl.get("total") or 0, dl.get("done") or 0
        prog = {"done": done, "total": total, "pct": round(done / total * 100) if total else 0,
                "file": dl.get("file"), "files_done": dl.get("files_done"),
                "files_total": dl.get("files_total"), "line": dl.get("line")}
        key = {"downloading": "downloading", "paused": "paused", "failed": "failed"}[dl["state"]]
        return {"state": key, "progress": prog, "line": dl.get("line")}
    if dl.get("state") == "done" or is_downloaded(choice):
        selected = effective_selected(choice["slot"], app) == cid
        return {"state": "in_use" if selected else "downloaded", "progress": None, "line": None}
    return {"state": "not_downloaded", "progress": None, "line": None}


def _hardware(app):
    try:
        if hasattr(app, "hardware"):
            return app.hardware()
    except Exception:
        pass
    from . import modelsetup
    return modelsetup.hardware()


def payload(app):
    """GET /api/models: hardware, slots, memory, circle, agent vision. Read only."""
    hw = _hardware(app)
    doc = saved() or {}
    slots = {}
    for slot in ("speech", "vision"):
        base = _slot_used_excluding(app, slot)
        rec = recommended_id(slot, app, hw)
        eff, sav = effective_selected(slot, app), _selected_id(slot)
        choices = []
        for c in model_catalog.for_slot(slot):
            st = choice_state(c, app)
            # fit is pure memory maths (a remote Glimmer file costs 0 and fits);
            # the state carries availability. local_gb is what this computer pays.
            fit = fit_for(c, app, hw, base)["fit"]
            choices.append({**c, "state": st["state"], "fit": fit,
                            "recommended": c["id"] == rec, "progress": st["progress"],
                            "line": st["line"], "local_gb": round(local_memory_gb(c, app), 1)})
        slots[slot] = {"selected": eff, "saved": sav, "implicit": eff is not None and sav is None,
                       "keep_loaded": keep_loaded(slot),
                       "choices": choices}
    return {"hardware": hw, "slots": slots, "memory": memory_bar(app, hw),
            "circle": circle(app), "agent_vision": agent_vision(app),
            "vision_mode": vision_mode(app)}


# --- runtime notifications (lazy; a missing lane module is skipped) ------------------------------------------------------

def _notify_configured(slot):
    try:
        if slot == "vision":
            from . import vlm_runtime
            fn = getattr(vlm_runtime, "configure", None)
            if callable(fn):
                fn("vision")
        elif slot == "speech":
            from . import dictation
            fn = getattr(dictation, "configure_speech", None)
            if callable(fn):
                fn("speech")
    except (ImportError, ModuleNotFoundError):
        pass


def _notify_keep_loaded(slot, on):
    try:
        if slot == "vision":
            from . import vlm_runtime
            fn = getattr(vlm_runtime, "set_keep_loaded", None)
            if callable(fn):
                fn("vision", bool(on))
            fn = getattr(vlm_runtime, "configure", None)
            if callable(fn):
                fn("vision")
        elif slot == "speech":
            from . import dictation
            fn = getattr(dictation, "set_speech_keep_loaded", None)
            if callable(fn):
                fn("speech", bool(on))
    except (ImportError, ModuleNotFoundError):
        pass


def _download_done(choice):
    """A finished download follows into the runtime. Runs in the worker thread;
    never raises."""
    try:
        _notify_configured(choice.get("slot"))
    except Exception:
        pass


def _restart_local_for_vision(app):
    """Restart the owned local server so its --mmproj matches the saved vision.
    Only the app's own LocalServer, through the existing Agent flow (which
    preserves the model and re-applies the saved projector on start). Returns a
    response fragment (restarted / restart_needed) or None when nothing was needed."""
    try:
        ls = getattr(app, "local_server", None)
        if ls is None or not agent_local(app):
            return None
        want = _selected_id("vision") == "glimmer-mmproj" and model_path("glimmer-mmproj") is not None
        running, cmd = _local_running(app), _local_cmd(app)
        if (want and _cmd_arg(cmd, "--mmproj")) or (not want and not _cmd_arg(cmd, "--mmproj")):
            return None  # already matches (a stopped server honors it on next start)
        if not running:
            return None  # next Agent start loads it
        ms = getattr(app, "model_source", None) or {}
        path = ms.get("path") or _cmd_arg(cmd, "-m")
        if not path:
            return {"restart_needed": True,
                    "restart_line": "Restart your agent's server from the Agent slot to load vision."}
        from . import modelsetup
        port = _cmd_arg(cmd, "--port")
        lora = _cmd_arg(cmd, "--lora-scaled")
        nondefault = (port and port != str(modelsetup.LOCAL_PORT)) or lora
        if nondefault:
            # The owned server runs custom flags: restart it directly with the
            # same port and adapter (the Agent flow only starts defaults).
            info = modelsetup.gguf_info(path)
            if not info["ok"]:
                raise ValueError(" ".join(info["lines"]))
            adapter = lora.rsplit(":", 1)[0] if lora and ":" in lora else (lora or None)
            started = ls.start(info["path"], int(port) if port and port.isdigit() else modelsetup.LOCAL_PORT,
                               adapter)
            try:
                app.model_source = {"source": "gguf", "path": info["path"], "url": started["url"],
                                    "kind": "llama.cpp", "from": "settings", "starting": True}
            except Exception:
                pass
            switch = getattr(app, "_switch_when_healthy", None)
            if callable(switch):
                threading.Thread(target=switch, args=(started["url"],), daemon=True).start()
            return {"restarted": True}
        use_model = getattr(app, "use_model", None)
        if not callable(use_model):
            return {"restart_needed": True,
                    "restart_line": "Restart your agent's server from the Agent slot to load vision."}
        use_model({"source": "gguf", "path": path}, persist=False)
        return {"restarted": True}
    except Exception as e:
        return {"restart_needed": True,
                "restart_line": f"The server did not restart: {e}. Restart it from the Agent slot."}


# --- routes ----------------------------------------------------------------------------------------------------------

def models_get(app, req):
    return payload(app)


def _need_id(req):
    cid = (req.body or {}).get("id")
    c = get_choice(cid) if isinstance(cid, str) else None
    if not c:
        raise ValueError("unknown model id")
    return c


def download_post(app, req):
    c = _need_id(req)
    if c["id"] == "glimmer-mmproj" and not agent_local(app):
        raise ValueError("Your agent runs on another machine; its owner installs the vision file there.")
    if c["runtime"] == "mlx-vlm":
        try:
            from . import vlm_runtime
        except (ImportError, ModuleNotFoundError):
            vlm_runtime = None
        if vlm_runtime is not None:
            ensure = getattr(vlm_runtime, "ensure_runtime", None)
            if not callable(ensure):
                raise RuntimeError("The vision runtime installer is missing.")
            res = ensure()
            ready = res.get("ready", res) if isinstance(res, dict) else bool(res)
            if not ready:
                line = res.get("line") if isinstance(res, dict) and res.get("line") else                     "The vision runtime is not ready."
                raise RuntimeError(line)
    DOWNLOADS.start(c, on_done=_download_done)
    return payload(app)


def pause_post(app, req):
    DOWNLOADS.pause(_need_id(req)["id"])
    return payload(app)


def remove_post(app, req):
    c = _need_id(req)
    if _selected_id(c["slot"]) == c["id"]:
        doc = saved() or {}
        doc[c["slot"]] = None
        save(doc)
    DOWNLOADS.remove(c)
    _notify_configured(c["slot"])
    out = payload(app)
    if is_downloaded(c):
        out["note"] = "Kept the copy another app downloaded."
    return out


def use_post(app, req):
    body = req.body or {}
    slot = body.get("slot")
    if slot not in ("speech", "vision"):
        raise ValueError("slot must be speech or vision")
    cid = body.get("id")
    if cid is None:
        doc = saved() or {}
        doc[slot] = None
        save(doc)
        _notify_configured(slot)
        out = payload(app)
        if slot == "vision":
            frag = _restart_local_for_vision(app)
            if frag:
                out.update(frag)
        return out
    c = get_choice(cid) if isinstance(cid, str) else None
    if not c or c["slot"] != slot:
        raise ValueError("unknown model id for that slot")
    if c["id"] == "glimmer-mmproj" and not agent_local(app):
        if agent_vision(app)["available"] is True:
            doc = saved() or {}
            doc["vision"] = c["id"]
            save(doc)
            _notify_configured(slot)
            return payload(app)
        raise ValueError("Your agent's server does not load its vision projector.")
    if not is_downloaded(c):
        raise ValueError("Download it first, then press Use.")
    doc = saved() or {}
    doc[slot] = c["id"]
    save(doc)
    _notify_configured(slot)
    out = payload(app)
    if slot == "vision":
        frag = _restart_local_for_vision(app)
        if frag:
            out.update(frag)
    return out


def keep_loaded_post(app, req):
    body = req.body or {}
    slot = body.get("slot")
    if slot not in ("speech", "vision"):
        raise ValueError("slot must be speech or vision")
    doc = saved() or {}
    kl = dict(doc.get("keep_loaded") or {})
    kl[slot] = bool(body.get("on"))
    doc["keep_loaded"] = kl
    save(doc)
    _notify_keep_loaded(slot, bool(body.get("on")))
    return payload(app)


ROUTES = {("GET", "/api/models"): models_get,
          ("POST", "/api/models/download"): download_post,
          ("POST", "/api/models/pause"): pause_post,
          ("POST", "/api/models/remove"): remove_post,
          ("POST", "/api/models/use"): use_post,
          ("POST", "/api/models/keep-loaded"): keep_loaded_post}
