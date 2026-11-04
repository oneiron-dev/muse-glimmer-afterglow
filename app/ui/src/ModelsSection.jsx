import { useEffect, useState } from "react";
import { getJSON, postJSON } from "./lib.js";
import { cue } from "./sound.js";
import { Switch } from "./Settings.jsx";
import "./models.css";

/** Settings > Models: the memory estimate first, then the Agent slot (children), then speech and vision.
    The exact contract is GET /api/models in app/afterglow/models.py. */

const SLOT_META = {
  speech: {
    title: "Speech",
    line: "Turns what you say into text in the message box. Pick the speech model that fits this computer.",
    none: "Default",
    noneLine: "What you have today. Nothing changes until you pick a model.",
  },
  vision: {
    title: "Vision",
    line: "Lets the circle see pictures, icons and emoji, not just text.",
    none: "None",
    noneLine: "Text only. The circle finds words, not pictures.",
  },
};

const FIT = { fits: "Fits", tight: "Tight", too_big: "Too big for this computer" };
const GLIMMER_VISION = "glimmer-mmproj";

/* ---------------- memory ---------------- */

function MemoryBar({ memory, hwLine }) {
  const segs = memory.segments;
  if (!segs || !segs.length) return null;
  const total = memory.total_gb || segs.reduce((n, s) => n + s.gb, 0) || 1;
  return (
    <section className="fsec" aria-label="Memory on this computer">
      <h2 className="fh">Memory on this computer</h2>
      <p className="fp">An estimate for the models in use, and what is still free.</p>
      <div className="membar" role="img" aria-label={segs.map((s) => `${s.label}, ${s.gb} GB`).join("; ")}>
        {segs.map((s, i) => (
          <i key={s.id} className={s.id === "free" ? "free" : ""}
            style={{ width: `${((s.gb / total) * 100).toFixed(1)}%`, opacity: s.id === "free" ? undefined : Math.max(0.35, 1 - i * 0.18) }} />
        ))}
      </div>
      <ul className="memlegend num">
        {segs.map((s) => <li key={s.id}><b>{s.label}</b> {s.gb} GB</li>)}
      </ul>
      <p className="fhint">{hwLine}.</p>
    </section>
  );
}

/* ---------------- rows ---------------- */

function NoneRow({ slotId, slot, acting, onAct }) {
  const meta = SLOT_META[slotId];
  const selected = slot.selected == null;
  return (
    <div className="frow">
      <div className="frow-text">
        <b>{meta.none}{selected && <span className="mtag">In use</span>}</b>
        <p>{meta.noneLine}</p>
      </div>
      {!selected && (
        <div className="frow-act">
          <button type="button" className="btn btn-send" disabled={acting} onClick={() => onAct("use", { slot: slotId, id: null })}>Use</button>
        </div>
      )}
    </div>
  );
}

function ChoiceRow({ slotId, slot, c, av, acting, onAct }) {
  const st = c.state;
  const selected = slot.selected === c.id;
  const glimmer = c.id === GLIMMER_VISION;
  const remote = glimmer && av.local === false;
  const inUse = st === "in_use";
  const fit = FIT[c.fit] || null;
  const pct = c.progress ? c.progress.pct : null;
  const needsLine = c.needs === "apple-silicon" ? "Needs Apple silicon" : c.needs === "cuda" ? "Needs an NVIDIA GPU" : null;
  const sizes = remote ? "Runs in your agent's server, using no memory on this computer." : [`${c.disk_gb} GB to download`, `${c.memory_gb} GB in memory`, needsLine].filter(Boolean).join(" \u00b7 ");
  const speedLink = /^https?:\/\//.test(c.speed_source || "") ? c.speed_source : null;
  const statusLine = c.line
    ? (st === "failed" ? <p className="ferr" role="alert">{c.line}</p> : <p>{c.line}</p>)
    : null;

  let action = null;
  if (st === "not_downloaded") {
    action = <button type="button" className="btn btn-send" disabled={acting} onClick={() => onAct("download", { id: c.id })}>Download</button>;
  } else if (st === "downloading") {
    action = <button type="button" className="btn btn-quiet" disabled={acting} onClick={() => onAct("pause", { id: c.id })}>Pause</button>;
  } else if (st === "paused") {
    action = <button type="button" className="btn btn-send" disabled={acting} onClick={() => onAct("download", { id: c.id })}>Resume</button>;
  } else if (st === "failed") {
    action = <button type="button" className="btn btn-send" disabled={acting} onClick={() => onAct("download", { id: c.id })}>Retry</button>;
  } else if (st === "downloaded") {
    action = <button type="button" className="btn btn-send" disabled={acting} onClick={() => onAct("use", { slot: slotId, id: c.id })}>Use</button>;
  } else if (inUse) {
    if (remote) {
      if (!selected) action = <button type="button" className="btn btn-send" disabled={acting} onClick={() => onAct("use", { slot: slotId, id: c.id })}>Use</button>;
    } else {
      action = (
        <div className="mkeep">
          <Switch on={!!slot.keep_loaded} label={`Keep ${c.label} loaded`} disabled={acting}
            onChange={(v) => onAct("keep", { slot: slotId, on: v })} />
          <span>Keep loaded</span>
        </div>
      );
    }
  }
  return (
    <div className="frow">
      <div className="frow-text">
        <b>{c.label}
          {inUse && (!remote || selected) && <span className="mtag">In use</span>}
          {st === "downloaded" && <span className="mtag">Downloaded</span>}
          {st === "paused" && <span className="mtag">Paused</span>}
          {c.recommended && <span className="mtag">Recommended for this computer</span>}
          {fit && <span className="mtag">{fit}</span>}
        </b>
        <p>{c.adds}</p>
        {glimmer && <p>{av.line}</p>}
        {!glimmer && statusLine}
        {glimmer && av.local && statusLine}
        <p className="mmeta num">{sizes}</p>
        <p className="mmeta">{c.speed}{speedLink && <> <a href={speedLink} target="_blank" rel="noreferrer">Source</a></>}</p>
        {c.tested === false && <p className="mmeta">Not yet tried on this Mac.</p>}
        {(st === "downloading" || st === "paused") && (
          <div className="mdl" role="status">
            <div className="fit-bar"><i style={{ "--f": (pct || 0) / 100 }} /></div>
            <div className="fit-cap num"><span>{st === "paused" ? "Paused" : `Downloading, ${pct}%`}</span><span /></div>
          </div>
        )}
        {st === "downloaded" && (
          <button type="button" className="link mremove" disabled={acting} onClick={() => onAct("remove", { id: c.id })}>Remove</button>
        )}
      </div>
      {action && <div className="frow-act">{action}</div>}
    </div>
  );
}

/* ---------------- slots ---------------- */

function SlotSection({ slotId, slot, av, circle, acting, onAct }) {
  const meta = SLOT_META[slotId];
  return (
    <section className="fsec" aria-label={meta.title}>
      <h2 className="fh">{meta.title}</h2>
      <p className="fp">{meta.line}</p>
      <NoneRow slotId={slotId} slot={slot} acting={acting} onAct={onAct} />
      {slot.choices.map((c) => <ChoiceRow key={c.id} slotId={slotId} slot={slot} c={c} av={slotId === "vision" ? av : null} acting={acting} onAct={onAct} />)}
      {slotId === "vision" && <p className="fp mcircle"><b>Circle:</b> {circle === "vision" ? "sees pictures, icons and emoji" : "reads text only"}</p>}
    </section>
  );
}

const PATHS = { download: "/api/models/download", pause: "/api/models/pause", remove: "/api/models/remove", use: "/api/models/use", keep: "/api/models/keep-loaded" };

export function ModelsSection({ children }) {
  const [m, setM] = useState(null);
  const [err, setErr] = useState(null);
  const [note, setNote] = useState(null);
  const [acting, setActing] = useState(false);
  const load = async () => {
    try {
      const r = await getJSON("/api/models");
      if (r && !r.error) { setM(r); return r; }
      setErr((r && r.error) || "Your models did not load.");
    } catch {
      setErr("Your models did not load.");
    }
    return null;
  };
  useEffect(() => { load(); }, []);
  const busy = m ? ["speech", "vision"].some((id) => m.slots[id].choices.some((c) => c.state === "downloading")) : false;
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, [busy]);
  const onAct = async (kind, body) => {
    if (kind === "download" || kind === "use") cue("press");
    setActing(true);
    setErr(null);
    setNote(null);
    try {
      const r = await postJSON(PATHS[kind], body);
      if (r && r.error) { setErr(r.error); await load(); }
      else if (r && r.slots) { setM(r); setNote(r.restart_line || r.note || null); }
      else await load();
    } catch {
      setErr("That did not go through. Try again.");
      await load();
    }
    setActing(false);
  };
  if (!m && !err) return (<>{children}<p className="pfine">Reading your models…</p></>);
  if (!m) {
    return (
      <>
        {children}
        <p className="ferr" role="alert">{err}</p>
        <div className="factions"><button type="button" className="btn btn-quiet" onClick={() => { setErr(null); load(); }}>Try again</button></div>
      </>
    );
  }
  return (
    <>
      <MemoryBar memory={m.memory} hwLine={m.hardware.memory_line} />
      {children}
      {note && <p className="fp" role="status">{note}</p>}
      <SlotSection slotId="speech" slot={m.slots.speech} av={m.agent_vision} circle={m.circle} acting={acting} onAct={onAct} />
      <SlotSection slotId="vision" slot={m.slots.vision} av={m.agent_vision} circle={m.circle} acting={acting} onAct={onAct} />
      {err && <p className="ferr" role="alert">{err}</p>}
    </>
  );
}
