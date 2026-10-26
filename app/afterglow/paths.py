"""Where worlds and runs live.

runs/ is shared by every git worktree of the entry repo: it resolves to the main checkout's
runs/ (gitignored), so the day app and the night job read and write the same files.
Override with AFTERGLOW_RUNS / AFTERGLOW_WORLDS.
"""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main_checkout():
    try:
        common = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        return Path(common).parent
    except (OSError, subprocess.SubprocessError):
        return REPO


def runs_root():
    return Path(os.environ.get("AFTERGLOW_RUNS") or main_checkout() / "runs")


def worlds_root():
    return Path(os.environ.get("AFTERGLOW_WORLDS") or REPO / "worlds")


def day_dir(world, day):
    d = runs_root() / world / str(day)
    d.mkdir(parents=True, exist_ok=True)
    return d
