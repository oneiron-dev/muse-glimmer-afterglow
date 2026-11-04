import { useState } from "react";
import { postJSON } from "../lib.js";
import { cue } from "../sound.js";

/**
 * BranchSwitcher: the "‹ 2/3 ›" stepper under a user message that has
 * more than one version. Switching posts /api/branch/switch, then asks
 * the app to reload its state.
 *
 * Props:
 * - alternatives: the loom_state alternatives object ({turn: versions})
 *   or the versions array for this turn ([{branch, turn, text}]).
 * - turn: the user turn number on the current line.
 * - currentBranch: the branch shown now ("main" when absent).
 * - onReload: async callback after a successful switch.
 * - disabled: true while a turn streams or the day is sealed.
 *
 * The shown position is the current branch; when it is not among the
 * versions (an inherited message on a child branch), the version with
 * this turn number is shown instead.
 */
export function BranchSwitcher({ alternatives, turn, currentBranch, onReload, disabled }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const versions = Array.isArray(alternatives) ? alternatives : (alternatives && alternatives[turn]) || [];
  if (!versions || versions.length < 2) return null;
  const here = currentBranch || "main";
  let index = versions.findIndex((v) => v.branch === here);
  if (index < 0) index = versions.findIndex((v) => v.turn === turn);
  if (index < 0) index = 0;
  const go = async (step) => {
    const next = versions[(index + step + versions.length) % versions.length];
    if (!next || next.branch === here || busy || disabled) return;
    cue("press");
    setBusy(true);
    setError(null);
    let r = null;
    try {
      r = await postJSON("/api/branch/switch", { branch: next.branch });
    } catch {
      r = { error: "The switch did not go through." };
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
      <div className="proof-nav" role="group" aria-label="Versions of this message">
        <button type="button" onClick={() => go(-1)} disabled={disabled || busy} aria-label="Older version" title="Older version">‹</button>
        <span className="num">{index + 1}/{versions.length}</span>
        <button type="button" onClick={() => go(1)} disabled={disabled || busy} aria-label="Newer version" title="Newer version">›</button>
      </div>
      {error && <p className="ferr" role="alert">{error}</p>}
    </>
  );
}
