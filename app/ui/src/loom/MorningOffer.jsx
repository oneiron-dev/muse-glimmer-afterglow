import { useState } from "react";
import { postJSON } from "../lib.js";
import { cue } from "../sound.js";

/** One complete morning answer: choose the right facts and any branches to add later.
 * day targets the report's day. Existing facts and added branches stay selected on later saves.
 * A saved resolution answers every shown conflict; choosing the line keeps its value.
 */
export function MorningOffer({ day, leftOut, extra, conflicts, resolution, onResolved }) {
  const list = conflicts || [];
  const rest = extra || [];
  const sides = list.map((c) => ({ key: c.key, value: c.side.value, id: c.side.id, branch: c.side.branch }));
  const [local, setLocal] = useState(null);
  const [pick, setPick] = useState({});
  const [add, setAdd] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [skipped, setSkipped] = useState(false);
  const answer = local || resolution;
  const saved = answer?.add_branches || [];
  const kept = answer?.keep_side || [];
  const unanswered = !answer && list.length > 0;
  const names = [...new Set([...(Array.isArray(leftOut) ? leftOut : [...rest, ...sides].map((n) => n.branch)),
    ...(unanswered ? sides.map((n) => n.branch) : [])])];
  const remaining = names.filter((b) => !saved.includes(b));
  if (skipped) return null;
  if (!remaining.length && !unanswered) {
    if (!local) return null;
    return (
      <article className="mcard">
        <p className="mfrom">This morning</p>
        <p className="mkeep">Saved for a later night. Last night’s result has not changed.</p>
      </article>
    );
  }
  const choice = (c) => pick[c.side.id] || (kept.includes(c.side.id) ? "side" : "line");
  const factsOf = (b) => [...rest.filter((n) => n.branch === b), ...sides.filter((n) => n.branch === b)];
  const submit = async () => {
    if (busy) return;
    cue("press");
    setBusy(true);
    setError(null);
    const conflictIds = new Set(list.map((c) => c.side.id));
    const keepSide = [...kept.filter((id) => !conflictIds.has(id)),
      ...list.filter((c) => choice(c) === "side").map((c) => c.side.id)];
    const chosen = remaining.filter((b) => add[b]);
    let r = null;
    try {
      r = await postJSON("/api/loom/resolve", {
        ...(day != null ? { day } : {}),
        keep_side: [...new Set(keepSide)],
        add_branches: [...new Set([...saved, ...chosen])],
      });
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
  const skip = () => {
    cue("toggle");
    setSkipped(true);
    if (onResolved) onResolved(null);
  };
  return (
    <article className="mcard">
      <p className="mfrom">This morning</p>
      <h2 className="mtitle">Left out last night</h2>
      <p className="mkeep">Choose which facts are right and any side branches to add for a later night. Last night’s result stays as it was.</p>
      {list.map((c) => (
        <div className="proof" key={c.side.id}>
          <p className="pk">{c.key}: which is right?</p>
          <div className="v-list" role="radiogroup" aria-label={c.key}>
            <button type="button" role="radio" aria-checked={choice(c) === "line"} disabled={busy}
              className={`v-row${choice(c) === "line" ? " on" : ""}`}
              onClick={() => setPick((p) => ({ ...p, [c.side.id]: "line" }))}>
              <i className="v-radio" aria-hidden="true" />
              <span><b>{c.line.value}</b><small>On the day’s line.</small></span>
            </button>
            <button type="button" role="radio" aria-checked={choice(c) === "side"} disabled={busy}
              className={`v-row${choice(c) === "side" ? " on" : ""}`}
              onClick={() => setPick((p) => ({ ...p, [c.side.id]: "side" }))}>
              <i className="v-radio" aria-hidden="true" />
              <span><b>{c.side.value}</b><small>Branch {c.side.branch}.</small></span>
            </button>
          </div>
        </div>
      ))}
      {remaining.length > 0 && <>
        <p className="pk">Add side branches</p>
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
      </>}
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
