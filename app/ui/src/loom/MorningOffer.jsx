import { useState } from "react";
import { postJSON } from "../lib.js";
import { cue } from "../sound.js";

/**
 * MorningOffer: the side branches left out of the night, offered back in
 * the morning. Save posts POST /api/loom/resolve with the chosen branch
 * names.
 *
 * Props (all from GET /api/loom):
 * - leftOut: the left_out names (present once the night ran). When the
 *   prop is absent, the branch names are read from extra and conflicts
 *   instead.
 * - extra: contradictions.extra ([{key, value, id, branch}]).
 * - conflicts: contradictions.conflicts; each side fact is shown with
 *   its branch, so a branch is never listed with no facts when its
 *   facts came through conflicts.
 * - resolution: the saved answer, if any ({keep_side, add_branches}).
 *   Its keep_side is preserved and its add_branches stay added: resolve
 *   replaces the whole answer, so a save posts the old names plus the
 *   newly chosen ones. Branches already added are not offered again,
 *   but the rest stay on show until they are added.
 * - onResolved: called with the resolve response after a save, and with
 *   null when the user skips (nothing is posted on skip).
 *
 * Renders nothing when no branch was left out.
 */
export function MorningOffer({ leftOut, extra, conflicts, resolution, onResolved }) {
  const rest = extra || [];
  const sides = (conflicts || []).map((c) => ({ key: c.key, value: c.side.value, id: c.side.id, branch: c.side.branch }));
  const names = Array.isArray(leftOut) ? leftOut : [...new Set([...rest, ...sides].map((n) => n.branch))];
  const savedBefore = (resolution && resolution.add_branches) || [];
  const [savedNow, setSavedNow] = useState([]);
  const [add, setAdd] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [skipped, setSkipped] = useState(false);
  const saved = new Set([...savedBefore, ...savedNow]);
  const remaining = names.filter((b) => !saved.has(b));
  if (!names.length || skipped) return null;
  if (!remaining.length) {
    return (
      <article className="mcard">
        <p className="mfrom">This morning</p>
        <p className="mkeep">Saved. Your answer is kept.</p>
      </article>
    );
  }
  const factsOf = (b) => [...rest.filter((n) => n.branch === b), ...sides.filter((n) => n.branch === b)];
  const submit = async () => {
    cue("press");
    setBusy(true);
    setError(null);
    const chosen = remaining.filter((b) => add[b]);
    let r = null;
    try {
      r = await postJSON("/api/loom/resolve", {
        keep_side: (resolution && resolution.keep_side) || [],
        add_branches: [...new Set([...savedBefore, ...savedNow, ...chosen])],
      });
    } catch {
      r = { error: "Your answer did not go through." };
    }
    setBusy(false);
    if (r && r.error) {
      setError(r.error);
      return;
    }
    setSavedNow((s) => [...new Set([...s, ...chosen])]);
    if (onResolved) onResolved(r);
  };
  const skip = () => {
    cue("toggle");
    setSkipped(true);
    if (onResolved) onResolved(null);
  };
  return (
    <article className="mcard">
      <p className="mfrom">This morning</p>
      <h2 className="mtitle">Left out last night</h2>
      <p className="mkeep">These side branches were left out last night: add them?</p>
      <div className="v-list">
        {remaining.map((b) => {
          const facts = factsOf(b);
          return (
            <button key={b} type="button" role="checkbox" aria-checked={!!add[b]} disabled={busy}
              className={`v-row${add[b] ? " on" : ""}`}
              onClick={() => setAdd((a) => ({ ...a, [b]: !a[b] }))}>
              <i className="v-radio" aria-hidden="true" />
              <span><b>Branch {b}</b><small>{facts.length ? facts.map((n) => `${n.key}: ${n.value}`).join("; ") : "No saved facts."}</small></span>
            </button>
          );
        })}
      </div>
      <div className="factions">
        <button type="button" className="btn btn-send" disabled={busy} onClick={submit}>
          {busy ? "Saving…" : "Save your answer"}
        </button>
        <button type="button" className="btn btn-quiet" disabled={busy} onClick={skip}>Not now</button>
      </div>
      {error && <p className="ferr" role="alert">{error}</p>}
    </article>
  );
}
