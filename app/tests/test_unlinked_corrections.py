"""Offline test for the morning report's unlinked corrections (night_checks.unlinked_corrections).

  python3 -m unittest discover -s app/tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow.night_checks import unlinked_corrections  # noqa: E402


def fact(fid, key, value, accept, valid_from=1, supersedes=None):
    return {"id": fid, "key": key, "value": value, "accept": accept, "valid_from": valid_from,
            **({"supersedes": supersedes} if supersedes else {})}


def item(nid, key, value):
    return {"id": nid, "key": key, "value": value, "full_value": value}


class UnlinkedCorrectionsTest(unittest.TestCase):
    def test_only_corrections_trained_both_ways(self):
        worlds = Path(tempfile.mkdtemp())
        os.environ["AFTERGLOW_WORLDS"] = str(worlds)
        self.addCleanup(os.environ.pop, "AFTERGLOW_WORLDS", None)
        (worlds / "wt").mkdir()
        (worlds / "wt" / "world.json").write_text(json.dumps({"facts": [
            fact("F01", "head mechanic name", "Priya Nair", ["priya nair"]),
            fact("F02", "overhaul deadline", "2026-11-15", ["2026-11-15"]),
            fact("F03", "cleaning crew", "Tidy Spokes Crew", ["tidy spokes crew"]),
            fact("F10", "head mechanic name", "Sofia Lind", ["sofia lind"], 2, "F01"),
            fact("F11", "overhaul deadline", "2026-11-22", ["2026-11-22"], 2, "F02"),
            fact("F12", "cleaning crew", "Sparkle Wheels Team", ["sparkle wheels team"], 2, "F03"),
            fact("F13", "rota meeting", "Tuesdays at 08:30", ["08:30"], 3, "F02")]}))
        consolidated = {
            "items": [item("N1", "repair lead", "Priya Nair leading repair benches"), item("N2", "head mechanic", "Sofia Lind"),
                      item("N3", "fall deadline", "wrapped by 2026-11-15"), item("N4", "overhaul deadline", "2026-11-22"),
                      item("N5", "cleaning crew", "Sparkle Wheels Team")],
            "outcomes": [{"id": "N2", "corrects": None}, {"id": "N4", "corrects": "wrapped by 2026-11-15"},
                         {"id": "N5", "corrects": None}]}
        # F10: both kept, no link -> listed; F11: linked -> not; F12: the old value is no longer held -> not;
        # F13: valid from day 3 -> not on day 2
        self.assertEqual(unlinked_corrections("wt", 2, consolidated),
                         [{"key": "head mechanic", "old": "Priya Nair", "new": "Sofia Lind"}])
        self.assertEqual(unlinked_corrections("wt", 2, None), [])


if __name__ == "__main__":
    unittest.main()
