import { useState } from "react";
import { postJSON } from "../lib.js";
import { cue } from "../sound.js";
import { factLabel, versionLabel } from "./words.js";

/**
 * NightQuestion: the question before the night when two versions of today
 * disagree on a fact. The night trains one version of the day (the line,
 * night/loom_line.py), so the choice is a version: the one you are on, or
 * an other version with the facts it said. Saving another version moves
 * today to it (POST /api/branch/switch), then saves the answer
 * (POST /api/loom/resolve); tonight's training uses that version.
 *
 * Props (from GET /api/loom):
 * - conflicts: contradictions.conflicts
 *   ([{key, line: {id, value}, side: {id, value, branch}}]).
 * - resolution: the saved answer for this version, if any.
 * - line: the branch you are on (loom.line).
 * - versionWords: {branch: what you said on it that this version did not} (loom.version_words).
 * - onResolved: async, called after a save to reload the state and loom.
 *
 * Renders nothing when there is nothing to ask. A saved answer shows as
 * one quiet line with Change.
 */
export function NightQuestion({ conflicts, resolution, line, versionWords, onResolved }) {
  const list = conflicts || [];
  const here = line || "main";
  const [pick, setPick] = useState(here);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [changing, setChanging] = useState(false);
  if (!list.length) return null;
  if (resolution && !changing) {
    return (
      <p className="fhint nq-saved" role="status">
        Saved. Tonight’s training uses the version you picked.{" "}
        <button type="button" className="link" onClick={() => { cue("toggle"); setPick(here); setChanging(true); }}>Change</button>
      </p>
    );
  }
  const others = [...new Set(list.map((c) => c.side.branch))];
  const options = [
    { branch: here, title: "The version of today you are on",
      facts: [...new Map(list.map((c) => [c.key, `${factLabel(c.key)}: ${c.line.value}`])).values()] },
    ...others.map((b) => ({ branch: b, title: versionLabel(b, versionWords),
      facts: list.filter((c) => c.side.branch === b).map((c) => `${factLabel(c.key)}: ${c.side.value}`) })),
  ];
  const submit = async () => {
    if (busy) return;
    cue("press");
    setBusy(true);
    setError(null);
    let r = null;
    try {
      if (pick !== here) {
        r = await postJSON("/api/branch/switch", { branch: pick });
      }
      if (!r || !r.error) r = await postJSON("/api/loom/resolve", { keep_side: [], add_branches: [] });
    } catch {
      r = { error: "Your answer did not go through. Try again." };
    }
    setBusy(false);
    if (r && r.error) {
      setError(r.error);
      return;
    }
    setChanging(false);
    if (onResolved) await onResolved(r);
  };
  return (
    <article className="mcard">
      <h2 className="mtitle">Which is right?</h2>
      <p className="mwhy">{list.length === 1 ? "You told me two different things today." : "You told me different things today."} Tonight’s training uses the version you pick.</p>
      <div className="v-list" role="radiogroup" aria-label="Which version is right">
        {options.map((o) => (
          <button key={o.branch} type="button" role="radio" aria-checked={pick === o.branch} disabled={busy}
            className={`v-row${pick === o.branch ? " on" : ""}`} onClick={() => setPick(o.branch)}>
            <i className="v-radio" aria-hidden="true" />
            <span><b>{o.title}</b><small>{o.facts.join("; ")}</small></span>
          </button>
        ))}
      </div>
      <div className="factions">
        <button type="button" className="btn btn-send" disabled={busy} onClick={submit}>
          {busy ? "Saving…" : "Save for tonight"}
        </button>
        {changing && <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => { cue("toggle"); setChanging(false); }}>Cancel</button>}
        <p className="fhint">{pick !== here ? "Saving moves today to that version." : "No answer by the night: tonight’s training uses the version you are on, and I ask you in the morning."}</p>
      </div>
      {error && <p className="ferr" role="alert">{error}</p>}
    </article>
  );
}
