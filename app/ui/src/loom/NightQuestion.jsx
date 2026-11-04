import { useState } from "react";
import { postJSON } from "../lib.js";
import { cue } from "../sound.js";
import { factLabel } from "./words.js";

/**
 * NightQuestion: the contradiction question before the night. Each fact
 * the other versions of the day disagree on is one choice; facts said only
 * in another version are tick boxes. Save posts POST /api/loom/resolve.
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
 * the line side (or the saved answer), which matches the fallback of
 * keeping one line. Picking a side fact keeps that fact only; it never
 * adds its branch. A saved answer shows as one quiet line with Change.
 */
export function NightQuestion({ conflicts, extra, resolution, onResolved }) {
  const list = conflicts || [];
  const rest = extra || [];
  const saved = resolution?.keep_side || [];
  const [pick, setPick] = useState({});
  const [keep, setKeep] = useState(() => Object.fromEntries(saved.map((id) => [id, true])));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [local, setLocal] = useState(null);
  const [changing, setChanging] = useState(false);
  const done = local || resolution;
  if (!list.length && !rest.length) return null;
  const choice = (c) => pick[c.side.id] || ((done?.keep_side || []).includes(c.side.id) ? "side" : "line");
  if (done && !changing) {
    const chosen = [...list.map((c) => `${factLabel(c.key)}: ${choice(c) === "side" ? c.side.value : c.line.value}`),
      ...rest.filter((n) => (done.keep_side || []).includes(n.id)).map((n) => `${factLabel(n.key)}: ${n.value}`)];
    return (
      <p className="fhint nq-saved" role="status">
        Saved. Tonight I learn {chosen.join("; ")}.{" "}
        <button type="button" className="link" onClick={() => { cue("toggle"); setChanging(true); }}>Change</button>
      </p>
    );
  }
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
      r = { error: "Your answer did not go through. Try again." };
    }
    setBusy(false);
    if (r && r.error) {
      setError(r.error);
      return;
    }
    setLocal(r);
    setChanging(false);
    if (onResolved) onResolved(r);
  };
  return (
    <article className="mcard">
      <h2 className="mtitle">Which is right?</h2>
      <p className="mwhy">{list.length === 1 ? "You told me two different things today." : "You told me different things today."} Tonight I learn the one you pick.</p>
      {list.map((c) => (
        <div className="proof" key={c.side.id}>
          <p className="pk">{factLabel(c.key)}</p>
          <div className="v-list" role="radiogroup" aria-label={factLabel(c.key)}>
            <button type="button" role="radio" aria-checked={choice(c) === "line"} disabled={busy}
              className={`v-row${choice(c) === "line" ? " on" : ""}`}
              onClick={() => setPick((p) => ({ ...p, [c.side.id]: "line" }))}>
              <i className="v-radio" aria-hidden="true" />
              <span><b>{c.line.value}</b><small>In the version of today you are on.</small></span>
            </button>
            <button type="button" role="radio" aria-checked={choice(c) === "side"} disabled={busy}
              className={`v-row${choice(c) === "side" ? " on" : ""}`}
              onClick={() => setPick((p) => ({ ...p, [c.side.id]: "side" }))}>
              <i className="v-radio" aria-hidden="true" />
              <span><b>{c.side.value}</b><small>In your other version.</small></span>
            </button>
          </div>
        </div>
      ))}
      {rest.length > 0 && (
        <div className="proof">
          <p className="pk">Said only in your other version</p>
          <div className="v-list">
            {rest.map((n) => (
              <button key={n.id} type="button" role="checkbox" aria-checked={!!keep[n.id]} disabled={busy}
                className={`v-row${keep[n.id] ? " on" : ""}`}
                onClick={() => setKeep((k) => ({ ...k, [n.id]: !k[n.id] }))}>
                <i className="v-radio" aria-hidden="true" />
                <span><b>{factLabel(n.key)}: {n.value}</b><small>Tick it and I learn it too.</small></span>
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="factions">
        <button type="button" className="btn btn-send" disabled={busy} onClick={submit}>
          {busy ? "Saving…" : "Save for tonight"}
        </button>
        {changing && <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => { cue("toggle"); setChanging(false); setPick({}); }}>Cancel</button>}
        <p className="fhint">No answer by the night: I learn the version you are on, and ask you in the morning.</p>
      </div>
      {error && <p className="ferr" role="alert">{error}</p>}
    </article>
  );
}
