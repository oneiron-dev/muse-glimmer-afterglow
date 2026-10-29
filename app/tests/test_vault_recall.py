"""Offline tests for vault recall (oneiron_vault.OneironVault.recall, notes.OneironNotes.recall, the recall tool):
every recall names deadline_ms, and a pack the vault cut short (retrieval_meta.partial) is "not finished", never
"nothing found". No network: the facade and the vault are stand-ins.

  python3 -m unittest discover -s app/tests
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import oneiron_vault  # noqa: E402
from afterglow.notes import LocalJsonNotes, OneironNotes  # noqa: E402
from afterglow.tools import ToolBox  # noqa: E402


class StandInVault:
    def __init__(self, partial):
        self.partial = partial

    def recall(self, query, limit=5):
        return {"items": [{"short_id": "v1", "predicate": "lab mate", "value_text": "Jonas Weber"}],
                "retrieval_meta": {"partial": self.partial}}

    def witness(self, *a, **kw):
        return []


class VaultRecallTest(unittest.TestCase):
    def test_the_client_names_its_deadline_and_waits_past_it(self):
        sent = {}
        v = oneiron_vault.OneironVault.__new__(oneiron_vault.OneironVault)
        v.facade = lambda verb, body, timeout=None: sent.update(verb=verb, body=body, timeout=timeout) or {}
        v.recall("lab mate", limit=3)
        self.assertEqual(sent["verb"], "vault.recall")
        self.assertEqual(sent["body"]["deadline_ms"], 30000)
        self.assertGreater(sent["timeout"], 30)

    def test_the_two_speaker_client_recalls_with_the_companion_credential(self):
        sv = oneiron_vault.SpeakerVaults.__new__(oneiron_vault.SpeakerVaults)
        asked = []
        sv.by_author = {"user": None, "companion": type("C", (), {"recall": lambda self, *a, **kw: asked.append(kw) or {}})()}
        sv.recall("lab mate")
        self.assertEqual(asked[0]["deadline_ms"], 30000)

    def test_a_partial_pack_is_not_finished(self):
        self.assertTrue(oneiron_vault.recall_partial({"retrieval_meta": {"partial": True}}))
        self.assertFalse(oneiron_vault.recall_partial({"items": []}))

    def notes(self, partial):
        local = LocalJsonNotes(Path(tempfile.mkdtemp()) / "notes.json")
        return OneironNotes(local, "w", 1, vault=StandInVault(partial))

    def test_the_recall_tool_says_not_finished_when_the_vault_stopped_early(self):
        n = self.notes(True)
        out, _ = ToolBox(n, None, day=1).run("recall", {"query": "lab mate"})
        self.assertEqual([x["value"] for x in out["notes"]], ["Jonas Weber"])  # what it found stands
        self.assertIn("not finished", out["vault"])
        self.assertIn("not finished (partial)", n.errors[0])
        out, _ = ToolBox(self.notes(False), None, day=1).run("recall", {"query": "lab mate"})
        self.assertNotIn("vault", out)


if __name__ == "__main__":
    unittest.main()
