import { useEffect, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import { getJSON, postJSON, queueAttachment } from "../lib.js";
import { Empty } from "../Panel.jsx";
import { Icon } from "../Sidebar.jsx";
import { cue } from "../sound.js";

const KIND = { md: "Markdown", txt: "Text", pdf: "PDF", docx: "Word" };
const fmtSize = (b) => (b == null ? "" : b < 1024 ? `${b} B` : b < 1024 * 1024 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1024 / 1024).toFixed(1)} MB`);
const fmtWhen = (iso) => {
  const d = new Date(iso || "");
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
};

/** Files > Your files: the files your agent can read and write (GET /api/files), kept in the folder from
 Settings > Connectors or in the app's own folder. Open one to read it, open the original, or add it to your next message. */
export function FilesList({ world, onAttached }) {
  const [files, setFiles] = useState(null);
  const [folder, setFolder] = useState(undefined);
  const [err, setErr] = useState(null);
  const [open, setOpen] = useState(null); // id
  const [body, setBody] = useState({}); // id -> {text, truncated} | {error}
  const [added, setAdded] = useState({}); // id -> true
  const [writing, setWriting] = useState(false);
  const [msg, setMsg] = useState(null);
  const pick = useRef(null);

  const load = () => getJSON("/api/files")
    .then((r) => { if (Array.isArray(r)) { setFiles(r); setErr(null); } else { setFiles([]); setErr((r && r.error) || "Your files did not load."); } })
    .catch(() => { setFiles([]); setErr("Your files did not load."); });

  useEffect(() => {
    setFiles(null); setOpen(null); setBody({}); setAdded({}); setMsg(null);
    load();
    getJSON("/api/connectors").then((r) => setFolder((r && r.files && r.files.folder) || null)).catch(() => setFolder(null));
  }, [world]);

  const toggle = async (f) => {
    if (open === f.id) { setOpen(null); return; }
    setOpen(f.id);
    if (body[f.id] && !body[f.id].error) return;
    const r = await getJSON(`/api/files/${encodeURIComponent(f.id)}`).catch(() => ({ error: "It did not open." }));
    setBody((m) => ({ ...m, [f.id]: r && r.error ? { error: r.error } : { text: r.text || "", truncated: !!r.truncated } }));
  };

  const addToMessage = async (f) => {
    cue("press");
    const b = body[f.id];
    if (!b || b.error || !b.text.trim()) return;
    const r = await postJSON("/api/attachments", { name: f.name, text: b.text }).catch(() => ({ error: "It was not added." }));
    if (!r || r.error) { setBody((m) => ({ ...m, [f.id]: { ...b, addError: (r && r.error) || "It was not added." } })); return; }
    queueAttachment(r);
    setAdded((m) => ({ ...m, [f.id]: true }));
    onAttached && onAttached();
  };

  const addFiles = async (list) => {
    setMsg(null);
    for (const file of Array.from(list || [])) {
      if (!/\.(md|txt)$/i.test(file.name)) { setMsg(`${file.name}: add .md or .txt here. Put PDF and Word files in your files folder.`); continue; }
      const r = await postJSON("/api/files", { name: file.name, text: await file.text(), exclusive: true }).catch(() => ({ error: "It was not saved." }));
      if (!r || r.error) { setMsg(`${file.name}: ${(r && r.error) || "it was not saved."}`); continue; }
      cue("press");
    }
    load();
  };

  if (files === null) return null; // loading: no flash
  const where = folder ? `Kept in ${folder}.` : "Kept in the app's own folder.";
  return (
    <section className="fl">
      <div className="fl-head">
        <h2 className="sec">Your files</h2>
        <div className="fl-acts">
          <button type="button" className="btn btn-quiet" onClick={() => setWriting((w) => !w)} aria-expanded={writing}>New note</button>
          <button type="button" className="btn btn-quiet" onClick={() => pick.current && pick.current.click()}>Add a text file</button>
          <input ref={pick} type="file" accept=".md,.txt,text/markdown,text/plain" multiple hidden onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
        </div>
      </div>
      <p className="fl-where">{where} <Link to="/settings" search={{ section: "connectors" }}>Change the folder</Link></p>
      {writing && <NewNote onSaved={() => { setWriting(false); load(); }} />}
      {msg && <p className="fl-msg" role="status">{msg}</p>}
      {err ? <p className="fl-msg" role="alert">{err}</p> : files.length ? (
        <ul className="files fl-list">
          {files.map((f) => {
            const b = body[f.id];
            const isOpen = open === f.id;
            return (
              <li key={f.id} className={isOpen ? "open" : ""}>
                <button type="button" className="fl-row" onClick={() => toggle(f)} aria-expanded={isOpen}>
                  <Icon id="files" />
                  <span><b>{f.name}</b><small>{[KIND[f.kind] || f.kind, fmtSize(f.bytes), fmtWhen(f.created)].filter(Boolean).join(" · ")}</small></span>
                </button>
                {isOpen && (
                  <div className="fl-view">
                    {!b ? <p className="fl-msg">Opening…</p> : b.error ? <p className="fl-msg" role="alert">{b.error}</p> : (
                      <>
                        <pre className="fl-text">{b.text || "This file has no text."}</pre>
                        {b.truncated && <p className="fl-msg">This is the start of the file. Your agent reads the same part.</p>}
                        <div className="fl-acts">
                          <button type="button" className="btn btn-quiet" disabled={!!added[f.id] || !b.text.trim()} onClick={() => addToMessage(f)}>{added[f.id] ? "Added to your next message" : "Add to your next message"}</button>
                          <a className="btn btn-quiet" href={`/api/files/${encodeURIComponent(f.id)}/preview`} target="_blank" rel="noreferrer">Open the original</a>
                        </div>
                        {b.addError && <p className="fl-msg" role="alert">{b.addError}</p>}
                      </>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      ) : <Empty kind="file">No files yet. Add a note or a text file here, or ask your agent to write one. Files in your folder show here too.</Empty>}
    </section>
  );
}

/** A new note's name when none is given: note-2026-11-05-0215, so a second note never takes the first one's name. */
export function defaultNoteName(d = new Date()) {
  const p = (x) => String(x).padStart(2, "0");
  return `note-${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}

/** A short note saved as a Markdown file (POST /api/files). A name that exists is refused, never replaced. */
function NewNote({ onSaved }) {
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const save = async (e) => {
    e.preventDefault();
    const n = name.trim() || defaultNoteName();
    const file = /\.(md|txt)$/i.test(n) ? n : `${n}.md`;
    cue("press");
    setBusy(true); setErr(null);
    const r = await postJSON("/api/files", { name: file, text, exclusive: true }).catch(() => ({ error: "It was not saved." }));
    setBusy(false);
    if (!r || r.error) { setErr((r && r.error) || "It was not saved."); return; }
    onSaved && onSaved(r);
  };
  return (
    <form className="fl-note" onSubmit={save}>
      <input className="finput" value={name} onChange={(e) => setName(e.target.value)} placeholder="Name, for example plan.md" aria-label="File name" autoComplete="off" spellCheck="false" />
      <textarea className="finput" value={text} onChange={(e) => setText(e.target.value)} rows={6} placeholder="Write here" aria-label="Text" />
      <div className="fl-acts">
        <button type="submit" className="btn btn-send" disabled={busy || !text.trim()}>{busy ? "Saving…" : "Save"}</button>
      </div>
      {err && <p className="fl-msg" role="alert">{err}</p>}
    </form>
  );
}
