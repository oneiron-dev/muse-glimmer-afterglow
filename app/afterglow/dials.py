"""The steering dials the chat sends, as per-request control vector scales: the user's voice and the dials accepted.

A night proposes dial changes (runs/<world>/<day>/night/steering-proposals.json); the user accepts or dismisses each
on the morning report (steering-decisions.json, web.py record_proposal). An accepted dial applies from the next day
on: day N's chat sends every dial accepted after nights 1..N-1, the latest accepted change per axis winning. A dismiss
changes nothing. The scale is the proposal's own; the served vector for the axis comes from the serving manifest of
the control-vector build (runs/serving/adapters-cv.json "control_vectors": {"id", "axis", "toward", "max_scale",
"negative_toward", "negative_scale"}), and a scale outside the dial's tested range is never sent. With no accepted
dial and no voice, no control_vectors field is sent, so the requests are byte-equal to a chat without dials.

A voice (steering/voices.json) is how replies sound: a preset is a named bundle of the served dials, a custom voice
is a control vector of its own (steering/voices/<slug>/voice.json, fitted from the user's one sentence and offered
on a morning report like a dial). The user picks one voice on the day page (runs/<world>/<day>/voice.json); it
applies from the next message and stays on later days until the user picks another. Picking a voice sets every
voice axis (the served dials and every custom voice): its own dials at their scales, the others off. A dial accepted
later on a morning report then changes its own axis on top of the voice.
"""
import datetime as dt
import importlib.util
import json

from . import paths

SERVED_MANIFEST = "adapters-cv.json"  # in runs/serving/: the manifest the control-vector build was started with
VOICES = paths.REPO / "steering" / "voices.json"
VOICE_DIR = paths.REPO / "steering" / "voices"


def read(path):
    return json.loads(path.read_text()) if path.exists() else None


def served_vectors():
    """The served control vectors by axis, from the control-vector build's manifest; {} when it has none."""
    m = read(paths.runs_root() / "serving" / SERVED_MANIFEST) or {}
    return {v["axis"]: v for v in m.get("control_vectors") or [] if v.get("axis")}


def proposal_scale(night, axis, toward):
    """The scale the night proposed for this change (proposals or counter candidates), or None."""
    doc = read(night / "steering-proposals.json") or {}
    for p in (doc.get("proposals") or []) + (doc.get("counter_candidates") or []):
        if p.get("axis") == axis and p.get("toward") == toward and p.get("scale") is not None:
            return float(p["scale"])
    return None


def check_range(v, axis, toward, scale):
    """Why this dial setting cannot be sent with the served vector v, or None when it is inside the tested range."""
    if v is None:
        return "the served build has no vector for this axis"
    if scale is None:
        return "no scale for this change"
    if toward == v.get("toward"):
        if not 0 < scale <= float(v.get("max_scale") or 0):
            return f"scale {scale} is outside the tested range (0, {v.get('max_scale')}]"
    elif toward == v.get("negative_toward") and v.get("negative_scale") is not None:
        if not float(v["negative_scale"]) <= scale < 0:
            return f"scale {scale} is outside the tested range [{v['negative_scale']}, 0)"
    else:
        return f"the served vector has no tested pole toward {toward!r}"
    return None


def voice_presets():
    return (read(VOICES) or {}).get("presets") or []


def custom_voices():
    """Every custom voice with a voice.json (ready or not), by slug."""
    out = {}
    for f in sorted(VOICE_DIR.glob("*/voice.json")) if VOICE_DIR.exists() else []:
        v = read(f)
        if v and v.get("slug"):
            out[v["slug"]] = v
    return out


def voice_dials(choice):
    """The dial settings [{"axis", "toward", "scale"}] a voice choice ({"preset": slug} or {"voice": slug}) sends."""
    if choice.get("preset"):
        preset = next((p for p in voice_presets() if p["slug"] == choice["preset"]), None)
        if preset is None:
            raise ValueError(f"no voice preset {choice['preset']!r}")
        if preset.get("status") == "not ready":
            raise ValueError(f"the preset {preset['slug']!r} is not offered: {(preset.get('check') or {}).get('why')}")
        return [dict(d) for d in preset["dials"]]
    v = custom_voices().get(choice.get("voice") or "")
    if v is None:
        raise ValueError(f"no custom voice {choice.get('voice')!r}")
    if v.get("status") != "ready":
        raise ValueError(f"the voice {v['slug']!r} is not ready: {v.get('why')}")
    return [{"axis": v["axis"]["name"], "toward": v["label"], "scale": float(v["strength"]["scale"])}]


def voice_choice(world, day):
    """The voice the user picked on day `day` or the latest day before it ({"preset"|"voice", "at", "day"}), or None."""
    root = paths.runs_root() / world
    for d in range(int(day), 0, -1):
        c = read(root / str(d) / "voice.json")
        if c:
            return {**c, "day": d}
    return None


def choose_voice(world, day, preset=None, voice=None):
    """Record the user's voice for day `day` (runs/<world>/<day>/voice.json); it applies from the next message."""
    if bool(preset) == bool(voice):
        raise ValueError("pick one preset or one custom voice")
    choice = {"preset": preset} if preset else {"voice": voice}
    voice_dials(choice)  # an unknown or unready voice is refused before anything is written
    choice["at"] = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec="seconds")
    d = paths.runs_root() / world / str(int(day))
    d.mkdir(parents=True, exist_ok=True)
    (d / "voice.json").write_text(json.dumps(choice, indent=1) + "\n")
    return choice


def accepted_dials(world, day):
    """{"dials": [{"id", "axis", "toward", "scale", "night"}] in id order, "skipped": [{"axis", "toward", "night",
    "why"}], "voice": the voice choice or None} for day `day` of `world`. Events apply in day order (a night's accept
    on the day after that night, a voice on its own day), by time within a day; "night" is None for a voice's dials."""
    root = paths.runs_root() / world
    vectors = served_vectors()
    events = []  # (day it applies from, time, kind, payload)
    for night_day in range(1, int(day)):
        night = root / str(night_day) / "night"
        for d in (read(night / "steering-decisions.json") or {}).values():
            if d.get("answer") == "accept":
                events.append((night_day + 1, d.get("at") or "", "accept",
                               {"axis": d.get("axis"), "toward": d.get("toward"),
                                "scale": proposal_scale(night, d.get("axis"), d.get("toward")), "night": night_day}))
    choice = voice_choice(world, day)
    if choice:
        events.append((choice["day"], choice.get("at") or "", "voice", choice))
    chosen, skipped = {}, []
    for _, _, kind, e in sorted(events, key=lambda x: (x[0], x[1])):
        if kind == "voice":
            voice_axes = {p["axis"] for preset in voice_presets() for p in preset["dials"]}
            voice_axes |= {v["axis"]["name"] for v in custom_voices().values() if v.get("axis")}
            for axis in voice_axes:
                chosen.pop(axis, None)
            try:
                settings = voice_dials(e)
            except ValueError as err:
                skipped.append({"axis": None, "toward": None, "night": None, "why": str(err)})
                continue
            for s in settings:
                why = check_range(vectors.get(s["axis"]), s["axis"], s["toward"], s["scale"])
                if why:
                    skipped.append({"axis": s["axis"], "toward": s["toward"], "night": None, "why": why})
                else:
                    chosen[s["axis"]] = {"id": int(vectors[s["axis"]]["id"]), **s, "night": None}
            continue
        axis, toward, scale = e["axis"], e["toward"], e["scale"]
        v = vectors.get(axis)
        why = check_range(v, axis, toward, scale)
        if why == "no scale for this change":
            why = "the night's proposals hold no scale for this change"
        if why:
            skipped.append({"axis": axis, "toward": toward, "night": e["night"], "why": why})
        else:
            chosen[axis] = {"id": int(v["id"]), "axis": axis, "toward": toward, "scale": scale, "night": e["night"]}
    return {"dials": sorted(chosen.values(), key=lambda x: x["id"]), "skipped": skipped, "voice": choice}


def voice_request_refusal(text):
    """Why a described voice is refused (health, diet, fitness or mood; or a voice that would guess), or None:
    steering/voice_pairs.py's own rule, so the day page and the night refuse the same sentences."""
    spec = importlib.util.spec_from_file_location("voice_pairs", paths.REPO / "steering" / "voice_pairs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.refusal(text)


def request_voice(world, day, text):
    """Queue a described voice for tonight's night (runs/<world>/<day>/voice-request.json), or refuse it."""
    text = " ".join((text or "").split())
    why = voice_request_refusal(text)
    rec = {"text": text, "status": "refused" if why else "queued", "why": why,
           "at": dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec="seconds")}
    if not why:
        d = paths.runs_root() / world / str(int(day))
        d.mkdir(parents=True, exist_ok=True)
        (d / "voice-request.json").write_text(json.dumps(rec, indent=1) + "\n")
    return rec


def voices_state(world, day, dials):
    """What the day page shows: presets, custom voices (summaries), the current choice and the dials sent now."""
    custom = []
    for v in custom_voices().values():
        custom.append({k: v.get(k) for k in ("slug", "label", "description", "status", "why")} |
                      {"scale": (v.get("strength") or {}).get("scale"), "max_scale": (v.get("strength") or {}).get("max_scale"),
                       "idk": {k: (v.get("idk") or {}).get(k) for k in ("pass", "rule")}})
    request = read(paths.runs_root() / world / str(int(day)) / "voice-request.json")
    return {"presets": voice_presets(), "custom": custom, "choice": voice_choice(world, day), "dials": dials,
            "request": request}
