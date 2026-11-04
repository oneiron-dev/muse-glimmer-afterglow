import { useEffect, useRef, useState } from "react";
import { postJSON } from "./lib.js";

/** Add a profile: two short steps, then straight into the first chat. Skippable; the first chat is the real product. */
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
        <p className="ob-step num">Step {step} of 2</p>
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
              <button type="button" className="btn btn-text" onClick={() => setStep(1)}>Back</button>
              <button ref={first} type="button" className="btn btn-send" onClick={create} disabled={busy}>{busy ? "Making the profile…" : "Start the first day"}</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
