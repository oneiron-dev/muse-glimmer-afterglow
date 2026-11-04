"""With default settings the served agent is byte-equal to main before the app work: the same tool list and the same
system prompt, even with every feature module loaded. A feature that changes either when switched on says so in its PR
and keeps this test green while it is off. The fingerprints were taken from main at a4b0d32 (4 Nov 2026)."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import agent, routes, tools  # noqa: E402

TOOLS_SHA256 = "357dd5627823ecd0dcd3e4ad115380844aa587784e34519abb392cd1ca9e3564"
PROMPT_TEMPLATE_SHA256 = "375f6f2eeb6cf0292d84a5f904507ceb49c8c9196e6c3457424dfdc783842d27"
INES_SYSTEM_SHA256 = "6c5dcc55b516d4ed5729db3d8c9bda4e1dc2ebc59bc350955d5160e1566ea203"


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


class PromptFingerprintTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AFTERGLOW_RUNS"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("AFTERGLOW_RUNS", None)
        self.tmp.cleanup()

    def check(self):
        self.assertEqual(sha(json.dumps(tools.TOOL_SCHEMAS, sort_keys=True, ensure_ascii=False)), TOOLS_SHA256)
        self.assertEqual(sha(agent.SYSTEM_PROMPT), PROMPT_TEMPLATE_SHA256)
        msg = agent.system_message(agent.load_world("w1-ines-varga"))
        self.assertEqual(sha(json.dumps(msg, sort_keys=True, ensure_ascii=False)), INES_SYSTEM_SHA256)

    def test_tools_and_system_prompt_are_unchanged_by_default(self):
        self.check()

    def test_loading_every_feature_module_changes_neither(self):
        routes._table = None
        try:
            routes.table()
            self.check()
        finally:
            routes._table = None


if __name__ == "__main__":
    unittest.main()
