"""Model downloads: pinned multi-file Hub fetches with streamed checksums. Fake Hub in-process; no network."""
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from afterglow import model_download  # noqa: E402


def blob_sha1(data):
    h = hashlib.sha1()
    h.update(f"blob {len(data)}\0".encode())
    h.update(data)
    return h.hexdigest()


def choice_single(rev="revA"):
    return {"id": "glimmer-mmproj", "slot": "vision", "runtime": "none",
            "repo": "meta-models/Muse-Glimmer-30B-GGUF", "revision": rev,
            "files": ["mmproj-test.gguf"], "single_file": True}


def choice_multi(rev=None):
    return {"id": "qwen3.5-4b-mac", "slot": "vision", "runtime": "mlx-vlm",
            "repo": "mlx-community/Qwen3.5-4B-MLX-4bit", "revision": rev,
            "files": None, "single_file": False}


class FakeHub:
    """Tree/sha/file endpoints with Range support. `files`: path -> bytes; `lfs`: path -> bool."""

    def __init__(self, files, rev="revA", lfs_paths=()):
        self.files = dict(files)
        self.rev = rev
        self.lfs_paths = set(lfs_paths)
        self.seen_ranges = []
        self.seen_urls = []

    def tree(self, repo):
        out = []
        for path, data in self.files.items():
            e = {"type": "file", "path": path, "size": len(data)}
            if path in self.lfs_paths:
                e["lfs"] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            else:
                e["blobId"] = blob_sha1(data)
            out.append(e)
        return out

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.seen_urls.append(url)
        if "/tree/" in url:
            body = json.dumps(self.tree(None)).encode()
            return _Resp(body)
        if url.rstrip("/").rsplit("/", 1)[-1].find("?") >= 0 or url.endswith(tuple()):
            pass
        if "/api/models/" in url and "/tree/" not in url:
            return _Resp(json.dumps({"sha": self.rev}).encode())
        # file fetch: .../resolve/<rev>/<path>
        rev, _, path = url.split("/resolve/", 1)[1].partition("/")
        import urllib.parse
        path = urllib.parse.unquote(path)
        data = self.files[path]
        rng = req.get_header("Range")
        if rng:
            self.seen_ranges.append(rng)
            start = int(rng.split("=")[1].split("-")[0])
            return _Resp(data[start:], status=206)
        return _Resp(data, status=200)


class _Resp:
    def __init__(self, body, status=200):
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        if n is None or n < 0:
            out, self._body = self._body, b""
            return out
        out, self._body = self._body[:n], self._body[n:]
        return out


def wait_state(mgr, cid, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = mgr.state(cid)
        if st.get("state") in ("done", "failed", "paused"):
            return st
        time.sleep(0.02)
    raise AssertionError(f"download {cid} stuck in {mgr.state(cid)}")


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "models"
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_multi_file_lfs_and_blob_verify_and_manifest_pins(self):
        hub = FakeHub({"model.safetensors": b"W" * 6000, "config.json": b'{"a":1}',
                       "tokenizer.json": b"T" * 100}, lfs_paths=("model.safetensors",))
        mgr = model_download.DownloadManager()
        st = mgr.start(choice_multi("revA"), opener=hub, dest_root=self.root)
        self.assertEqual(st["state"], "downloading")
        done = wait_state(mgr, "qwen3.5-4b-mac")
        self.assertEqual(done["state"], "done")
        man = json.loads((self.root / "qwen3.5-4b-mac" / ".afterglow-manifest.json").read_text())
        self.assertEqual(man["revision"], "revA")
        pol = {f["path"]: f["verified"] for f in man["files"]}
        self.assertEqual(pol, {"model.safetensors": "lfs-sha256", "config.json": "git-blob-sha1",
                               "tokenizer.json": "git-blob-sha1"})
        self.assertFalse((self.root / "qwen3.5-4b-mac" / ".afterglow-plan.json").exists())
        self.assertTrue(mgr.is_downloaded(choice_multi("revA"), dest_root=self.root))

    def test_resume_uses_range_and_keeps_pinned_rev(self):
        data = b"R" * 9000
        hub = FakeHub({"model.safetensors": data, "config.json": b"{}"}, lfs_paths=("model.safetensors",))
        mgr = model_download.DownloadManager()
        # interrupt: a half part file plus a plan pinned at revA while Hub moved to revB
        d = self.root / "qwen3.5-4b-mac"
        d.mkdir(parents=True)
        (d / "model.safetensors.part").write_bytes(data[:4000])
        (d / ".afterglow-plan.json").write_text(json.dumps({
            "choice": "qwen3.5-4b-mac", "repo": "x", "revision": "revA",
            "files": [{"path": "model.safetensors", "size": len(data),
                       "lfs_sha256": hashlib.sha256(data).hexdigest(), "blob_sha1": None},
                      {"path": "config.json", "size": 2, "lfs_sha256": None,
                       "blob_sha1": blob_sha1(b"{}")}]}) + "\n")
        hub.rev = "revB"  # main moved; resume must ignore it (unpinned choice)
        done = wait_state(mgr, "qwen3.5-4b-mac") if False else None
        st = mgr.start(choice_multi(None), opener=hub, dest_root=self.root)
        self.assertEqual(st["revision"], "revA")
        done = wait_state(mgr, "qwen3.5-4b-mac")
        self.assertEqual(done["state"], "done")
        self.assertTrue(hub.seen_ranges, "resume must send Range")
        self.assertTrue(all("/revA/" in u or "/api/" in u for u in hub.seen_urls), hub.seen_urls)
        self.assertEqual((d / "model.safetensors").read_bytes(), data)

    def test_plan_is_written_before_first_byte(self):
        seen = {}

        class Guard(FakeHub):
            def __call__(self_inner, req, timeout=None):
                if "/resolve/" in req.full_url and "plan_seen" not in seen:
                    seen["plan_seen"] = (self_inner.root_plan).exists()
                return super().__call__(req, timeout=timeout)

        hub = Guard({"a.bin": b"1" * 100})
        hub.root_plan = self.root / "qwen3.5-4b-mac" / ".afterglow-plan.json"
        mgr = model_download.DownloadManager()
        done = mgr.start(choice_multi("revP"), opener=hub, dest_root=self.root)
        self.assertEqual(done["state"], "downloading")
        self.assertTrue(hub.root_plan.exists())
        self.assertTrue(wait_state(mgr, "qwen3.5-4b-mac")["state"] == "done")
        self.assertTrue(seen.get("plan_seen"), "plan must exist before first byte")

    def test_checksum_mismatch_fails_and_deletes_part(self):
        hub = FakeHub({"model.safetensors": b"BAD!" * 1000, "config.json": b"{}"})
        # plan pins the hash of GOOD bytes, Hub serves BAD
        good = b"GOOD" * 1000
        mgr = model_download.DownloadManager()
        files = [{"path": "model.safetensors", "size": len(good),
                  "lfs_sha256": hashlib.sha256(good).hexdigest(), "blob_sha1": None}]
        # size matches BAD (same length) so only the hash catches it
        self.assertEqual(len(good), len(b"BAD!" * 1000))
        mgr.start({**choice_multi("revA"), "files": ["model.safetensors"]}, files=files,
                  opener=hub, dest_root=self.root)
        done = wait_state(mgr, "qwen3.5-4b-mac")
        self.assertEqual(done["state"], "failed")
        self.assertFalse((self.root / "qwen3.5-4b-mac" / "model.safetensors.part").exists())
        self.assertFalse(mgr.is_downloaded(choice_multi("revA"), dest_root=self.root))

    def test_corrupt_finished_file_refetches_without_double_count(self):
        good_cfg, good_w = b'{"ok":true}', b"W" * 6000
        hub = FakeHub({"model.safetensors": good_w, "config.json": good_cfg},
                      lfs_paths=("model.safetensors",))
        d = self.root / "qwen3.5-4b-mac"
        d.mkdir(parents=True)
        (d / "config.json").write_bytes(good_cfg)  # correct final, kept
        (d / "model.safetensors").write_bytes(b"BAD!!" * 1200)  # right size, wrong bytes
        mgr = model_download.DownloadManager()
        mgr.start(choice_multi("revA"), opener=hub, dest_root=self.root)
        done = wait_state(mgr, "qwen3.5-4b-mac")
        self.assertEqual(done["state"], "done")
        self.assertEqual((d / "model.safetensors").read_bytes(), good_w)
        self.assertEqual(done["done"], done["total"])  # no double count past 100%
        self.assertTrue(mgr.is_downloaded(choice_multi("revA"), dest_root=self.root))

    def test_verify_streams_large_files(self):
        data = os.urandom(3 * (1 << 20) + 7)
        fp = self.root / "big.bin"
        fp.write_bytes(data)
        entry = {"path": "big.bin", "size": len(data), "lfs_sha256": hashlib.sha256(data).hexdigest()}
        with mock.patch.object(model_download, "VERIFY_BLOCK", 1 << 16):
            self.assertEqual(model_download._verify_file(fp, entry), "lfs-sha256")
        bent = {"path": "big.bin", "size": len(data),
                "lfs_sha256": hashlib.sha256(b"x" + data[1:]).hexdigest()}
        with self.assertRaisesRegex(ValueError, "sha256"):
            model_download._verify_file(fp, bent)

    def test_completion_needs_manifest_never_a_guess(self):
        mgr = model_download.DownloadManager()
        c = choice_multi("revA")
        d = self.root / c["id"]
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}")
        (d / "model.safetensors").write_bytes(b"W" * 10)
        self.assertFalse(mgr.is_downloaded(c, dest_root=self.root), "weights without a manifest are not complete")
        s = choice_single()
        (self.root / "mmproj-test.gguf").write_bytes(b"G" * 10)
        self.assertFalse(mgr.is_downloaded(s, dest_root=self.root), "single file without a manifest is not complete")
        (d / ".afterglow-manifest.json").write_text(json.dumps(
            {"choice": c["id"], "repo": "r", "revision": "x",
             "files": [{"path": "model.safetensors", "size": 10}, {"path": "missing.json", "size": 2}]}) + "\n")
        self.assertFalse(mgr.is_downloaded(c, dest_root=self.root), "manifest with a missing file is not complete")

    def test_single_file_download_and_remove(self):
        data = b"G" * 5000
        hub = FakeHub({"mmproj-test.gguf": data}, lfs_paths=("mmproj-test.gguf",))
        mgr = model_download.DownloadManager()
        mgr.start(choice_single("revM"), opener=hub, dest_root=self.root)
        self.assertEqual(wait_state(mgr, "glimmer-mmproj")["state"], "done")
        self.assertTrue(mgr.is_downloaded(choice_single(), dest_root=self.root))
        mgr.remove(choice_single(), dest_root=self.root)
        self.assertFalse((self.root / "mmproj-test.gguf").exists())
        self.assertFalse((self.root / "mmproj-test.gguf.json").exists())
        self.assertFalse(mgr.is_downloaded(choice_single(), dest_root=self.root))

    def test_only_one_download_runs(self):
        gate = threading.Event()

        class Slow(FakeHub):
            def __call__(self_inner, req, timeout=None):
                if "/resolve/" in req.full_url:
                    gate.wait(timeout=10)
                return super().__call__(req, timeout=timeout)

        hub = Slow({"a.bin": b"1" * 100})
        mgr = model_download.DownloadManager()
        mgr.start(choice_multi("r1"), opener=hub, dest_root=self.root)
        with self.assertRaisesRegex(RuntimeError, "already running"):
            mgr.start(choice_single("r1"), opener=hub, dest_root=self.root)
        gate.set()
        self.assertEqual(wait_state(mgr, "qwen3.5-4b-mac")["state"], "done")

    def test_pause_then_resume_completes(self):
        gate = threading.Event()
        gate.set()

        class PauseMid(FakeHub):
            def __call__(self_inner, req, timeout=None):
                r = super().__call__(req, timeout=timeout)
                if "/resolve/" in req.full_url and "model.safetensors" in req.full_url:
                    orig = r.read

                    def first(n=-1):
                        out = orig(100 if n == -1 or n > 100 else n)
                        if out:
                            gate.clear()
                        return out

                    r.read = first
                return r

        data = b"P" * 5000
        hub = PauseMid({"model.safetensors": data, "config.json": b"{}"}, lfs_paths=("model.safetensors",))
        mgr = model_download.DownloadManager()
        mgr.start(choice_multi("revA"), opener=hub, dest_root=self.root)
        mgr.pause("qwen3.5-4b-mac")
        st = wait_state(mgr, "qwen3.5-4b-mac")
        self.assertIn(st["state"], ("paused", "done"))
        if st["state"] == "paused":
            mgr.start(choice_multi("revA"), opener=hub, dest_root=self.root)
            self.assertEqual(wait_state(mgr, "qwen3.5-4b-mac")["state"], "done")
        self.assertTrue(mgr.is_downloaded(choice_multi("revA"), dest_root=self.root))


if __name__ == "__main__":
    unittest.main()
