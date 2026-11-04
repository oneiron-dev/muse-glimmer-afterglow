import { useState } from "react";
import { postJSON } from "../lib.js";
import { cue } from "../sound.js";
import { factLabel, versionLabel } from "./words.js";

/** One complete morning answer: choose the right facts and any other versions of the day to learn later.
 * A section of the morning card, under its verdict. day targets the report's day. Facts are asked only when the
 * night had no answer for them; a saved answer keeps its facts and added versions on later saves.
 * firstWords names each version by the first thing you said on it (GET /api/loom first_words).
 */
export function MorningOffer({ day, leftOut, extra, conflicts, resolution, firstWords, onResolved }) {
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
      <section className="mprops" aria-label="Left out last night">
        <p className="mprop-state" role="status">Saved. A later night learns what you picked. Last night’s result stays as it was.</p>
      </section>
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
      r = { error: "Your answer did not go through. Try again." };
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
    <section className="mprops moffer" aria-label="Left out last night">
      <h3 className="mprops-head">Left out last night</h3>
      <p className="moffer-why">Last night I trained on the version of day {day} you ended on. Pick what else I should learn on a later night.</p>
      {unanswered && list.map((c) => (
        <div className="moffer-q" key={c.side.id}>
          <p className="pk">{factLabel(c.key)}: which is right?</p>
          <div className="v-list" role="radiogroup" aria-label={factLabel(c.key)}>
            <button type="button" role="radio" aria-checked={choice(c) === "line"} disabled={busy}
              className={`v-row${choice(c) === "line" ? " on" : ""}`}
              onClick={() => setPick((p) => ({ ...p, [c.side.id]: "line" }))}>
              <i className="v-radio" aria-hidden="true" />
              <span><b>{c.line.value}</b><small>What I trained on last night.</small></span>
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
      {remaining.length > 0 && (
        <div className="moffer-q">
          <p className="pk">{remaining.length === 1 ? "Learn your other version too?" : "Learn your other versions too?"}</p>
          <div className="v-list">
            {remaining.map((b) => {
              const facts = factsOf(b);
              return (
                <button key={b} type="button" role="checkbox" aria-checked={!!add[b]} disabled={busy}
                  className={`v-row${add[b] ? " on" : ""}`}
                  onClick={() => setAdd((a) => ({ ...a, [b]: !a[b] }))}>
                  <i className="v-radio v-check" aria-hidden="true" />
                  <span><b>{versionLabel(b, firstWords)}</b><small>{facts.length ? facts.map((n) => `${factLabel(n.key)}: ${n.value}`).join("; ") : "No new facts in it."}</small></span>
                </button>
              );
            })}
          </div>
        </div>
      )}
      <div className="mprop-acts">
        <button type="button" className="btn btn-quiet" disabled={busy} onClick={submit}>{busy ? "Saving…" : "Save"}</button>
        <button type="button" className="btn btn-text" disabled={busy} onClick={skip}>Not now</button>
      </div>
      {error && <p className="ferr" role="alert">{error}</p>}
    </section>
  );
}
