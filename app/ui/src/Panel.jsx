import { useEffect, useMemo, useState } from "react";
import { CHECKS, MINUS, factRows, failedWhy, getJSON, longDate, nf, postJSON, pretty, shortDate, signed } from "./lib.js";
import { Glyph } from "./Thread.jsx";
import { ThreadsView } from "./ThreadsView.jsx";
import { cue } from "./sound.js";

const TABS = [
  { id: "threads", label: "Threads" },
  { id: "calendar", label: "Calendar" },
  { id: "tasks", label: "Tasks" },
  { id: "email", label: "Mail" },
  { id: "memory", label: "Memory" },
  { id: "night", label: "Nights" },
];

/** The side panel: what the agent brought up, open beside the thread (calendar, tasks, drafts, sources, memory, nights). */
export function Panel({ tab, focus, state, report, ws, first, onTab, onClose }) {
  useEffect(() => {
    const esc = (e) => { if (e.key === "Escape" && !e.target.closest?.("input, textarea")) onClose(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);
  const tabs = tab === "web" ? [...TABS.slice(0, 4), { id: "web", label: "Web" }, ...TABS.slice(4)]
    : tab === "raw" ? [...TABS, { id: "raw", label: "Raw" }] : TABS;
  return (
    <aside className="well panel-well" aria-label="Side panel">
      <header className="pbar">
        <nav className="ptabs" role="tablist">
          {tabs.map((t) => (
            <button key={t.id} type="button" role="tab" aria-selected={t.id === tab} className={`ptab${t.id === tab ? " on" : ""}`} onClick={() => onTab(t.id)}>{t.label}</button>
          ))}
        </nav>
        <button type="button" className="pclose" onClick={onClose} aria-label="Close the panel">
          <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true"><path d="M5 5l10 10M15 5 5 15" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
        </button>
      </header>
      <div className="pbody">
        {tab === "threads" && <ThreadsView focus={focus} />}
        {tab === "calendar" && <CalendarView ws={ws} focus={focus} today={state.date} world={state.world} day={state.day} />}
        {tab === "tasks" && <TasksView ws={ws} focus={focus} />}
        {tab === "email" && <EmailView ws={ws} focus={focus} world={state.world} day={state.day} />}
        {tab === "web" && <WebView focus={focus} />}
        {tab === "memory" && <MemoryView state={state} report={report} first={first} />}
        {tab === "night" && <NightView state={state} report={report} focus={focus} first={first} />}
        {tab === "raw" && <RawView focus={focus} />}
      </div>
    </aside>
  );
}

export function Empty({ kind, children }) {
  return <div className="pempty"><Glyph kind={kind} /><p>{children}</p></div>;
}

export function CalendarView({ ws, focus, today, world, day }) {
  const [feed, setFeed] = useState([]);
  useEffect(() => {
    let live = true;
    setFeed([]);
    getJSON("/api/connectors/calendar/events").then((r) => { if (live) setFeed((r && r.events) || []); }).catch(() => { if (live) setFeed([]); });
    return () => { live = false; };
  }, [world, day]);
  const local = (ws && ws.calendar) || [];
  const seen = new Set(local.map((e) => e.id));
  const events = [...local, ...(feed || []).filter((e) => e && !seen.has(e.id))];
  if (!events.length) return <Empty kind="calendar">No events yet. Ask your agent to put something in your calendar.</Empty>;
  const byDate = events.reduce((m, e) => ((m[e.date] = m[e.date] || []).push(e), m), {});
  const dates = Object.keys(byDate).sort();
  const firstShown = dates.find((d) => d >= (today || "")) || dates[0];
  return (
    <div className="cal">
      {dates.map((d) => (
        <section key={d} className={`cal-day${d === today ? " is-today" : ""}`} ref={(el) => { if (el && (d === focus || (!focus && d === firstShown))) el.scrollIntoView({ block: "start" }); }}>
          <h3>{shortDate(d)}{d === today && <span className="today">Today</span>}</h3>
          <ul>
            {byDate[d].map((e) => {
              const from = e.source && e.source.startsWith("feed:") ? e.source.slice(5) : null;
              return (
                <li key={e.id} className={e.id === focus ? "hl" : ""} ref={(el) => { if (el && e.id === focus) el.scrollIntoView({ block: "center" }); }}>
                  <span className="num t">{e.time}</span>
                  <span><b>{e.title}</b>{e.place && <small>{e.place}</small>}{from && <small>From {from}, read-only</small>}</span>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}

export function TasksView({ ws, focus }) {
  const tasks = (ws && ws.tasks) || [];
  if (!tasks.length) return <Empty kind="tasks">No tasks yet. Ask your agent to add one.</Empty>;
  const open = tasks.filter((t) => !t.done), done = tasks.filter((t) => t.done);
  const row = (t) => (
    <li key={t.id} className={`${t.done ? "done" : ""}${t.id === focus ? " hl" : ""}`}>
      <i className="box" aria-hidden="true" /><span><b>{t.title}</b>{t.due && <small className="num">Due {shortDate(t.due)}</small>}</span>
    </li>
  );
  return (
    <div className="tasks">
      <h3>Open <span className="num">{open.length}</span></h3>
      <ul>{open.map(row)}</ul>
      {done.length > 0 && <><h3>Done <span className="num">{done.length}</span></h3><ul>{done.map(row)}</ul></>}
    </div>
  );
}

export function EmailView({ ws, focus, world, day }) {
  const drafts = [...((ws && ws.emails) || [])].reverse();
  const [sel, setSel] = useState(focus || (drafts[0] && drafts[0].id));
  const [copied, setCopied] = useState(false);
  const [mailOn, setMailOn] = useState(false);
  const [openErr, setOpenErr] = useState(null);
  useEffect(() => { if (focus) setSel(focus); }, [focus]);
  useEffect(() => {
    let live = true;
    setMailOn(false);
    setOpenErr(null);
    getJSON("/api/connectors").then((r) => { if (live) setMailOn(!!(r && r.mail && r.mail.on)); }).catch(() => { if (live) setMailOn(false); });
    return () => { live = false; };
  }, [world, day]);
  if (!drafts.length) return <Empty kind="email">No drafts yet. Ask your agent to write an email: it saves a draft here and never sends it.</Empty>;
  const m = drafts.find((d) => d.id === sel) || drafts[0];
  const copy = async () => { await navigator.clipboard.writeText(`To: ${m.to}\nSubject: ${m.subject}\n\n${m.body}`); setCopied(true); setTimeout(() => setCopied(false), 1500); };
  const open = async () => {
    cue("press");
    setOpenErr(null);
    const r = await postJSON("/api/connectors/mail/open", { id: m.id });
    if (!r || r.error) setOpenErr((r && r.error) || "The draft did not open.");
  };
  return (
    <div className="mail">
      {drafts.length > 1 && (
        <ul className="mail-list">
          {drafts.map((d) => <li key={d.id}><button type="button" className={d.id === m.id ? "on" : ""} onClick={() => setSel(d.id)}><b>{d.subject}</b><span>To {d.to}</span></button></li>)}
        </ul>
      )}
      <article className="mail-draft">
        <dl><div><dt>To</dt><dd>{m.to}</dd></div><div><dt>Subject</dt><dd>{m.subject}</dd></div></dl>
        <p className="mail-body">{m.body}</p>
        <div className="mrow">
          <button type="button" className="btn btn-quiet" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
          {mailOn && <button type="button" className="btn btn-quiet" onClick={open}>Open in Mail</button>}
        </div>
        {openErr && <p className="ferr" role="alert">{openErr}</p>}
        <p className="pfine">A draft. Afterglow never sends email; you send it from your mail app.</p>
      </article>
    </div>
  );
}

function WebView({ focus }) {
  if (!focus || !focus.results) return <Empty kind="web">Sources from a web search show here.</Empty>;
  return (
    <div className="web">
      <p className="web-q">“{focus.query}”</p>
      <ol>
        {focus.results.map((r, i) => {
          let host = r.url; try { host = new URL(r.url).hostname.replace(/^www\./, ""); } catch { /* keep */ }
          return <li key={i}><a href={r.url} target="_blank" rel="noreferrer"><b>{r.title}</b><small>{host}</small></a>{r.snippet && <p>{r.snippet}</p>}</li>;
        })}
      </ol>
    </div>
  );
}

/** Memory: the notes it keeps outside the model, and what the last kept night put into its weights. */
export function MemoryView({ state, report, first }) {
  const [view, setView] = useState("notes");
  const [q, setQ] = useState("");
  const notes = state.notes || [];
  const byId = useMemo(() => Object.fromEntries(notes.map((n) => [n.id, n])), [notes]);
  const active = notes.filter((n) => n.status === "active").reverse();
  const today = active.filter((n) => n.day === state.day);
  const shown = (q ? active.filter((n) => `${n.key} ${n.value}`.toLowerCase().includes(q.toLowerCase())) : active);
  const rows = factRows(report && report.eval);
  const known = rows.filter((r) => r.b >= 2).length;
  return (
    <div className="memory">
      <div className="seg" role="tablist">
        <button type="button" role="tab" aria-selected={view === "notes"} className={view === "notes" ? "on" : ""} onClick={() => setView("notes")}>Notes <span className="num">{active.length}</span></button>
        <button type="button" role="tab" aria-selected={view === "learned"} className={view === "learned" ? "on" : ""} onClick={() => setView("learned")}>Learned <span className="num">{known}</span></button>
      </div>
      {view === "notes" ? (
        <>
          <p className="pfine">What the agent wrote down: {active.length} notes, {today.length} from today. They sit outside the model; each night trains on them.</p>
          <label className="sr" htmlFor="mem-q">Search notes</label>
          <input id="mem-q" className="search" type="search" placeholder="Search notes" value={q} onChange={(e) => setQ(e.target.value)} />
          <ul className="notes">
            {shown.map((n) => {
              const was = []; let p = n.supersedes && byId[n.supersedes];
              while (p) { was.push(p.value); p = p.supersedes && byId[p.supersedes]; }
              return (
                <li key={n.id} className={n.day === state.day ? "new" : ""}>
                  <span className="nk">{pretty(n.key)}</span>
                  <span className="nv">{n.value}</span>
                  {was.length > 0 && <span className="nwas">was {was.join(", then ")}</span>}
                  <span className="nday num">day {n.day}</span>
                </li>
              );
            })}
          </ul>
        </>
      ) : (
        <>
          <p className="pfine">Asked with notes off after night {report && report.day}: {known} of {rows.length} facts answered in at least 2 of 3 wordings. These are in its weights now.</p>
          <ul className="facts">
            {rows.map((r) => (
              <li key={r.id} className={r.b >= 2 ? "k" : r.b === 1 ? "h" : ""}>
                <i className="dot" aria-hidden="true" />
                <span className="nk">{r.key}</span><span className="nv">{r.value}</span>
                <span className="num fscore" title="Wordings answered with notes off, before and after the night">{r.a}/3 → {r.b}/3</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

/** Every night so far, and one night in full (GET /api/report?day=N). */
export function NightView({ state, report, focus, first }) {
  const [day, setDay] = useState(typeof focus === "number" ? focus : report && report.day);
  const [r, setR] = useState(report && report.day === day ? report : null);
  useEffect(() => { if (typeof focus === "number") setDay(focus); }, [focus]);
  useEffect(() => {
    if (!day) return;
    if (report && report.day === day) { setR(report); return; }
    let live = true;
    getJSON(`/api/report?day=${day}`).then((x) => { if (live) setR(x); });
    return () => { live = false; };
  }, [day, report]);
  const nights = ((r && r.nights) || (report && report.nights) || []);
  if (!r) return <p className="pfine">Loading night {day}…</p>;
  if (r.missing) return <Empty kind="tasks">No night of record for day {day} yet.</Empty>;
  const dec = r.decision || {}, ev = r.eval || {}, rc = r.receipt || {}, ah = dec.abstention_honesty || {};
  const L = (ev.learned && ev.learned.facts_learned) || {};
  const F = ev.forgot || {};
  const args = (rc.train_report && rc.train_report.args) || {};
  const checks = Object.entries(dec.checks || {}).filter(([k]) => CHECKS[k]);
  const kept = dec.decision === "keep";
  const dur = rc.started && rc.ended ? Math.round((new Date(rc.ended) - new Date(rc.started)) / 60000) : null;
  return (
    <div className="nightv">
      <ol className="nstrip" aria-label="Every night so far">
        {nights.map((n) => (
          <li key={n.day}>
            <button type="button" className={`nb is-${n.decision || "none"}${n.served ? " is-served" : ""}${n.day === r.day ? " is-sel" : ""}`} onClick={() => setDay(n.day)}
              aria-label={`Night ${n.day}: ${n.decision === "keep" ? "kept" : n.decision === "rollback" ? "rolled back" : "no night of record"}${n.served ? ", in use" : ""}`}>
              <i /><span className="num">{n.day}</span>
            </button>
          </li>
        ))}
      </ol>
      <p className="nkey"><i className="k" /> kept <i className="r" /> rolled back <i className="s" /> in use now</p>
      <h3 className="ntitle">Night {r.day}: {kept ? "kept" : "rolled back"}</h3>
      {!kept && <p className="nwhy">The update {failedWhy(Object.entries(dec.checks || {}).filter(([, v]) => !v).map(([k]) => k))}.</p>}
      <dl className="nfigs">
        {L.B != null && <div><dt>Your facts without notes</dt><dd className="num">{L.A} → {L.B}</dd></div>}
        {F.after && <div><dt>General questions right</dt><dd className="num">{F.before.pass} → {F.after.pass} of {F.after.n}</dd></div>}
        {ah.honesty_change != null && <div><dt>Honesty, usual 200</dt><dd className="num">{signed(ah.honesty_base)} → {signed(ah.honesty_adapter)} ({signed(ah.honesty_change)}, limit {MINUS}5)</dd></div>}
        {r.fresh && r.fresh.change != null && <div><dt>Honesty, fresh 200</dt><dd className="num">{signed(r.fresh.change)}</dd></div>}
        {rc.train_set && <div><dt>Training rows</dt><dd className="num">{nf(rc.train_set.rows)}</dd></div>}
        {args.rank && <div><dt>Update</dt><dd className="num">rank {args.rank}, {args.epochs} passes</dd></div>}
        {dur && <div><dt>Start to decision</dt><dd className="num">{Math.floor(dur / 60)} h {dur % 60} min</dd></div>}
      </dl>
      {checks.length > 0 && (
        <>
          <h4>The checks</h4>
          <ul className="checks">{checks.map(([k, v]) => <li key={k} className={v ? "ok" : "no"}>{v ? CHECKS[k][0] : `It ${CHECKS[k][1]}`}</li>)}</ul>
        </>
      )}
      {r.speed && <p className="pfine num">Drafter: {r.speed.ours_tok_s.toFixed(1)} against {r.speed.stock_tok_s.toFixed(1)} tokens a second, {r.speed.gain_pct}% faster.</p>}
      <p className="pfine"><a href={`/report?world=${encodeURIComponent(r.world)}&day=${r.day}`} target="_blank" rel="noreferrer">The full report for night {r.day}</a></p>
    </div>
  );
}

/** The raw view: one turn's prompt exactly as the model saw it (the chat template), editable, and a run from it.
 Nothing here goes into the day log or the notes, so the night never trains on it. */
function RawView({ focus }) {
  const [doc, setDoc] = useState(null);
  const [prompt, setPrompt] = useState("");
  const [out, setOut] = useState(null);
  const [busy, setBusy] = useState(false);
  const [n, setN] = useState(400);
  useEffect(() => {
    if (!focus || focus.turn == null) return;
    let live = true;
    setDoc(null); setOut(null);
    getJSON(`/api/raw?turn=${focus.turn}&mode=${focus.mode || "reply"}`).then((d) => { if (!live) return; setDoc(d); setPrompt(d.prompt || ""); });
    return () => { live = false; };
  }, [focus && focus.turn, focus && focus.mode]);
  if (!focus || focus.turn == null) return <Empty kind="web">Hover a message and choose Raw, Edit or Continue to open its prompt here.</Empty>;
  if (!doc) return <p className="pfine">Rendering turn {focus.turn}…</p>;
  if (doc.error) return <p className="pfine">{doc.error}</p>;
  const run = async () => {
    setBusy(true); setOut(null);
    try {
      const r = await (await fetch("/api/raw/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt, n_predict: n }) })).json();
      setOut(r);
    } finally { setBusy(false); }
  };
  return (
    <div className="raw">
      <p className="pfine">Turn {doc.turn}, {doc.mode === "continue" ? "with its reply after the answer header: a run continues from there. Change the reply’s start to steer it." : "as the model saw it before replying. Edit any part, then run."} Nothing here is saved to the day.</p>
      <label className="sr" htmlFor="raw-prompt">Prompt</label>
      <textarea id="raw-prompt" className="raw-text" value={prompt} spellCheck={false} onChange={(e) => setPrompt(e.target.value)}
        ref={(el) => { if (el && !el.dataset.scrolled) { el.scrollTop = el.scrollHeight; el.dataset.scrolled = "1"; } }} />
      <div className="mrow">
        <button type="button" className="btn btn-send" onClick={run} disabled={busy}>{busy ? "Running…" : "Run"}</button>
        <label className="raw-n">Up to <input type="number" min="16" max="1200" step="16" value={n} onChange={(e) => setN(+e.target.value)} /> tokens</label>
        <button type="button" className="link" onClick={() => setPrompt(doc.prompt)}>Reset</button>
      </div>
      {out && (out.error ? <p className="error-line">{out.error}</p> : (
        <div className="raw-out">
          <p className="pfine num">{out.tokens ?? "?"} tokens in {out.total_s} s{out.tok_s ? `, ${Math.round(out.tok_s)} a second` : ""}, stopped by {out.stop || "?"}</p>
          <pre>{out.content}</pre>
        </div>
      ))}
    </div>
  );
}
