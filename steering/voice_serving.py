#!/usr/bin/env python3
"""The serving entries for ready custom voices: what the control-vector build's manifest (runs/serving/adapters-cv.json
"control_vectors") needs so the app can send a voice (app/afterglow/dials.py served_vectors).

Each ready voice gets {"id", "axis", "toward", "path", "sha256", "working_scale", "max_scale", "negative_scale": null,
"negative_toward": null, "source"}: ids follow the dials already served (concise 0, formal 1, decisive 2 -> voices
from 3), the path is where the GGUF sits on the serving host, and the sha256 is voice.json's (the file the sweep and
the "I don't know" check measured). Prints the entries as JSON; it edits no manifest.
"""

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", action="append", required=True, help="slug, in load order")
    ap.add_argument("--first-id", type=int, default=3)
    ap.add_argument("--dir", default="/data/afterglow/dials", help="the GGUF folder on the serving host")
    args = ap.parse_args()
    out = []
    for i, slug in enumerate(args.voice):
        v = json.loads((HERE / "voices" / slug / "voice.json").read_text())
        if v.get("status") != "ready":
            raise SystemExit(f"{slug}: not ready ({v.get('why')})")
        s = v["strength"]
        out.append({"id": args.first_id + i, "axis": v["axis"]["name"], "toward": v["label"],
                    "path": f"{args.dir}/voice-{slug}-v1.gguf", "sha256": v["vector"]["sha256"],
                    "working_scale": s["scale"], "max_scale": s["max_scale"], "negative_scale": None,
                    "negative_toward": None, "source": f"steering/voices/{slug}/voice.json"})
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
