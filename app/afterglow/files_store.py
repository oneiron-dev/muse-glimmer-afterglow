"""The person's files: list, read and write under one per-world store.

The storage root is the person's files folder (connectors.files_folder) when one is set, else
runs/<world>/files/. The configured folder is never created here: when it is missing, list
returns [] and read/write report it. The fallback folder is created on writes only. No network.

Lists and reads never write into the configured folder and never recurse: only supported direct
children (.md/.txt/.pdf/.docx) are listed, never index.json. Sequential F001 ids live in a small
index (runs/<world>/files/index.json in fallback mode, runs/<world>/files-index.json when a folder
is configured); every list and read reconciles it with a directory scan, so user documents dropped
into the folder appear without being written by the tool.
"""
import datetime as dt
import json
import logging
import os
import re
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from . import connectors, paths

MAX_TEXT_CHARS = 20000  # a read returns at most this many characters, with truncated set
SUPPORTED = {".md": "md", ".txt": "txt", ".pdf": "pdf", ".docx": "docx"}
WRITABLE = {".md", ".txt"}
INDEX_NAME = "index.json"
ID_RE = re.compile(r"F\d{3,}")
NAME_MAX = 120
JST = dt.timezone(dt.timedelta(hours=9))

PREVIEW_TYPES = {"md": "text/markdown; charset=utf-8", "txt": "text/plain; charset=utf-8",
                 "pdf": "application/pdf",
                 "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


class FilesError(Exception):
    """A file that exists but cannot be read (a broken PDF or Word file, a missing PDF reader)."""


def now_iso():
    return dt.datetime.now(JST).isoformat(timespec="milliseconds")


def valid_file_id(fid):
    return isinstance(fid, str) and ID_RE.fullmatch(fid) is not None


def validate_name(name):
    """A write's file name, cleaned, or (None, why): plain .md/.txt basenames only."""
    if not isinstance(name, str):
        return None, "a file name must be a string"
    safe = " ".join(name.split())
    if not safe:
        return None, "a file name is needed"
    if len(safe) > NAME_MAX:
        return None, "that file name is too long"
    if "/" in safe or "\\" in safe or ".." in safe or os.path.isabs(safe):
        return None, f"bad file name {name!r}"
    if safe.startswith((".", "-")) or ":" in safe or "\x00" in safe:
        return None, f"bad file name {name!r}"
    if Path(safe).suffix.lower() not in WRITABLE:
        return None, "only .md and .txt files can be written"
    return safe, None


def configured_root(world):
    """The person's files folder as a Path, or None when none is set (connectors.files_folder)."""
    return connectors.files_folder(world)


def _next_id(index):
    n = max((int(i[1:]) for i in index if valid_file_id(i)), default=0) + 1
    return f"F{n:03d}"


def _mtime_iso(path):
    try:
        ts = path.stat().st_mtime
    except OSError:
        return now_iso()
    return dt.datetime.fromtimestamp(ts, JST).isoformat(timespec="milliseconds")


def extract_pdf_text(path):
    """Every page's text, joined; raises FilesError without pypdf or on a broken file."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise FilesError("PDF files need the pypdf package to be read.")
    log, level = logging.getLogger("pypdf"), logging.getLogger("pypdf").level
    log.setLevel(logging.CRITICAL)  # a broken user PDF reports as an error, not log noise
    try:
        reader = PdfReader(str(path))
        parts = []
        for pg in reader.pages:
            parts.append(pg.extract_text() or "")
            if sum(map(len, parts)) > MAX_TEXT_CHARS + 1000:
                break
    except FilesError:
        raise
    except Exception as e:
        raise FilesError(f"that PDF could not be read ({type(e).__name__}).")
    finally:
        log.setLevel(level)
    return "\n".join(p for p in parts if p.strip())


def extract_docx_text(path):
    """The paragraphs of word/document.xml, joined; raises FilesError on a broken file."""
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise FilesError("that Word file could not be read.")
    with zf:
        try:
            xml_bytes = zf.read("word/document.xml")
        except KeyError:
            raise FilesError("that Word file has no document text.")
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError:
            raise FilesError("that Word file could not be read.")
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paras = []
    for p in root.iter(ns + "p"):
        paras.append("".join(t.text for t in p.iter(ns + "t") if t.text))
        if sum(map(len, paras)) > MAX_TEXT_CHARS + 1000:
            break
    text = "\n".join(paras)
    if not text.strip():  # text outside paragraphs (tables are paragraphs already)
        text = "".join(t.text for t in root.iter(ns + "t") if t.text)
    return text


class FileStore:
    """One person's files: the configured folder when set, else runs/<world>/files/."""

    def __init__(self, world):
        self.world = world
        self.configured = configured_root(world)
        if self.configured is not None:
            self.root = self.configured
            self.index_path = paths.runs_root() / world / "files-index.json"
        else:
            self.root = paths.runs_root() / world / "files"
            self.index_path = self.root / INDEX_NAME

    @property
    def is_configured(self):
        return self.configured is not None

    def _missing_configured(self):
        return self.is_configured and not self.root.is_dir()

    # --- index ---

    def _load_index(self):
        try:
            doc = json.loads(self.index_path.read_text())
        except (OSError, ValueError):
            return {}
        return {i: e for i, e in doc.items() if valid_file_id(i) and isinstance(e, dict)} \
            if isinstance(doc, dict) else {}

    def _save_index(self, index):
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(index, indent=1, ensure_ascii=False))
        os.replace(tmp, self.index_path)

    def _scan(self):
        """Supported direct children only: no recursion, no symlinks, never index.json."""
        if not self.root.is_dir():
            return {}
        try:
            children = list(self.root.iterdir())
        except OSError:
            return {}
        out = {}
        for p in children:
            if p.is_symlink() or not p.is_file() or p.name == INDEX_NAME or p.name.startswith("."):
                continue
            kind = SUPPORTED.get(p.suffix.lower())
            if kind is None:
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            out[p.name] = {"name": p.name, "kind": kind, "bytes": size}
        return out

    def _reconcile(self, index, scan):
        """Fold the scan into the index: adopt new files, drop vanished ones, refresh sizes. True when changed."""
        changed = False
        for fid in [i for i, e in index.items() if not isinstance(e.get("name"), str) or e["name"] not in scan]:
            del index[fid]
            changed = True
        by_name = {e["name"]: fid for fid, e in index.items()}
        for name in sorted(scan):
            meta = scan[name]
            if name in by_name:
                e = index[by_name[name]]
                if e.get("kind") != meta["kind"] or e.get("bytes") != meta["bytes"]:
                    e.update(kind=meta["kind"], bytes=meta["bytes"])
                    changed = True
            else:
                fid = _next_id(index)
                index[fid] = {"id": fid, "name": name, "kind": meta["kind"], "bytes": meta["bytes"],
                              "created": _mtime_iso(self.root / name)}
                by_name[name] = fid
                changed = True
        return changed

    def _entries(self):
        """The reconciled index; persists only when the scan changed it."""
        scan = self._scan()
        index = self._load_index()
        if self._reconcile(index, scan) and (index or self.index_path.exists()):
            self._save_index(index)
        return index

    # --- the tools and the routes ---

    def list_files(self):
        """[{id, name, kind, bytes, created}], by name. A missing configured folder lists empty."""
        if self._missing_configured():
            return []
        index = self._entries()
        return [{"id": e["id"], "name": e["name"], "kind": e["kind"], "bytes": e["bytes"],
                 "created": e.get("created")} for e in sorted(index.values(), key=lambda e: e["name"])]

    def get_entry(self, fid):
        """One reconciled entry by id, or None (a bad id is None too)."""
        if not valid_file_id(fid) or self._missing_configured():
            return None
        return self._entries().get(fid)

    def entry_text(self, entry):
        """A reconciled entry's text (extracted for PDF/Word); raises FilesError when unreadable."""
        path = self.root / entry["name"]
        kind = entry["kind"]
        if kind in ("md", "txt"):
            raw = path.read_bytes()
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("utf-8", errors="replace")
        if kind == "pdf":
            return extract_pdf_text(path)
        if kind == "docx":
            return extract_docx_text(path)
        raise FilesError(f"that file's kind {kind!r} cannot be read.")

    def read_entry(self, entry):
        """read_file's response for a reconciled entry; raises FilesError or OSError."""
        text = self.entry_text(entry)
        text, cut = (text[:MAX_TEXT_CHARS], True) if len(text) > MAX_TEXT_CHARS else (text, False)
        return {"id": entry["id"], "name": entry["name"], "kind": entry["kind"], "text": text,
                "bytes": entry["bytes"], "truncated": cut}

    def read_file(self, fid):
        """{id, name, kind, text, bytes} plus truncated, capped at MAX_TEXT_CHARS; or {"error", ...}."""
        if not valid_file_id(fid):
            return {"error": f"bad file id {fid!r}"}
        if self._missing_configured():
            return {"error": f"the files folder {self.root} is missing"}
        entry = self._entries().get(fid)
        if entry is None:
            return {"error": f"no file {fid}"}
        try:
            return self.read_entry(entry)
        except FilesError as e:
            return {"error": str(e)}
        except OSError:
            return {"error": f"no file {fid}"}

    def preview_entry(self, entry):
        """(raw bytes, content type) for a reconciled entry; raises OSError when vanished."""
        return (self.root / entry["name"]).read_bytes(), PREVIEW_TYPES[entry["kind"]]

    def write_file(self, name, text, exclusive=False):
        """Write a .md/.txt file; {id, name}, or {"error", ...}. Overwrites the same name, unless `exclusive`
        (a new file from the Files screen): then an existing name is refused with {"error", "exists": True}."""
        if not isinstance(text, str):
            return {"error": "the file text must be a string"}
        safe, err = validate_name(name)
        if err:
            return {"error": err}
        if self._missing_configured():
            return {"error": f"the files folder {self.root} is missing"}
        if not self.is_configured:
            try:
                self.root.mkdir(parents=True, exist_ok=True)
            except OSError:
                return {"error": f"the files folder {self.root} could not be made"}
        path = self.root / safe
        if path.is_symlink() or (path.exists() and not path.is_file()):
            return {"error": f"{safe} cannot be written there"}
        if exclusive and path.exists():
            return {"error": f"A file named {safe} already exists. Choose another name.", "exists": True}
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, prefix=".files-", delete=False) as f:
                tmp = Path(f.name)
                f.write(text.encode("utf-8"))
            os.replace(tmp, path)
        except OSError:
            if tmp is not None:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            return {"error": f"{safe} could not be written"}
        index = self._entries()  # adopts the write (or refreshes it)
        fid = next((i for i, e in index.items() if e["name"] == safe), None)
        return {"id": fid, "name": safe} if fid else {"error": f"{safe} could not be listed"}
