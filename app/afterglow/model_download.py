"""Resumable multi-file downloads from the Hugging Face Hub. Stdlib only.

One DownloadManager holds every choice's download state (in memory only; the
.part files on disk plus a plan file are the resume state). Importing this
module spawns nothing and writes nothing.

Storage (under models_dir(), so existing loaders find the weights):
  single-file choices (GGUF, ggml): one flat file, the same layout as
    modelsetup.Download, plus <name>.json (manifest) while complete.
  multi-file choices (MLX repos): models_dir()/<choice-id>/ with repo paths kept,
    plus .afterglow-manifest.json with the pinned revision and per-file hashes.

Revision pinning: downloads use https://huggingface.co/<repo>/resolve/<rev>/<path>
with a pinned commit sha, never mutable main. Choices with a catalog revision use
it; others resolve main once at download start and pin the resolved sha in the
plan file BEFORE any bytes are fetched. Pause, resume and restarts reuse the
plan's revision and file list, so a moving main branch can never mix revisions.
The manifest (written only when every file verifies) carries the same pin.

Checksum policy (recorded per file in manifest and plan):
  LFS files (Hub gives an lfs sha256): size plus streamed SHA256 must match.
  plain files (Hub gives a git blob sha1): size plus streamed git blob SHA1 must
    match, where blob sha1 = sha1(b"blob <size>\\0" + bytes).
  files with neither hash: size only, recorded as "size-only".
Verification streams in 4 MB blocks; multi-GB weights never sit in memory. A
mismatch deletes the part file and fails the download; nothing is renamed into
place until every file verifies.

Completion means a verified manifest: is_downloaded() requires the manifest with
every listed file present at its recorded size. A repo folder with weights but
no manifest (interrupted before the plan covered it, or a stray copy) does NOT
count, so an interrupted repo can never skip its tokenizer files. The HF cache
and legacy paths are separate externally-managed layouts; models.py checks those
itself. GET-time completion checks compare sizes only (hashes were verified at
download time); resume re-verifies finished files by hash before skipping them.
"""
import hashlib
import json
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MANIFEST = ".afterglow-manifest.json"
PLAN = ".afterglow-plan.json"
CHUNK = 1 << 20
VERIFY_BLOCK = 1 << 22


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def git_blob_sha1(data):
    """The git blob id for small bytes (tests and tiny files)."""
    h = hashlib.sha1()
    h.update(f"blob {len(data)}\0".encode())
    h.update(data)
    return h.hexdigest()


def models_dir():
    """Late import so tests can point AFTERGLOW_MODELS anywhere."""
    from . import modelsetup
    return modelsetup.models_dir()


def _dest(choice, root):
    if choice.get("single_file"):
        return root / choice["files"][0].rsplit("/", 1)[-1]
    return root / choice["id"]


def _manifest_path(choice, root):
    d = _dest(choice, root)
    return d.parent / (d.name + ".json") if choice.get("single_file") else d / MANIFEST


def _plan_path(choice, root):
    d = _dest(choice, root)
    return d.parent / (d.name + ".plan.json") if choice.get("single_file") else d / PLAN


def dest_for(choice):
    """Where a choice's weights live: a file for single-file, a dir for repos."""
    return _dest(choice, models_dir())


def manifest_for(choice):
    return _manifest_path(choice, models_dir())


def read_manifest(choice):
    f = manifest_for(choice)
    try:
        doc = json.loads(f.read_text())
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def _read_plan(choice, root):
    try:
        doc = json.loads(_plan_path(choice, root).read_text())
        if isinstance(doc, dict) and doc.get("revision") and isinstance(doc.get("files"), list) and doc["files"]:
            return doc
    except (OSError, ValueError):
        pass
    return None


def _write_plan(choice, root, rev, files):
    p = _plan_path(choice, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    slim = [{"path": e["path"], "size": e.get("size"),
             "lfs_sha256": e.get("lfs_sha256"), "blob_sha1": e.get("blob_sha1")} for e in files]
    p.write_text(json.dumps({"choice": choice["id"], "repo": choice["repo"], "revision": rev,
                             "planned_at": now_iso(), "files": slim}, indent=1) + "\n")
    return p


def hf_tree(repo, revision="main", opener=urllib.request.urlopen, timeout=15):
    """Repo files at a revision: [{"path", "size", "lfs_sha256"|None, "blob_sha1"|None}].

    Uses the tree API (recursive). `opener` is injectable for tests; it must act
    like urlopen and return a context manager of JSON bytes.
    """
    url = f"https://huggingface.co/api/models/{repo}/tree/{revision}?recursive=true"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with opener(req, timeout=timeout) as r:
        tree = json.loads(r.read())
    out = []
    for e in tree if isinstance(tree, list) else []:
        if not isinstance(e, dict):
            continue
        if e.get("type") == "directory":
            continue
        if e.get("type") not in ("file", "blob", None):
            continue
        path = e.get("path") or e.get("rfilename")
        if not path:
            continue
        lfs = e.get("lfs") or {}
        out.append({"path": path, "size": int(e.get("size") or 0),
                    "lfs_sha256": lfs.get("sha256") or lfs.get("oid"),
                    "blob_sha1": e.get("blobId") or e.get("blob_id") or e.get("oid")})
    return out


def hf_sha(repo, opener=urllib.request.urlopen, timeout=15):
    """The current commit sha of a repo's main branch (pinned at download start)."""
    url = f"https://huggingface.co/api/models/{repo}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with opener(req, timeout=timeout) as r:
        return json.loads(r.read()).get("sha")


def _verify_file(path, entry):
    """Check one finished file against its Hub hashes, streaming in 4 MB blocks.
    Returns the policy used. Never loads the whole file into memory."""
    size = Path(path).stat().st_size
    if entry.get("size") and size != entry["size"]:
        raise ValueError(f"{entry['path']}: got {size} bytes, expected {entry['size']}")
    if entry.get("lfs_sha256"):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(VERIFY_BLOCK), b""):
                h.update(block)
        if h.hexdigest() != entry["lfs_sha256"]:
            raise ValueError(f"{entry['path']}: sha256 mismatch")
        return "lfs-sha256"
    if entry.get("blob_sha1"):
        h = hashlib.sha1()
        h.update(f"blob {size}\0".encode())
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(VERIFY_BLOCK), b""):
                h.update(block)
        if h.hexdigest() != entry["blob_sha1"]:
            raise ValueError(f"{entry['path']}: blob sha1 mismatch")
        return "git-blob-sha1"
    return "size-only"


def _filter_tree(choice, tree):
    if choice.get("files"):
        want = set(choice["files"])
        files = [e for e in tree if e["path"] in want]
        missing = want - {e["path"] for e in files}
        if missing:
            raise ValueError(f"{choice['repo']}: Hub has no {sorted(missing)}")
        return files
    files = [e for e in tree if not e["path"].endswith(".gitattributes")]
    if not files:
        raise ValueError(f"{choice['repo']}: Hub lists no files")
    return files


class DownloadManager:
    """One download at a time across choices; per-choice progress for GET."""

    def __init__(self):
        self._lock = threading.Lock()
        self._states = {}  # choice id -> state dict
        self._stop = {}  # choice id -> Event
        self._thread = None
        self._running_id = None

    def state(self, choice_id):
        with self._lock:
            return dict(self._states.get(choice_id) or {"state": "idle", "id": choice_id})

    def all_states(self):
        with self._lock:
            return {k: dict(v) for k, v in self._states.items()}

    def running(self):
        with self._lock:
            return self._running_id

    def plan(self, choice, dest_root=None):
        """The persisted pinned plan for a choice, or None (resume reuses it)."""
        root = Path(dest_root) if dest_root else models_dir()
        return _read_plan(choice, root)

    def start(self, choice, files=None, opener=urllib.request.urlopen, dest_root=None, on_done=None):
        """Start (or resume) a choice's download. `files` overrides the Hub listing
        (tests pass a fake Hub); `dest_root` overrides models_dir() (tests).
        `on_done(choice)` runs once in the worker thread when every file verifies.

        The pinned plan (revision + file list + hashes) is written BEFORE any
        bytes are fetched; resume and restarts reuse it instead of re-resolving
        main, so a download never mixes revisions."""
        cid = choice["id"]
        with self._lock:
            if self._running_id is not None:
                if self._running_id != cid:
                    raise RuntimeError("another download is already running or preparing")
                return dict(self._states[cid])
            if self.is_downloaded(choice, dest_root=dest_root):
                root = Path(dest_root) if dest_root else models_dir()
                _plan_path(choice, root).unlink(missing_ok=True)  # stale plan from a crash
                st = {"state": "done", "id": cid, "done": 0, "total": 0,
                      "line": "Already downloaded."}
                self._states[cid] = st
                return dict(st)
            # Reserve the slot before Hub metadata or plan preparation can block.
            ev = threading.Event()
            self._stop[cid] = ev
            self._running_id = cid
            self._states[cid] = {"state": "downloading", "id": cid, "done": 0, "total": 0,
                                 "line": "Preparing the download."}
        try:
            root = Path(dest_root) if dest_root else models_dir()
            root.mkdir(parents=True, exist_ok=True)
            if files is None:
                pinned = choice.get("revision")
                old = _read_plan(choice, root)
                if pinned:
                    rev = pinned
                    if old and old.get("revision") == rev:
                        files = old["files"]
                    else:
                        files = _filter_tree(choice, hf_tree(choice["repo"], rev, opener=opener))
                elif old:
                    rev, files = old["revision"], old["files"]
                else:
                    rev = hf_sha(choice["repo"], opener=opener)
                    if not rev:
                        raise ValueError(f"{choice['repo']}: Hub gave no commit sha")
                    files = _filter_tree(choice, hf_tree(choice["repo"], rev, opener=opener))
            else:
                old = _read_plan(choice, root)
                rev = choice.get("revision") or (old.get("revision") if old else None) or "test"
            dest = _dest(choice, root)
            total = sum(e.get("size") or 0 for e in files)
            have = 0
            for e in files:
                fp = dest if choice.get("single_file") else dest / e["path"]
                part = Path(str(fp) + ".part")
                if fp.is_file() and e.get("size") and fp.stat().st_size == e["size"]:
                    have += e["size"]
                elif part.is_file():
                    have += min(part.stat().st_size, e.get("size") or part.stat().st_size)
            free = shutil.disk_usage(root).free
            if free < max(total - have, 0) + 512 * 1024 * 1024:
                raise RuntimeError(f"Not enough free disk space in {root}: "
                                   f"about {(total - have) / 1e9:.1f} GB more is needed.")
            _write_plan(choice, root, rev, files)
            with self._lock:
                st = {"state": "downloading", "id": cid, "revision": rev, "done": have, "total": total,
                      "files_done": 0, "files_total": len(files), "file": None,
                      "started_at": now_iso(), "rate_mb_s": None}
                self._states[cid] = st
                self._thread = threading.Thread(target=self._run,
                                                args=(choice, files, rev, dest, root, opener, ev, on_done), daemon=True)
                self._thread.start()
                return dict(st)
        except Exception as e:
            with self._lock:
                self._running_id = None
                self._states[cid].update(state="failed", line=f"The download could not start: {e}.")
            raise

    def _run(self, choice, files, rev, dest, root, opener, stop, on_done=None):
        cid = choice["id"]
        single = bool(choice.get("single_file"))
        try:
            t0, got0 = time.time(), self._states[cid]["done"]
            done = got0
            verified = []
            for i, e in enumerate(files):
                if stop.is_set():
                    break
                fp = dest if single else dest / e["path"]
                fp.parent.mkdir(parents=True, exist_ok=True)
                part = Path(str(fp) + ".part")
                with self._lock:
                    self._states[cid].update(file=e["path"], files_done=i)
                # a finished file with the right size is hash-checked before skipping
                if fp.is_file() and e.get("size") and fp.stat().st_size == e["size"]:
                    try:
                        policy = _verify_file(fp, e)
                    except ValueError:
                        fp.unlink()
                        # it was counted in done at start; drop it before refetching
                        done -= e["size"]
                        with self._lock:
                            self._states[cid]["done"] = done
                    else:
                        verified.append({**e, "verified": policy})
                        continue
                have = part.stat().st_size if part.is_file() else 0
                if e.get("size") and have > e["size"]:
                    part.unlink()
                    have = 0
                url = f"https://huggingface.co/{choice['repo']}/resolve/{rev}/{urllib.parse.quote(e['path'])}"
                req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
                with opener(req, timeout=60) as r, open(part, "ab" if have else "wb") as out:
                    if have and getattr(r, "status", 206) != 206:
                        out.truncate(0)
                        done -= have
                        have = 0
                        with self._lock:
                            self._states[cid]["done"] = done
                    while not stop.is_set():
                        chunk = r.read(CHUNK)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        with self._lock:
                            s = self._states[cid]
                            s["done"] = done
                            s["rate_mb_s"] = round((done - got0) / 1e6 / max(time.time() - t0, 1e-3), 1)
                if stop.is_set():
                    break
                try:
                    policy = _verify_file(part, e)
                except ValueError as ve:
                    part.unlink(missing_ok=True)
                    with self._lock:
                        self._states[cid].update(state="failed", line=f"{ve}. The part was deleted; start again.")
                    return
                part.rename(fp)
                verified.append({**e, "verified": policy})
                with self._lock:
                    self._states[cid]["files_done"] = i + 1
            if stop.is_set():
                with self._lock:
                    self._states[cid].update(state="paused",
                                             line="Paused. Start again to resume where it stopped.")
                return
            man = {"choice": cid, "repo": choice["repo"], "revision": rev, "done_at": now_iso(),
                   "files": [{"path": v["path"], "size": v.get("size"),
                               "lfs_sha256": v.get("lfs_sha256"), "blob_sha1": v.get("blob_sha1"),
                               "verified": v.get("verified")} for v in verified]}
            mf = _manifest_path(choice, root)
            mf.parent.mkdir(parents=True, exist_ok=True)
            mf.write_text(json.dumps(man, indent=1) + "\n")
            _plan_path(choice, root).unlink(missing_ok=True)
            with self._lock:
                self._states[cid].update(state="done", done=done,
                                         files_done=len(files), file=None, verified=True,
                                         line="Downloaded and checked against the published checksums.")
            if on_done is not None:
                try:
                    on_done(choice)
                except Exception:
                    pass
        except (OSError, urllib.error.URLError) as e:
            with self._lock:
                self._states[cid].update(state="failed", line=f"The download stopped: {e}. Start again to resume.")
        except Exception as e:  # never kill the server thread pool on a bad Hub reply
            with self._lock:
                self._states[cid].update(state="failed", line=f"The download stopped: {e}.")
        finally:
            with self._lock:
                if self._running_id == cid:
                    self._running_id = None

    def pause(self, choice_id):
        with self._lock:
            ev = self._stop.get(choice_id)
            st = self._states.get(choice_id)
        if ev is not None:
            ev.set()
        return dict(st) if st else {"state": "idle", "id": choice_id}

    def remove(self, choice, dest_root=None):
        """Delete weights this manager downloaded (never the HF cache or legacy paths)."""
        cid = choice["id"]
        with self._lock:
            if self._running_id == cid:
                raise RuntimeError("pause the download before removing it")
            self._states.pop(cid, None)
            self._stop.pop(cid, None)
        root = Path(dest_root) if dest_root else models_dir()
        if choice.get("single_file"):
            name = choice["files"][0].rsplit("/", 1)[-1]
            for p in (root / name, Path(str(root / name) + ".part"),
                      root / (name + ".json"), root / (name + ".plan.json")):
                try:
                    if p.is_file():
                        p.unlink()
                except OSError:
                    pass
        else:
            import shutil as _sh
            d = root / cid
            if d.is_dir() and d.parent == root:
                _sh.rmtree(d, ignore_errors=True)
        return {"state": "idle", "id": cid}

    def is_downloaded(self, choice, dest_root=None):
        """True only with a verified manifest and every listed file present at its
        recorded size. No manifest means not complete, even when weights sit in
        the folder. No network, no writes."""
        root = Path(dest_root) if dest_root else models_dir()
        man = _manifest_path(choice, root)
        try:
            doc = json.loads(man.read_text()) if man.is_file() else None
        except (OSError, ValueError):
            return False
        files = (doc or {}).get("files")
        if not isinstance(files, list) or not files:
            return False
        dest = _dest(choice, root)
        for e in files:
            if not e.get("path"):
                return False
            fp = dest if choice.get("single_file") else dest / e["path"]
            try:
                if not fp.is_file() or fp.stat().st_size != e.get("size"):
                    return False
            except OSError:
                return False
        return True
