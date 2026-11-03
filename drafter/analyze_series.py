#!/usr/bin/env python3
"""Summarise the nightly series (run_series.sh): for each world and night k, the teacher-forced acceptance on day
k+1 (the next morning's turns) of the stock drafter, last night's drafter and tonight's drafter.

usage: analyze_series.py RESULTS_DIR   (holds series-<w>/night<k>.json and series-<w>/stock-day<k+1>-stock.json)
"""
import glob
import json
import os
import re
import sys

root = sys.argv[1]
out = {}
for d in sorted(glob.glob(os.path.join(root, "series-*"))):
    s = os.path.basename(d).split("-", 1)[1]
    rows = []
    for f in sorted(glob.glob(os.path.join(d, "night*.json")), key=lambda p: int(re.findall(r"\d+", os.path.basename(p))[0])):
        k = int(re.findall(r"\d+", os.path.basename(f))[0])
        r = json.load(open(f))
        world = r["train_days"][0][0]
        stock_f = os.path.join(d, f"stock-day{k + 1}-stock.json")
        stock = json.load(open(stock_f))["stock"][world]["tau"] if os.path.exists(stock_f) else None
        prev = r["stock_val"][world]["tau"]      # the drafter this night started from, on day k+1
        new = r["evals"][-1]["val"][world]["tau"]
        rows.append({"night": k, "checked_on_day": k + 1, "stock": stock, "last_night": prev, "tonight": new,
                     "kept": new > prev, "train_minutes": r["train_minutes"], "anchors": r["anchors"]})
    out[s] = rows
json.dump(out, open(os.path.join(root, "series-summary.json"), "w"), indent=1)
for s, rows in out.items():
    print(f"\n{s}: night | checked on | stock | last night | tonight | kept | train min")
    for r in rows:
        print(f"  {r['night']:>2} | day {r['checked_on_day']:>2} | {r['stock']:.3f} | {r['last_night']:.3f} | "
              f"{r['tonight']:.3f} | {'yes' if r['kept'] else 'no'} | {r['train_minutes']:.1f}")
