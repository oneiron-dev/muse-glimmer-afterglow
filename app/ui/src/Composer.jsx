import { useRef, useState } from "react";

/** The message box: Enter sends, Shift+Enter breaks a line. Open at night too: replies come from the last kept update. */
export function Composer({ first, busy, night, onSend }) {
  const [text, setText] = useState("");
  const ta = useRef(null);
  const grow = () => { const t = ta.current; if (!t) return; t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 180) + "px"; };
  const submit = (e) => {
    e?.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    onSend(t);
    setText("");
    requestAnimationFrame(grow);
  };
  return (
    <form className="composer" onSubmit={submit}>
      <label className="sr" htmlFor="msg">Message</label>
      <textarea id="msg" ref={ta} rows={1} value={text} autoComplete="off"
        placeholder={night ? `Still here tonight. Message ${first}’s agent` : `Message ${first}’s agent`}
        onChange={(e) => { setText(e.target.value); grow(); }}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) submit(e); }} />
      <button type="submit" className="btn btn-send" disabled={busy || !text.trim()}>{busy ? "Answering…" : "Send"}</button>
    </form>
  );
}
