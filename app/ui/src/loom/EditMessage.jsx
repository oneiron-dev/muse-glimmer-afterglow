import { useRef, useState } from "react";
import { branchStream } from "./stream.js";
import { cue } from "../sound.js";

/**
 * EditMessage: inline editor for a user message. Send forks the day at
 * that turn and streams the new reply from POST /api/branch.
 *
 * Props:
 * - turn: the user turn number to fork at (at_turn).
 * - text: the message text shown now (the draft starts from it).
 * - sealed: true on a sealed day (renders nothing; forks are refused).
 * - disabled: true while another turn streams.
 * - onLive: called with the streaming turn (the App send() shape:
 *   {user, files, tools, text, thinking, done, error}) and with null
 *   at the end, so the app can show it in the thread.
 * - onNote: called once per streamed note event.
 * - onReload: async callback after a sent turn to reload the app state.
 *
 * On failure the editor stays open with the draft and the error, so the
 * user can retry; nothing is reloaded. While busy or disabled the form,
 * its keyboard shortcuts and Cancel are all off.
 */
export function EditMessage({ turn, text, files, sealed, disabled, onLive, onNote, onReload }) {
  const attached = (files || []).filter((f) => f.id);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(text || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const ta = useRef(null);
  if (sealed) return null;
  const locked = busy || disabled;
  const grow = () => { const t = ta.current; if (!t) return; t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 180) + "px"; };
  const toggle = (v) => {
    if (!v && locked) return;
    cue("toggle");
    setError(null);
    if (v) {
      setDraft(text || "");
      requestAnimationFrame(grow);
    }
    setOpen(v);
  };
  const send = async (e) => {
    e && e.preventDefault();
    const t = draft.trim();
    if (!t || busy || disabled) return;
    cue("press");
    setBusy(true);
    setError(null);
    const live = { user: t, files: attached, tools: [], text: "", thinking: true, done: false, branch: true };
    if (onLive) onLive({ ...live });
    let failed = false;
    try {
      for await (const ev of branchStream("/api/branch", { at_turn: turn, text: t, ...(attached.length ? { attachments: attached.map((f) => f.id) } : {}) })) {
        if (ev.type === "tool_result") live.tools = [...live.tools, { name: ev.name, arguments: ev.arguments, result: ev.result }];
        else if (ev.type === "content") { live.text += ev.text; live.thinking = false; }
        else if (ev.type === "note" && onNote) onNote(ev);
        else if (ev.type === "error") { live.error = ev.message; live.thinking = false; live.done = true; failed = true; setError(ev.message); }
        else if (ev.type === "turn_done") { live.thinking = false; live.done = true; if (ev.content != null) live.text = ev.content; }
        if (onLive) onLive({ ...live });
      }
    } catch {
      failed = true;
      live.error = "The message did not go through.";
      live.thinking = false;
      live.done = true;
      setError(live.error);
      if (onLive) onLive({ ...live });
    }
    setBusy(false);
    if (onLive) onLive(null);
    if (failed) return;
    setOpen(false);
    if (onReload) await onReload();
  };
  if (!open) return <button type="button" className="act" disabled={disabled} onClick={() => toggle(true)}>Edit and resend</button>;
  return (
    <form className="composer msg-edit" onSubmit={send}>
      <div className="crow">
        <label className="sr" htmlFor={`edit-${turn}`}>Edit your message</label>
        <textarea id={`edit-${turn}`} ref={ta} rows={2} value={draft} autoComplete="off" disabled={locked}
          onChange={(e) => { if (!locked) { setDraft(e.target.value); grow(); } }}
          onKeyDown={(e) => {
            if (locked) return;
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) send(e);
            if (e.key === "Escape") toggle(false);
          }} />
      </div>
      <div className="factions">
        <button type="submit" className="btn btn-send" disabled={locked || !draft.trim()}>{busy ? "Sending…" : "Send"}</button>
        <button type="button" className="btn btn-quiet" disabled={locked} onClick={() => toggle(false)}>Cancel</button>
      </div>
      {attached.length > 0 && !busy && <p className="fhint">{attached.length === 1 ? `${attached[0].name} stays attached.` : `${attached.map((f) => f.name).join(", ")} stay attached.`}</p>}
      {busy && <p className="fhint">Sending. Your first version stays one step back.</p>}
      {error && <p className="ferr" role="alert">{error}</p>}
    </form>
  );
}
