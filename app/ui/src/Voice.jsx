import { useEffect, useRef, useState } from "react";
import { getJSON, postJSON } from "./lib.js";

/** Why a voice is not offered, in plain words, from its measured check (steering/voices.json). */
export function voiceWhy(check) {
  if (!check) return "";
  const out = [];
  const idk = check.idk || {}, known = check.known || {};
  if (check.honest === false && idk.abstain != null) out.push(`says “I don’t know” ${idk.abstain < idk.abstain_scale0 ? "less" : "more"} often (${idk.abstain} against ${idk.abstain_scale0} of 200), so it guesses ${idk.abstain < idk.abstain_scale0 ? "more" : "less"}`);
  if (check.keeps_known === false && known.right != null) out.push(`gets fewer of your known facts right (${known.right}; the floor is ${known.floor})`);
  if (check.coherent === false) out.push("its replies stop making sense");
  if (!out.length && check.band === false) out.push("moves too far from the measured range");
  return out.join("; ");
}
const effect = (v) => (v.check && v.check.words != null && v.check.words_scale0 != null && v.slug !== "default" ? `Replies ${Math.round(v.check.words_scale0)} → ${Math.round(v.check.words)} words` : "");

/** The voice list: the ready presets to pick, the rest with why they are held back, and a voice described in words. */
export function VoiceList({ voices, onChange }) {
  const [busy, setBusy] = useState(null);
  const [text, setText] = useState("");
  const [asked, setAsked] = useState(null);
  if (!voices) return <p className="pfine">Loading voices…</p>;
  const cur = voices.choice && voices.choice.preset ? voices.choice.preset : voices.choice && voices.choice.voice ? voices.choice.voice : "default";
  const pick = async (body, slug) => {
    setBusy(slug);
    const r = await postJSON("/api/voice", body);
    setBusy(null);
    if (!r.error) onChange(r);
  };
  const describe = async (e) => {
    e.preventDefault();
    if (!text.trim()) return;
    const r = await postJSON("/api/voice/describe", { text });
    setAsked(r);
    if (!r.error) setText("");
  };
  const ready = voices.presets.filter((p) => p.status === "ready");
  const held = voices.presets.filter((p) => p.status !== "ready");
  return (
    <div className="voices">
      <div className="v-list" role="radiogroup" aria-label="Voice">
        {ready.map((p) => (
          <button key={p.slug} type="button" role="radio" aria-checked={cur === p.slug} className={`v-row${cur === p.slug ? " on" : ""}`} disabled={busy === p.slug}
            onClick={() => cur !== p.slug && pick({ preset: p.slug }, p.slug)}>
            <i className="v-radio" aria-hidden="true" />
            <span><b>{p.label}</b><small>{p.line}{effect(p) ? ` ${effect(p)}.` : ""}</small></span>
          </button>
        ))}
        {(voices.custom || []).filter((c) => c.status === "ready").map((c) => (
          <button key={c.slug} type="button" role="radio" aria-checked={cur === c.slug} className={`v-row${cur === c.slug ? " on" : ""}`} onClick={() => pick({ voice: c.slug }, c.slug)}>
            <i className="v-radio" aria-hidden="true" /><span><b>{c.label}</b><small>{c.description}</small></span>
          </button>
        ))}
      </div>
      {(held.length > 0 || (voices.custom || []).some((c) => c.status !== "ready")) && (
        <details className="v-held">
          <summary>Held back: {[...held.map((p) => p.label), ...(voices.custom || []).filter((c) => c.status !== "ready").map((c) => c.label)].join(", ")}</summary>
          <p className="pfine">Each voice is tested for honesty before it is offered. These failed, so you cannot pick them yet.</p>
          <ul>
            {held.map((p) => <li key={p.slug}><b>{p.label}</b> {voiceWhy(p.check) || "failed its check"}.</li>)}
            {(voices.custom || []).filter((c) => c.status !== "ready").map((c) => <li key={c.slug}><b>{c.label}</b> {c.why || "still being tested"}.</li>)}
          </ul>
        </details>
      )}
      <form className="v-describe" onSubmit={describe}>
        <label className="sr" htmlFor="v-desc">Describe a voice</label>
        <input id="v-desc" value={text} onChange={(e) => setText(e.target.value)} placeholder="Describe a voice in one sentence" />
        <button type="submit" className="btn btn-quiet" disabled={!text.trim()}>Learn it tonight</button>
      </form>
      {asked && <p className="pfine" role="status">{asked.error || (asked.status === "queued" ? "Queued for tonight. It is tested for honesty before it is offered." : asked.why || "Not queued.")}</p>}
    </div>
  );
}

/** The voice pill in the message box, opening the list above it. */
export function VoicePill({ voices, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    const close = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const esc = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  if (!voices) return null;
  const all = [...voices.presets, ...(voices.custom || [])];
  const slug = voices.choice ? voices.choice.preset || voices.choice.voice : "default";
  const label = (all.find((v) => v.slug === slug) || { label: "Default" }).label;
  return (
    <div className="vwrap" ref={ref}>
      <button type="button" className="vpill" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(!open)}>Voice: {label}</button>
      {open && <div className="vsheet" role="dialog" aria-label="Voice"><p className="vs-head">Voice <span>from your next message</span></p><VoiceList voices={voices} onChange={(r) => { onChange(r); setOpen(false); }} /></div>}
    </div>
  );
}

export function useVoices() {
  const [voices, setVoices] = useState(null);
  useEffect(() => { getJSON("/api/voices").then((v) => setVoices(v && v.presets ? v : null)).catch(() => setVoices(null)); }, []);
  return [voices, setVoices];
}
