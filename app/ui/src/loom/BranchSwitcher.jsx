import { useState } from "react";
import { postJSON } from "../lib.js";
import { cue } from "../sound.js";

const WORDS = {
  message: { group: "Versions of your message", prev: "Previous version", next: "Next version" },
  answer: { group: "Answers to this message", prev: "Previous answer", next: "Next answer" },
};

/**
 * BranchSwitcher: the "‹ 2/3 ›" stepper under a message that has more
 * than one version. Switching posts /api/branch/switch, then asks the app
 * to reload its state.
 *
 * Props:
 * - alternatives: this turn's versions ([{branch, turn, text}], from
 *   loom.versions.messages or .replies), or the older loom_state
 *   alternatives object ({turn: versions}).
 * - turn: the user turn number on the current line.
 * - currentBranch: the branch shown now ("main" when absent).
 * - kind: "message" (other wordings, under your message) or "answer"
 *   (the same words asked again, under the reply).
 * - onReload: async callback after a successful switch.
 * - disabled: true while a turn streams or the day is sealed.
 *
 * The shown position is the current branch; when it is not among the
 * versions (an inherited message on a child branch), the version with
 * this turn number is shown instead.
 */
export function BranchSwitcher({ alternatives, turn, currentBranch, kind = "message", onReload, disabled }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const versions = Array.isArray(alternatives) ? alternatives : (alternatives && alternatives[turn]) || [];
  if (!versions || versions.length < 2) return null;
  const words = WORDS[kind] || WORDS.message;
  const here = currentBranch || "main";
  let index = versions.findIndex((v) => v.branch === here);
  if (index < 0) index = versions.findIndex((v) => v.turn === turn);
  if (index < 0) index = 0;
  const go = async (step) => {
    const next = versions[index + step];
    if (!next || next.branch === here || busy || disabled) return;
    cue("press");
    setBusy(true);
    setError(null);
    let r = null;
    try {
      r = await postJSON("/api/branch/switch", { branch: next.branch });
    } catch {
      r = { error: "That version did not open. Try again." };
    }
    setBusy(false);
    if (r && r.error) {
      setError(r.error);
      return;
    }
    if (onReload) await onReload();
  };
  return (
    <>
      <div className="proof-nav vswitch" role="group" aria-label={words.group}>
        <button type="button" onClick={() => go(-1)} disabled={disabled || busy || index === 0} aria-label={words.prev} title={words.prev}>‹</button>
        <span className="num">{index + 1}/{versions.length}</span>
        <button type="button" onClick={() => go(1)} disabled={disabled || busy || index === versions.length - 1} aria-label={words.next} title={words.next}>›</button>
      </div>
      {error && <p className="ferr" role="alert">{error}</p>}
    </>
  );
}
