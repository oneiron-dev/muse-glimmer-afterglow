"""Offline regression tests for the serving manifests (runs/serving/*.json, local files, not versioned).

A promoted night must lose its "arm" (serving_choice skips every entry with an arm;
PR #161 promoted w4 9/night-9e5 to 9/night but left the arm, so w4 served a stale night).
Runs against the checkout's runs/; skips when no serving manifest is present.

  python3 -m unittest app.tests.test_serving_manifest
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow.night_checks import serving_choice  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
RUNS = Path(os.environ.get("AFTERGLOW_RUNS") or REPO / "runs")
WORLDS = ["w1-ines-varga", "w2-solo-developer", "w3-shop-owner", "w4-grad-student"]


def load_manifest(name):
    f = RUNS / "serving" / name
    if not f.exists():
        raise unittest.SkipTest(f"no local {name}; serving manifests are not versioned")
    return json.loads(f.read_text())["adapters"]


class ServingManifestTest(unittest.TestCase):
    def test_night_of_record_has_no_arm(self):
        """A keep entry whose night_dir is the day's night of record must not carry an arm."""
        for name in ("adapters.json", "adapters-cv.json"):
            try:
                entries = load_manifest(name)
            except unittest.SkipTest:
                continue
            for e in entries:
                if e.get("decision", "keep") != "keep" or e.get("world") is None:
                    continue
                if e.get("night_dir", f"{e['day']}/night") == f"{e['day']}/night":
                    self.assertNotIn("arm", e, f"{name} id {e.get('id')}: night of record with arm")

    def test_latest_non_arm_keep_is_served(self):
        """serving_choice picks each world's latest non-arm keep (a stale arm must not hide it)."""
        entries = load_manifest("adapters.json")
        for world in WORLDS:
            keeps = [e for e in entries
                     if e.get("world") == world and e.get("decision", "keep") == "keep"
                     and not e.get("arm")]
            self.assertTrue(keeps, f"{world}: no servable keep entry")
            want_day = max(e["day"] for e in keeps)
            choice = serving_choice(world, entries)
            self.assertIsNotNone(choice, f"{world}: serving_choice returned None")
            got = choice["entry"]
            self.assertNotIn("arm", got, f"{world}: served an arm entry")
            self.assertEqual(got["day"], want_day,
                             f"{world}: serves day {got['day']}, latest servable keep is day {want_day}")


if __name__ == "__main__":
    unittest.main()
