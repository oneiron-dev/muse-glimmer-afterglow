"""The steering dials the user accepted, as per-request control vector scales for the chat.

A night proposes dial changes (runs/<world>/<day>/night/steering-proposals.json); the user accepts or dismisses each
on the morning report (steering-decisions.json, web.py record_proposal). An accepted dial applies from the next day
on: day N's chat sends every dial accepted after nights 1..N-1, the latest accepted change per axis winning. A dismiss
changes nothing. The scale is the proposal's own; the served vector for the axis comes from the serving manifest of
the control-vector build (runs/serving/adapters-cv.json "control_vectors": {"id", "axis", "toward", "max_scale",
"negative_toward", "negative_scale"}), and a scale outside the dial's tested range is never sent. With no accepted
dial, no control_vectors field is sent, so the requests are byte-equal to a chat without dials.
"""
import json

from . import paths

SERVED_MANIFEST = "adapters-cv.json"  # in runs/serving/: the manifest the control-vector build was started with


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


def accepted_dials(world, day):
    """{"dials": [{"id", "axis", "toward", "scale", "night"}] in id order, "skipped": [{"axis", "toward", "night",
    "why"}]} for day `day` of `world`."""
    root = paths.runs_root() / world
    vectors = served_vectors()
    chosen, skipped = {}, []
    for night_day in range(1, int(day)):
        night = root / str(night_day) / "night"
        for d in (read(night / "steering-decisions.json") or {}).values():
            if d.get("answer") != "accept":
                continue
            axis, toward = d.get("axis"), d.get("toward")
            v, scale = vectors.get(axis), proposal_scale(night, axis, toward)
            why = None
            if v is None:
                why = "the served build has no vector for this axis"
            elif scale is None:
                why = "the night's proposals hold no scale for this change"
            elif toward == v.get("toward"):
                if not 0 < scale <= float(v.get("max_scale") or 0):
                    why = f"scale {scale} is outside the tested range (0, {v.get('max_scale')}]"
            elif toward == v.get("negative_toward") and v.get("negative_scale") is not None:
                if not float(v["negative_scale"]) <= scale < 0:
                    why = f"scale {scale} is outside the tested range [{v['negative_scale']}, 0)"
            else:
                why = f"the served vector has no tested pole toward {toward!r}"
            if why:
                skipped.append({"axis": axis, "toward": toward, "night": night_day, "why": why})
            else:
                chosen[axis] = {"id": int(v["id"]), "axis": axis, "toward": toward, "scale": scale, "night": night_day}
    return {"dials": sorted(chosen.values(), key=lambda x: x["id"]), "skipped": skipped}
