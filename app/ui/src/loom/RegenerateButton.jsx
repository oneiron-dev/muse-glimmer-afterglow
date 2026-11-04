import { useState } from "react";
import { branchStream } from "./stream.js";
import { cue } from "../sound.js";

/**
 * RegenerateButton: for an assistant message. It forks at that turn with
 * the same user words and streams the new reply from
 * POST /api/branch/regenerate.
 *
 * Props:
 * - turn: the turn number of the assistant message (the user turn and
 *   its reply share one turn number, so this is the fork point).
 * - userText: the user words above this reply, echoed in the live turn.
 * - label: the button words ("Ask again" when absent).
 * - sealed: true on a sealed day (renders nothing; forks are refused).
 * - disabled: true while another turn streams.
 * - onLive, onNote, onReload: as in EditMessage.
 */
export function RegenerateButton({ turn, userText, label, sealed, disabled, onLive, onNote, onReload }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  if (sealed) return null;
  const again = async () => {
    if (busy || disabled) return;
    cue("press");
    setBusy(true);
    setError(null);
    const live = { user: userText || "", files: [], tools: [], text: "", thinking: true, done: false, branch: true, regenerate: turn };
    if (onLive) onLive({ ...live });
    try {
      for await (const ev of branchStream("/api/branch/regenerate", { turn })) {
        if (ev.type === "tool_result") live.tools = [...live.tools, { name: ev.name, arguments: ev.arguments, result: ev.result }];
        else if (ev.type === "content") { live.text += ev.text; live.thinking = false; }
        else if (ev.type === "note" && onNote) onNote(ev);
        else if (ev.type === "error") { live.error = ev.message; live.thinking = false; live.done = true; setError(ev.message); }
        else if (ev.type === "turn_done") { live.thinking = false; live.done = true; if (ev.content != null) live.text = ev.content; }
        if (onLive) onLive({ ...live });
      }
    } catch {
      live.error = "The message did not go through.";
      live.thinking = false;
      live.done = true;
      setError(live.error);
      if (onLive) onLive({ ...live });
    }
    setBusy(false);
    if (onLive) onLive(null);
    if (onReload) await onReload();
  };
  return (
    <>
      <button type="button" className="act" disabled={disabled || busy} onClick={again} title="Get another answer. This one stays one step back.">
        {busy ? "Asking…" : (label || "Ask again")}
      </button>
      {error && <p className="ferr" role="alert">{error}</p>}
    </>
  );
}
