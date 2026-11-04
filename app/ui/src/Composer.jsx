import { useEffect, useRef, useState } from "react";
import { PlusMenu, VoiceTag } from "./Voice.jsx";
import { postJSON } from "./lib.js";

const TEXT_EXT = /\.(txt|md|markdown|csv|tsv|json|jsonl|ya?ml|toml|ini|log|html?|xml|css|js|jsx|ts|tsx|py|rs|go|java|kt|swift|rb|sh|sql|c|cc|cpp|h|hpp)$/i;
const SOON_EXT = /\.(pdf|docx?|pptx?|xlsx?|pages|key|numbers|rtf)$/i;
const MAX_BYTES = 2_000_000;

/** Reads a dropped, pasted or picked file and stores its text (POST /api/attachments). */
async function upload(file) {
  if (SOON_EXT.test(file.name)) return { name: file.name, error: "PDF and Office files arrive with the files tool tonight; text files work now." };
  if (!(TEXT_EXT.test(file.name) || (file.type || "").startsWith("text/"))) return { name: file.name, error: "Only text files can be read for now." };
  if (file.size > MAX_BYTES) return { name: file.name, error: "Too large: up to 2 MB." };
  const r = await postJSON("/api/attachments", { name: file.name, text: await file.text() });
  return r.error ? { name: file.name, error: r.error } : r;
}

/** The message box: Enter sends, Shift+Enter breaks a line; files attach by the clip, a drop or a paste.
 Open at night too: replies come from the last kept update. */
export function Composer({ busy, night, sealed, voices, onVoice, onSend, dropRef }) {
  const [text, setText] = useState("");
  const [files, setFiles] = useState([]);
  const [over, setOver] = useState(false);
  const ta = useRef(null);
  const pick = useRef(null);
  const grow = () => { const t = ta.current; if (!t) return; t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 180) + "px"; };
  const add = async (list) => {
    const arr = [...list];
    if (!arr.length || sealed) return;
    const pending = arr.map((f) => ({ name: f.name, pending: true, key: `${f.name}-${Math.random()}` }));
    setFiles((x) => [...x, ...pending]);
    const done = await Promise.all(arr.map(upload));
    setFiles((x) => x.map((f) => { const i = pending.findIndex((p) => p.key === f.key); return i >= 0 ? { ...done[i], key: f.key } : f; }));
  };
  // drop anywhere on the chat
  useEffect(() => {
    const el = dropRef && dropRef.current;
    if (!el) return;
    const enter = (e) => { if ([...(e.dataTransfer?.types || [])].includes("Files")) { e.preventDefault(); setOver(true); } };
    const leave = (e) => { if (e.target === el || !el.contains(e.relatedTarget)) setOver(false); };
    const drop = (e) => { if (e.dataTransfer?.files?.length) { e.preventDefault(); setOver(false); add(e.dataTransfer.files); } };
    el.addEventListener("dragover", enter); el.addEventListener("dragleave", leave); el.addEventListener("drop", drop);
    return () => { el.removeEventListener("dragover", enter); el.removeEventListener("dragleave", leave); el.removeEventListener("drop", drop); };
  });
  const ready = files.filter((f) => f.id);
  const waiting = files.some((f) => f.pending);
  const submit = (e) => {
    e?.preventDefault();
    const t = text.trim();
    if ((!t && !ready.length) || busy || waiting) return;
    onSend(t, ready);
    setText(""); setFiles([]);
    requestAnimationFrame(grow);
  };
  return (
    <form className={`composer${over ? " over" : ""}`} onSubmit={submit}>
      {files.length > 0 && (
        <ul className="attach-list">
          {files.map((f) => (
            <li key={f.key} className={f.error ? "err" : f.pending ? "wait" : ""}>
              <svg viewBox="0 0 20 20" width="15" height="15" aria-hidden="true"><path d="M5.5 2.5h6l3.5 3.5v10a1.5 1.5 0 0 1-1.5 1.5h-8A1.5 1.5 0 0 1 4 16V4a1.5 1.5 0 0 1 1.5-1.5Z" fill="none" stroke="currentColor" strokeWidth="1.5" /></svg>
              <span className="an">{f.name}</span>
              <span className="am">{f.error ? f.error : f.pending ? "Reading…" : `${Math.max(1, Math.round(f.chars / 1000))}k characters${f.truncated ? ", the start is sent" : ""}`}</span>
              <button type="button" className="ax" aria-label={`Remove ${f.name}`} onClick={() => setFiles((x) => x.filter((y) => y.key !== f.key))}>×</button>
            </li>
          ))}
        </ul>
      )}
      <div className="crow">
        {!sealed && (
          <>
            <PlusMenu voices={voices} onChange={onVoice} onAttach={() => pick.current?.click()} />
            <input ref={pick} type="file" multiple hidden onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
            <VoiceTag voices={voices} onChange={onVoice} />
          </>
        )}
        <label className="sr" htmlFor="msg">Message</label>
        <textarea id="msg" ref={ta} rows={1} value={text} autoComplete="off"
          placeholder={sealed ? "This day is sealed. Go to today to keep talking" : night ? "Still here tonight. Message your agent" : "Message your agent"}
          disabled={!!sealed}
          onPaste={(e) => { if (e.clipboardData?.files?.length) { e.preventDefault(); add(e.clipboardData.files); } }}
          onChange={(e) => { setText(e.target.value); grow(); }}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) submit(e); }} />
        <button type="submit" className="btn btn-send" disabled={busy || sealed || waiting || (!text.trim() && !ready.length)}>{busy ? "Answering…" : "Send"}</button>
      </div>
      {over && <p className="drop-hint">Drop to attach. The agent reads the file’s text with your message.</p>}
    </form>
  );
}
