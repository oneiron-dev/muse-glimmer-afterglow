import { useEffect, useRef, useState } from "react";
import { getJSON, postJSON } from "./lib.js";
import { cue } from "./sound.js";

/** Add a profile: three short steps (who, the model, how it learns), then straight into the first chat. */
export function Onboarding({ onClose, onDone }) {
  const [step, setStep] = useState(1);
  const [name, setName] = useState("");
  const [about, setAbout] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const first = useRef(null);
  const box = useRef(null);
  useEffect(() => { first.current?.focus(); }, [step]);
  useEffect(() => {
    const esc = (e) => { if (e.key === "Escape") onClose(); };
    const trap = (e) => {
      if (e.key !== "Tab" || !box.current) return;
      const f = [...box.current.querySelectorAll("button, input, textarea")].filter((x) => !x.disabled);
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    };
    document.addEventListener("keydown", esc);
    document.addEventListener("keydown", trap);
    return () => { document.removeEventListener("keydown", esc); document.removeEventListener("keydown", trap); };
  }, [onClose]);

  const create = async () => {
    setBusy(true); setError(null);
    const r = await postJSON("/api/profile", { name, about });
    setBusy(false);
    if (r.error) { setError(r.error); return; }
    localStorage.setItem("afterglow-first-chat", r.world);
    onDone(r.world);
  };

  return (
    <div className="scrim" role="presentation" onPointerDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="sheet onboard" role="dialog" aria-modal="true" aria-labelledby="ob-title" ref={box}>
        <p className="ob-step num">Step {step} of 3</p>
        {step === 1 ? (
          <form onSubmit={(e) => { e.preventDefault(); if (name.trim()) setStep(2); }}>
            <h2 id="ob-title">Who is this agent for?</h2>
            <p className="ob-lede">Each profile has its own agent, notes and nights. Nothing is shared between profiles.</p>
            <label className="field"><span>Name</span>
              <input ref={first} value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" placeholder="Mira Holt" required />
            </label>
            <label className="field"><span>Your work, in one line <small>so its first replies fit you; optional</small></span>
              <input value={about} onChange={(e) => setAbout(e.target.value)} placeholder="I run a small bakery in Marlow Bay" />
            </label>
            <div className="ob-acts">
              <button type="button" className="btn btn-text" onClick={onClose}>Cancel</button>
              <button type="submit" className="btn btn-send" disabled={!name.trim()}>Continue</button>
            </div>
          </form>
        ) : step === 2 ? (
          <ModelStep first={first} onBack={() => setStep(1)} onNext={() => setStep(3)} />
        ) : (
          <div>
            <h2 id="ob-title">How your agent learns</h2>
            <ol className="ob-loop">
              <li><b>By day</b><span>You talk. It writes down what you tell it: people, projects, places, dates.</span></li>
              <li><b>At night</b><span>It trains a small update on your day, on a GPU, while you sleep.</span></li>
              <li><b>In the morning</b><span>It keeps the update only if it learned without getting worse or making things up. Otherwise it rolls back and says why.</span></li>
            </ol>
            {error && <p className="error-line" role="alert">{error}</p>}
            <div className="ob-acts">
              <button type="button" className="btn btn-text" onClick={() => setStep(2)}>Back</button>
              <button ref={first} type="button" className="btn btn-send" onClick={create} disabled={busy}>{busy ? "Making the profile…" : "Start the first day"}</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** Onboarding step 2: what this computer can run, and the model the agent will use (any server can be set here;
 a model file or the download live in Settings, Model). */
function ModelStep({ first, onBack, onNext }) {
  const [m, setM] = useState(null);
  const [pr, setPr] = useState(null);
  const [url, setUrl] = useState("");
  const [asked, setAsked] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    getJSON("/api/model").then((r) => { setM(r); return postJSON("/api/model/probe", { url: r.current.url }); }).then(setPr).catch(() => setErr("Afterglow could not read this computer."));
  }, []);
  const check = async (e) => {
    e.preventDefault();
    setErr(null); setAsked(null);
    const r = await postJSON("/api/model/probe", { url });
    if (r.error) setErr(r.error); else setAsked(r);
  };
  const use = async () => {
    cue("press");
    const r = await postJSON("/api/model/use", { source: "server", url: asked.url });
    if (r.error) { setErr(r.error); return; }
    setM(r); setPr(r.probe); setAsked(null); setUrl("");
  };
  const hw = m && m.hardware;
  const ready = pr && pr.chat;
  return (
    <div>
      <h2 id="ob-title">Your agent’s model</h2>
      <p className="ob-lede">Your agent runs on Muse Glimmer 30B, on this computer or on a server you choose.</p>
      {!m ? <p className="pfine">Reading this computer…</p> : (
        <>
          <ul className="flines">
            <li><i className={`fmark ${hw.serve ? "ok" : "no"}`} aria-hidden="true" />{hw.memory_line}. {hw.serve_line}</li>
            {ready
              ? <li><i className="fmark ok" aria-hidden="true" />Ready: {pr.glimmer ? "Muse Glimmer 30B" : pr.model} on {pr.label}. {pr.nights ? "Nightly updates reach your agent." : "Nightly updates do not reach your agent on this server."}</li>
              : <li><i className="fmark no" aria-hidden="true" />No model answers yet. Give a server address, or set up a model file in Settings, Model.</li>}
          </ul>
          <form className="ffield" onSubmit={check}>
            <label htmlFor="ob-url">{ready ? "Use another server (optional)" : "Server address"}</label>
            <div className="finline">
              <input id="ob-url" className="finput num" value={url} onChange={(e) => { setUrl(e.target.value); setAsked(null); }} placeholder="http://127.0.0.1:1234" spellCheck="false" />
              <button type="submit" className="btn btn-quiet" disabled={!url.trim()}>Check</button>
            </div>
          </form>
          {asked && <ul className="flines">{asked.lines.map((l, i) => <li key={i}><i className={`fmark ${/cannot|do not|No server|not serve/i.test(l) ? "no" : i ? "ok" : "info"}`} aria-hidden="true" />{l}</li>)}</ul>}
          {asked && asked.chat && <button type="button" className="btn btn-quiet" onClick={use}>Use this server</button>}
          {err && <p className="error-line" role="alert">{err}</p>}
        </>
      )}
      <div className="ob-acts">
        <button type="button" className="btn btn-text" onClick={onBack}>Back</button>
        <button ref={first} type="button" className="btn btn-send" onClick={onNext} disabled={!ready}>Continue</button>
      </div>
    </div>
  );
}
