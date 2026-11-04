import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { CalendarView, EmailView, Empty, NightView, TasksView } from "./Panel.jsx";
import { Icon } from "./Sidebar.jsx";
import { DaysView } from "./DaysView.jsx";
import { factRows, failedWhy, getJSON, postJSON, pretty, signed } from "./lib.js";

const TITLES = { memory: "Memory", nights: "Nights", calendar: "Calendar", mail: "Mail", tasks: "Tasks", files: "Files", settings: "Settings" };

/** A full screen from the sidebar. */
export function Screen({ id, ctx }) {
  return (
    <main className="main screen" aria-label={TITLES[id]}>
      <header className="bar"><div className="title"><h1>{TITLES[id]}</h1>{id === "memory" && <MemorySeg />}</div></header>
      <div className="screen-body">
        {id === "memory" && <MemoryTabs ctx={ctx} />}
        {id === "nights" && <NightsScreen ctx={ctx} />}
        {id === "calendar" && <div className="col"><CalendarView ws={ctx.ws} today={ctx.state.date} world={ctx.state.world} day={ctx.state.day} /></div>}
        {id === "mail" && <div className="col"><EmailView ws={ctx.ws} world={ctx.state.world} day={ctx.state.day} /></div>}
        {id === "tasks" && <div className="col"><TasksView ws={ctx.ws} /></div>}
        {id === "files" && <FilesScreen ctx={ctx} />}
      </div>
    </main>
  );
}

/** Memory's two views, in the screen's top bar: what your agent keeps (Notes) and the days you spent with it (Days). */
function MemorySeg() {
  const { section } = useSearch({ strict: false });
  const navigate = useNavigate();
  const tab = section === "days" ? "days" : "notes";
  return (
    <div className="seg bar-seg" role="tablist" aria-label="Memory">
      {[["notes", "Notes"], ["days", "Days"]].map(([id, label]) => (
        <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? "on" : ""}
          onClick={() => navigate({ to: "/memory", search: id === "days" ? { section: "days" } : {}, replace: true })}>{label}</button>
      ))}
    </div>
  );
}

function MemoryTabs({ ctx }) {
  const { section } = useSearch({ strict: false });
  return section === "days" ? <div className="col days-col"><DaysView ctx={ctx} /></div> : <MemoryScreen ctx={ctx} />;
}

/** When a kept night first trained a note from day d: notes are cumulative, so the first kept night on or after d. */
function trainedIn(nights, day) {
  const kept = nights.filter((n) => n.decision === "keep" && n.day >= (day || 1)).map((n) => n.day).sort((a, b) => a - b);
  return kept.length ? kept[0] : null;
}

/** Memory: the notes it keeps outside the model (editable, with when the model learned each) and the facts in its weights. */
function MemoryScreen({ ctx }) {
  const { state, report, nights } = ctx;
  const [notes, setNotes] = useState(state.notes || []);
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState("");
  const [msg, setMsg] = useState(null);
  useEffect(() => setNotes(state.notes || []), [state.notes]);
  const byId = useMemo(() => Object.fromEntries(notes.map((n) => [n.id, n])), [notes]);
  const active = notes.filter((n) => n.status === "active").reverse();
  const removed = notes.filter((n) => n.status === "forgotten").reverse();
  const shown = q ? active.filter((n) => `${n.key} ${n.value}`.toLowerCase().includes(q.toLowerCase())) : active;
  const facts = factRows(report && report.eval);
  const tested = Object.fromEntries(facts.map((f) => [f.key.toLowerCase(), f]));
  const known = facts.filter((f) => f.b >= 2).length;

  const refresh = async () => { const s = await getJSON("/api/state"); setNotes(s.notes || []); };
  const save = async (n) => {
    const r = await postJSON("/api/notes/edit", { id: n.id, value: draft });
    if (r.error) { setMsg(r.error); return; }
    setEditing(null); setMsg(null); await refresh();
  };
  const forget = async (n) => {
    const r = await postJSON("/api/notes/forget", { id: n.id });
    if (r.error) { setMsg(r.error); return; }
    const t = trainedIn(nights, n.day);
    setMsg(t ? `Removed “${pretty(n.key)}” from your notes. The model learned it in night ${t}; tonight’s night trains without it.` : `Removed “${pretty(n.key)}” from your notes.`);
    await refresh();
  };

  return (
    <div className="mem">
      <section className="mem-notes">
        <div className="mem-head">
          <h2>Notes <span className="num">{active.length}</span></h2>
          <p className="pfine">What your agent wrote down. They sit outside the model; each night trains on them. Change or remove any note.</p>
          <label className="sr" htmlFor="mem-search">Search notes</label>
          <input id="mem-search" className="search" type="search" placeholder="Search notes" value={q} onChange={(e) => setQ(e.target.value)} />
          {msg && <p className="mem-msg" role="status">{msg}</p>}
        </div>
        <ul className="notes big">
          {shown.map((n) => {
            const prev = n.supersedes && byId[n.supersedes];
            const tIn = trainedIn(nights, n.day);
            const prevIn = prev && trainedIn(nights, prev.day);
            const changedAfter = n.edited_by_user || (prev && prevIn && (n.day || 0) > prevIn);
            const t = tested[n.key.toLowerCase()];
            return (
              <li key={n.id}>
                <span className="nk">{pretty(n.key)}</span>
                {editing === n.id ? (
                  <form className="nedit" onSubmit={(e) => { e.preventDefault(); save(n); }}>
                    <label className="sr" htmlFor={`ne-${n.id}`}>New value</label>
                    <input id={`ne-${n.id}`} value={draft} onChange={(e) => setDraft(e.target.value)} autoFocus />
                    <button type="submit" className="btn btn-send">Save</button>
                    <button type="button" className="btn btn-text" onClick={() => setEditing(null)}>Cancel</button>
                  </form>
                ) : <span className="nv">{n.value}</span>}
                {prev && <span className="nwas">was {prev.value}</span>}
                <span className="nwhen">
                  {changedAfter && prevIn
                    ? <>Changed on day {n.day}, after night {prevIn}. The model still holds “{prev.value}” until a night trains this.</>
                    : tIn ? <>Said on day {n.day || "?"}. <b>Trained in night {tIn}</b>{t ? `, answers it without notes ${t.b} of 3 times` : ""}.</>
                      : <>Said on day {n.day || "?"}. Not trained yet: tonight’s night trains it.</>}
                </span>
                {editing !== n.id && (
                  <span className="nacts">
                    <button type="button" className="act" onClick={() => { setEditing(n.id); setDraft(n.value); }}>Change</button>
                    <button type="button" className="act" onClick={() => forget(n)}>Remove</button>
                  </span>
                )}
              </li>
            );
          })}
        </ul>
        {removed.length > 0 && (
          <details className="removed"><summary>Removed by you <span className="num">{removed.length}</span></summary>
            <ul className="notes">{removed.map((n) => <li key={n.id}><span className="nk">{pretty(n.key)}</span><span className="nv">{n.value}</span><span className="nwhen">Removed on day {n.forgotten_day}.</span></li>)}</ul>
          </details>
        )}
      </section>
      <section className="mem-facts">
        <h2>In its weights <span className="num">{known} of {facts.length}</span></h2>
        <p className="pfine">Tested after night {report && report.day}, with your notes closed: each fact asked three ways. A filled dot answers at least 2 of 3.</p>
        <ul className="facts">
          {facts.map((r) => (
            <li key={r.id} className={r.b >= 2 ? "k" : r.b === 1 ? "h" : ""}>
              <i className="dot" aria-hidden="true" />
              <span className="nk">{r.key}</span><span className="nv">{r.value}</span>
              <span className="num fscore" title="Ways answered with notes closed, before and after the night">{r.a}/3 → {r.b}/3</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

/** Nights: the learning loop. Every night, kept or rolled back and why; one night in full. */
function NightsScreen({ ctx }) {
  const { state, report, nights } = ctx;
  const [day, setDay] = useState(report && report.day);
  const kept = nights.filter((n) => n.decision === "keep").length;
  const tried = nights.filter((n) => n.decision).length;
  const served = report && report.served;
  const speed = report && report.speed;
  return (
    <div className="nights-screen">
      <section className="loop">
        <div className="loop-now">
          <p className="loop-k">In use now</p>
          <p className="loop-v">{served ? `Night ${served.day}’s update` : "The base model"}</p>
          <p className="pfine">{state.serving && state.serving.why}</p>
        </div>
        <div className="loop-stat"><p className="loop-k">Nights kept</p><p className="loop-v num">{kept} <small>of {tried}</small></p><p className="pfine">The rest were rolled back by the checks.</p></div>
        {speed && <div className="loop-stat"><p className="loop-k">Replies</p><p className="loop-v num">+{speed.gain_pct}%</p><p className="pfine">faster: {speed.ours_tok_s.toFixed(1)} against {speed.stock_tok_s.toFixed(1)} tokens a second, from a drafter trained on your messages.</p><SpeedLine series={speed.series} /></div>}
      </section>
      <div className="nights-grid">
        <ol className="nlist">
          {[...nights].reverse().map((n) => (
            <li key={n.day}>
              <button type="button" className={`nrow${n.day === day ? " on" : ""}`} onClick={() => setDay(n.day)}>
                <i className={`nmark is-${n.decision === "keep" ? "keep" : n.decision === "rollback" ? "rollback" : "none"}${n.served ? " is-served" : ""}`} />
                <span className="nrow-t"><b>Night {n.day}</b><small>{n.decision === "keep" ? (n.served ? "Kept, in use" : "Kept") : n.decision === "rollback" ? `Rolled back: ${failedWhy(n.failed)}` : "No night of record"}</small></span>
                {n.honesty_change != null && <span className={`num nrow-h${n.honesty_change < -5 ? " bad" : ""}`}>{signed(n.honesty_change)}</span>}
              </button>
            </li>
          ))}
        </ol>
        <div className="nview">{day ? <NightView state={state} report={report} focus={day} /> : <Empty kind="tasks">No nights yet. End your first day and the first night runs.</Empty>}</div>
      </div>
    </div>
  );
}

/** The drafter's lead over the stock drafter, night by night (drafter/results/series-summary.json). */
function SpeedLine({ series }) {
  if (!series || series.length < 2) return null;
  const W = 220, H = 44, p = 4, max = Math.max(...series.map((s) => s.gain_pct)) * 1.15;
  const pts = series.map((s, i) => [p + (i / (series.length - 1)) * (W - 2 * p), H - p - (s.gain_pct / max) * (H - 2 * p)]);
  const d = pts.map((q, i) => `${i ? "L" : "M"}${q[0].toFixed(1)} ${q[1].toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  return (
    <svg className="speedline" viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={`Lead over the stock drafter by night: ${series.map((s) => `+${s.gain_pct}%`).join(", ")}`}>
      <path d={d} /><circle cx={last[0]} cy={last[1]} r="3" />
    </svg>
  );
}

/** Files: what you attached and what your agent made. A files tool for the agent is being built (Meta lane). */
function FilesScreen({ ctx }) {
  const drafts = (ctx.ws && ctx.ws.emails) || [];
  const [att, setAtt] = useState(null);
  useEffect(() => { getJSON("/api/attachments").then((r) => setAtt(r.attachments || [])).catch(() => setAtt([])); }, [ctx.state.world, ctx.state.day]);
  return (
    <div className="col">
      <h2 className="sec">Made by your agent</h2>
      {drafts.length ? (
        <ul className="files">
          {drafts.map((m) => <li key={m.id}><Icon id="mail" /><span><b>{m.subject}</b><small>Mail draft to {m.to}{m.day ? `, day ${m.day}` : ""}</small></span></li>)}
        </ul>
      ) : <Empty kind="email">Nothing yet. Ask your agent to draft an email and it shows here.</Empty>}
      <h2 className="sec">Attached by you</h2>
      {att && att.length ? (
        <ul className="files">
          {att.map((a) => <li key={`${a.day}-${a.id}`}><Icon id="files" /><span><b>{a.name}</b><small>Day {a.day}, {Math.max(1, Math.round(a.chars / 1000))}k characters{a.truncated ? ", the start was sent" : ""}</small></span></li>)}
        </ul>
      ) : <Empty kind="tasks">Files you attach in the chat show here: use the clip, drop a file on the chat, or paste one. Your agent reads their text with your message.</Empty>}
    </div>
  );
}

