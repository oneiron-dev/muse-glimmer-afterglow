"""The person's files over HTTP for the open world (the Files screen and the owner demo upload).

GET /api/files              the person's files, the same list as the files.list tool
GET /api/files/<id>         one file's text, the same shape as the files.read tool
GET /api/files/<id>/preview the file's raw bytes with its content type
POST /api/files             JSON {name, text} only: write a .md/.txt file (the owner demo upload)
"""
from . import routes
from .files_store import FileStore, FilesError, valid_file_id


def _store(app):
    return FileStore(app.session.world)


def list_files(app, req):
    return _store(app).list_files()


def file_or_preview(app, req):
    """GET /api/files/*: one file's text, or its raw preview under <id>/preview."""
    rest = req.rest or ""
    if rest.endswith("/preview"):
        return preview(app, rest[: -len("/preview")])
    if not rest or "/" in rest:
        raise ValueError(f"bad file path {req.path!r}")
    return read(app, rest)


def read(app, fid):
    store = _store(app)
    if not valid_file_id(fid):
        raise ValueError(f"bad file id {fid!r}")
    entry = store.get_entry(fid)
    if entry is None:
        return {"error": f"no file {fid}"}, 404
    try:
        return store.read_entry(entry)
    except FilesError as e:
        return {"error": str(e)}, 500
    except OSError:
        return {"error": f"no file {fid}"}, 404


def preview(app, fid):
    store = _store(app)
    if not valid_file_id(fid):
        raise ValueError(f"bad file id {fid!r}")
    entry = store.get_entry(fid)
    if entry is None:
        return {"error": f"no file {fid}"}, 404
    try:
        body, ctype = store.preview_entry(entry)
    except OSError:
        return {"error": f"no file {fid}"}, 404
    return routes.Raw(body, ctype)


def upload(app, req):
    """POST /api/files: JSON {name, text} writes a .md/.txt file. No multipart: the demo posts text as JSON."""
    body = req.body
    if not isinstance(body, dict):
        raise ValueError("send JSON {name, text}")
    if "name" not in body or "text" not in body:
        raise ValueError("the upload needs {name, text}")
    out = _store(app).write_file(body["name"], body["text"], exclusive=bool(body.get("exclusive")))
    if out.get("exists"):
        raise RuntimeError(out["error"])  # 409: the Files screen never replaces a file without asking
    if "error" in out:
        raise ValueError(out["error"])
    return out


ROUTES = {("GET", "/api/files"): list_files, ("GET", "/api/files/*"): file_or_preview,
          ("POST", "/api/files"): upload}
