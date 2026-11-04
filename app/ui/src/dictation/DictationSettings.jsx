import { useEffect, useState } from "react";
import { getJSON, postJSON } from "../lib.js";
import { cue } from "../sound.js";
import { useDictation } from "./useDictation.js";
import "./dictation.css";

/** Settings > Dictation: the engine, a record button to try it, and the clean-up rules. */
export function DictationSettings() {
  const [s, setS] = useState(null);
  const [engines, setEngines] = useState(null);
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    getJSON("/api/dictation/settings")
      .then((r) => {
        if (r && !r.error) {
          setS({ engine: r.engine || "", model: r.model || "", rules: r.rules || "", server_url: r.server_url || "" });
          if (Array.isArray(r.engines)) setEngines(r.engines);
        } else {
          setErr((r && r.error) || "Dictation settings did not load.");
        }
      })
      .catch(() => setErr("Dictation settings did not load."));
  }, []);
  const save = async () => {
    if (!s) return;
    setSaving(true);
    setMsg(null);
    setErr(null);
    const r = await postJSON("/api/dictation/settings", s);
    setSaving(false);
    if (r && !r.error) {
      cue("press");
      setMsg("Saved.");
      if (r.engine != null || r.model != null || r.rules != null || r.server_url != null) {
        setS({ engine: r.engine ?? s.engine, model: r.model ?? s.model, rules: r.rules ?? s.rules, server_url: r.server_url ?? s.server_url });
      }
    } else {
      setErr((r && r.error) || "Saving did not go through.");
    }
  };
  if (err && !s) return <p className="ferr" role="alert">{err}</p>;
  if (!s) return <p className="pfine">Loading…</p>;
  const set = (k) => (e) => { setS({ ...s, [k]: e.target.value }); setMsg(null); };
  const engList = engines
    ? engines.map((e) => (typeof e === "string"
      ? { id: e, label: e, available: true, reason: "" }
      : { id: e.id, label: e.label || e.id, available: e.available !== false, reason: e.reason || "" }))
    : null;
  if (engList && !engList.some((e) => e.id === s.engine)) engList.unshift({ id: s.engine, label: s.engine || "Default", available: true, reason: "" });
  const engReason = engList && (engList.find((e) => e.id === s.engine) || {}).reason;
  return (
    <>
      <p className="fp">Talk to write. In the message box, click the microphone or hold Right Option. Your agent cleans up the speech, with your rules.</p>
      <section className="fsec" aria-label="Try it">
        <h2 className="fh">Try it</h2>
        <TryIt />
      </section>
      <section className="fsec" aria-label="Engine">
        <h2 className="fh">Engine</h2>
        <div className="ffield">
          <label htmlFor="dic-engine">Speech engine</label>
          {engList ? (
            <select id="dic-engine" className="finput" value={s.engine} onChange={set("engine")}>
              {engList.map((e) => <option key={e.id} value={e.id} disabled={!e.available}>{e.label}{e.available ? "" : " (not available)"}</option>)}
            </select>
          ) : (
            <input id="dic-engine" className="finput" value={s.engine} onChange={set("engine")} placeholder="whisper.cpp" spellCheck="false" autoComplete="off" />
          )}
          {engReason && <p className="fhint">{engReason}</p>}
        </div>
        <div className="ffield">
          <label htmlFor="dic-model">Model file</label>
          <p className="fhint">The speech model on this computer. Blank means the default.</p>
          <input id="dic-model" className="finput num" value={s.model} onChange={set("model")} placeholder="/path/to/model.bin" spellCheck="false" autoComplete="off" />
        </div>
        <div className="ffield">
          <label htmlFor="dic-server">Server address</label>
          <p className="fhint">A running speech server, if you have one. Blank means run on this computer.</p>
          <input id="dic-server" className="finput num" value={s.server_url} onChange={set("server_url")} placeholder="http://127.0.0.1:8080" spellCheck="false" autoComplete="off" />
        </div>
      </section>
      <section className="fsec" aria-label="Rules">
        <h2 className="fh">Rules</h2>
        <div className="ffield">
          <label htmlFor="dic-rules">How your agent cleans up speech</label>
          <p className="fhint">One rule per line. They apply after the built-in clean-up: punctuation, dropped fillers, spoken corrections.</p>
          <textarea id="dic-rules" className="finput dic-rules" value={s.rules} onChange={set("rules")} rows={5} spellCheck="false"
            placeholder={"Spell Mira with one r\nWrite Larkspur with a capital L"} />
        </div>
        <div className="factions">
          <button type="button" className="btn btn-send" disabled={saving} onClick={save}>Save</button>
          {msg && <span className="fhint" role="status">{msg}</span>}
          {err && <span className="ferr" role="alert">{err}</span>}
        </div>
      </section>
    </>
  );
}

/** A record button that shows what the engine heard and the cleaned text. */
function TryIt() {
  const dic = useDictation({ context: "composer" });
  const ms = dic.last && dic.last.ms;
  const meta = dic.last ? [
    dic.last.engine,
    ms && typeof ms.asr === "number" ? `heard in ${(ms.asr / 1000).toFixed(1)}s` : null,
    ms && typeof ms.clean === "number" ? `cleaned in ${(ms.clean / 1000).toFixed(1)}s` : null,
  ].filter(Boolean).join(" \u00b7 ") : "";
  return (
    <div className="dic-try">
      <div className="dic-try-row">
        <button type="button" className={`btn ${dic.recording ? "btn-send" : "btn-quiet"}`} disabled={dic.sending} onClick={dic.toggle}>
          {dic.sending ? "Writing it down…" : dic.recording ? "Stop" : dic.last ? "Try again" : "Try it"}
        </button>
        {dic.recording && <span className="dic-level wide" aria-hidden="true"><i style={{ "--l": dic.level.toFixed(2) }} /></span>}
        {dic.recording && <span className="fhint">Listening…</span>}
      </div>
      {dic.error && <p className="ferr" role="alert">{dic.error}</p>}
      {dic.last && (dic.last.raw || dic.last.clean) && (
        <dl className="kv dic-result">
          <div><dt>Heard</dt><dd>{dic.last.raw || "(nothing)"}</dd></div>
          <div><dt>Cleaned</dt><dd>{dic.last.clean || "(nothing)"}</dd></div>
        </dl>
      )}
      {meta && <p className="fhint num" role="status">{meta}</p>}
    </div>
  );
}
