"""Where the shared runs live. Every worktree reads and writes the main checkout's runs/ (as app/afterglow/paths.py
does), unless AFTERGLOW_RUNS points elsewhere."""

import os
import subprocess
from pathlib import Path


def runs_root(override=None):
    if override:
        return Path(override)
    if os.environ.get("AFTERGLOW_RUNS"):
        return Path(os.environ["AFTERGLOW_RUNS"])
    here = Path(__file__).resolve().parent
    common = subprocess.run(["git", "-C", str(here), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True).stdout.strip()
    main_checkout = Path(common).parent if common else here.parent
    return main_checkout / "runs"
