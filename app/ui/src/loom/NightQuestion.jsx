import { useState } from "react";
import { postJSON } from "../lib.js";
import { cue } from "../sound.js";

/**
 * NightQuestion: the contradiction question before the night. Each fact
 * the side branches disagree on is one choice; side facts that disagree
 * with nothing are tick boxes. Save posts POST /api/loom/resolve.
 *
 * Props (all from GET /api/loom):
 * - conflicts: contradictions.conflicts
 *   ([{key, line: {id, value}, side: {id, value, branch}}]).
 * - extra: contradictions.extra ([{key, value, id, branch}]).
 * - resolution: the saved answer, if any ({keep_side, add_branches}).
 *   Its add_branches are preserved: resolve replaces the whole answer,
 *   so the branches already added stay added.
 * - onResolved: called with the resolve response after a save.
 *
 * Renders nothing when there is nothing to ask. Each conflict starts on
 * the line side, which matches the fallback of keeping one line.
 * Picking a side fact keeps that fact only; it never adds its branch.
 */
export function NightQuestion({ conflicts, extra, resolution, onResolved }) {
  const list = conflicts || [];
  const rest = extra || [];
  const [pick, setPick] = useState({});
  const [keep, setKeep] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [local, setLocal] = useState(null);
  const done = local || resolution;
  if (!list.length && !rest.length) return null;
  if (done) {
    return (
      <article className="mcard">
        <p className="mfrom">Before the night</p>
        <p className="mkeep">Saved. Your answer is kept.</p>
      </article>
    );
  }
  const choice = (c) => pick[c.side.id] || "line";
  const submit = async () => {
    cue("press");
    setBusy(true);
    setError(null);
    const keepSide = [];
    for (const c of list) {
      if (choice(c) === "side") keepSide.push(c.side.id);
    }
    for (const n of rest) {
      if (keep[n.id]) keepSide.push(n.id);
    }
    let r = null;
    try {
      r = await postJSON("/api/loom/resolve", { keep_side: keepSide, add_branches: resolution?.add_branches || [] });
    } catch {
      r = { error: "Your answer did not go through." };
    }
    setBusy(false);
    if (r && r.error) {
      setError(r.error);
      return;
    }
    setLocal(r);
    if (onResolved) onResolved(r);
  };
  return (
    <article className="mcard">
      <p className="mfrom">Before the night</p>
      <h2 className="mtitle">Which is right?</h2>
      <p className="mwhy">Your branches disagree on these facts. Pick what is right.</p>
      {list.map((c) => (
        <div className="proof" key={c.side.id}>
          <p className="pk">{c.key}</p>
          <p className="mwhy">Today you said {c.line.value} on one branch and {c.side.value} on another. Which is right?</p>
          <div className="v-list" role="radiogroup" aria-label={c.key}>
            <button type="button" role="radio" aria-checked={choice(c) === "line"} disabled={busy}
              className={`v-row${choice(c) === "line" ? " on" : ""}`}
              onClick={() => setPick((p) => ({ ...p, [c.side.id]: "line" }))}>
              <i className="v-radio" aria-hidden="true" />
              <span><b>{c.line.value}</b><small>On your line today.</small></span>
            </button>
            <button type="button" role="radio" aria-checked={choice(c) === "side"} disabled={busy}
              className={`v-row${choice(c) === "side" ? " on" : ""}`}
              onClick={() => setPick((p) => ({ ...p, [c.side.id]: "side" }))}>
              <i className="v-radio" aria-hidden="true" />
              <span><b>{c.side.value}</b><small>On branch {c.side.branch}.</small></span>
            </button>
          </div>
        </div>
      ))}
      {rest.length > 0 && (
        <div className="proof">
          <p className="pk">Only on side branches</p>
          <div className="v-list">
            {rest.map((n) => (
              <button key={n.id} type="button" role="checkbox" aria-checked={!!keep[n.id]} disabled={busy}
                className={`v-row${keep[n.id] ? " on" : ""}`}
                onClick={() => setKeep((k) => ({ ...k, [n.id]: !k[n.id] }))}>
                <i className="v-radio" aria-hidden="true" />
                <span><b>{n.key}: {n.value}</b><small>On branch {n.branch}.</small></span>
              </button>
            ))}
          </div>
          <p className="fhint">Choose what to keep.</p>
        </div>
      )}
      <div className="factions">
        <button type="button" className="btn btn-send" disabled={busy} onClick={submit}>
          {busy ? "Saving…" : "Save for the night"}
        </button>
      </div>
      {error && <p className="ferr" role="alert">{error}</p>}
    </article>
  );
}
