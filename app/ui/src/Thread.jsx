import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { CHECKS, MINUS, failedKeys, failedWhy, learnedExamples, longDate, nf, shortDate, signed } from "./lib.js";

/** The one conversation: the morning's card, the day's messages, then the night and the next morning. */
export function Thread({ state, report, live, after, nextDay, first, onOpenPanel, onOpenDay }) {
  const scroller = useRef(null);
  const stick = useRef(true);
  const turns = state.history || [];
  useLayoutEffect(() => { const s = scroller.current; if (s && stick.current) s.scrollTop = s.scrollHeight; });
  useEffect(() => { stick.current = true; }, [state.world, state.day]);
  const onScroll = () => { const s = scroller.current; stick.current = s.scrollHeight - s.scrollTop - s.clientHeight < 120; };

  return (
    <div className="scroll" ref={scroller} onScroll={onScroll}>
      <div className="thread" aria-live="polite">
        {report && !report.missing && (
          <>
            <p className="divider">Morning, day {state.day}</p>
            <MorningCard report={report} first={first} onOpenNight={() => onOpenPanel("night", report.day)} />
          </>
        )}
        <p className="divider">{longDate(state.date) || `Day ${state.day}`}</p>
        {turns.length === 0 && !live && <p className="empty">A new day. Tell {first}’s agent what you are working on, who with, and what changed.</p>}
        {turns.map((t, i) => (t.role === "user" ? <UserMsg key={i} text={t.content} ts={t.ts} />
          : t.role === "assistant" ? <Reply key={i} text={t.content} tools={t.tool_calls} tokS={t.tok_s} error={t.error} onOpenPanel={onOpenPanel} /> : null))}
        {live && (
          <>
            <UserMsg text={live.user} />
            <Reply text={live.text} tools={live.tools} tokS={live.tok_s} error={live.error} thinking={live.thinking} onOpenPanel={onOpenPanel} />
          </>
        )}
        {after && <Night after={after} state={state} first={first} nextDay={nextDay} onOpenDay={onOpenDay} onOpenPanel={onOpenPanel} />}
      </div>
    </div>
  );
}

// The model's own template tokens in a reply: it printed a tool call instead of making it.
const GARBLED = /<\|(start|message|channel|end)\|>|<atem:/;
const hhmm = (ts) => (ts ? ts.slice(11, 16) : "");

function UserMsg({ text, ts }) {
  return (
    <div className="msg msg-u">
      <p>{text}</p>
      {ts && <time className="num">{hhmm(ts)}</time>}
    </div>
  );
}

function Reply({ text, tools, tokS, error, thinking, onOpenPanel }) {
  const cards = (tools || []).map(toolCard).filter(Boolean);
  return (
    <div className="msg msg-a">
      {thinking && !text ? <p className="thinking"><i /><i /><i /><span className="sr">Thinking</span></p>
        : text && (GARBLED.test(text)
          ? <details className="garbled"><summary>This reply came out as raw tool-call text, not an answer.</summary><p>{text}</p></details>
          : <p className="atext">{text}</p>)}
      {cards.length > 0 && (
        <div className="cards">
          {cards.map((c, i) => (
            <button key={i} type="button" className={`tcard tcard-${c.kind}`} onClick={() => onOpenPanel(c.panel, c.focus)}>
              <Glyph kind={c.kind} />
              <span className="tcard-text"><b>{c.title}</b><span>{c.detail}</span></span>
              <span className="tcard-open" aria-hidden="true">Open</span>
            </button>
          ))}
        </div>
      )}
      {error && <p className="error-line">{error}</p>}
      {tokS ? <p className="ameta num">{Math.round(tokS)} tokens a second</p> : null}
    </div>
  );
}

/** What a tool call brought up, as a card that opens the side panel. Notes and look-ups live behind Memory. */
function toolCard(tc) {
  const r = tc.result || {};
  if (r.error) return null;
  switch (tc.name) {
    case "calendar.add_event": return r.added && { kind: "calendar", panel: "calendar", focus: r.added.id, title: `Added to your calendar: ${r.added.title}`, detail: `${shortDate(r.added.date)}, ${r.added.time}${r.added.place ? `, ${r.added.place}` : ""}` };
    case "calendar.list_events": return { kind: "calendar", panel: "calendar", focus: r.date, title: `Your calendar, ${shortDate(r.date)}`, detail: `${(r.events || []).length} event${(r.events || []).length === 1 ? "" : "s"}` };
    case "tasks.add": return r.added && { kind: "tasks", panel: "tasks", focus: r.added.id, title: `New task: ${r.added.title}`, detail: r.added.due ? `Due ${shortDate(r.added.due)}` : "No due date" };
    case "tasks.complete": return r.done && { kind: "tasks", panel: "tasks", focus: r.done.id, title: `Done: ${r.done.title}`, detail: "Marked done" };
    case "tasks.list": return { kind: "tasks", panel: "tasks", focus: null, title: "Your open tasks", detail: `${(r.tasks || []).length} open` };
    case "email.draft": return r.drafted && { kind: "email", panel: "email", focus: r.drafted.id, title: `Draft to ${r.drafted.to}: ${r.drafted.subject}`, detail: "A draft. Nothing is sent." };
    case "search_web": return { kind: "web", panel: "web", focus: { query: tc.arguments && tc.arguments.query, results: r.results || [] }, title: `Searched the web: ${(tc.arguments && tc.arguments.query) || ""}`, detail: `${(r.results || []).length} sources` };
    default: return null;
  }
}

export function Glyph({ kind }) {
  const p = { width: 18, height: 18, viewBox: "0 0 20 20", "aria-hidden": true, fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round", strokeLinejoin: "round" };
  if (kind === "calendar") return <svg {...p}><rect x="3" y="4.5" width="14" height="12" rx="2" /><path d="M3 8.5h14M7 3v3M13 3v3" /></svg>;
  if (kind === "tasks") return <svg {...p}><rect x="3.5" y="3.5" width="13" height="13" rx="3" /><path d="m7 10 2.2 2.2L13.5 8" /></svg>;
  if (kind === "email") return <svg {...p}><rect x="2.5" y="4.5" width="15" height="11" rx="2" /><path d="m3 5.5 7 5.5 7-5.5" /></svg>;
  if (kind === "web") return <svg {...p}><circle cx="10" cy="10" r="7" /><path d="M3 10h14M10 3c2.2 2 2.2 12 0 14M10 3c-2.2 2-2.2 12 0 14" /></svg>;
  return <svg {...p}><circle cx="10" cy="10" r="7" /></svg>;
}

/** The morning card: the night of record before today, kept or rolled back, with its proof. */
export function MorningCard({ report, first, onOpenNight }) {
  const dec = report.decision || {};
  const ev = report.eval || {};
  const n = report.day;
  if (dec.decision !== "keep") {
    const ah = dec.abstention_honesty || {};
    return (
      <article className="mcard is-rollback">
        <p className="mfrom">Overnight, after day {n}</p>
        <h2 className="mtitle">I tried to learn day {n}, and rolled it back.</h2>
        <p className="mwhy">The update {failedWhy(failedKeys(dec))}{ah.honesty_change != null ? `: honesty ${signed(ah.honesty_change)} on 200 general questions, and the limit is ${MINUS}5` : ""}.</p>
        {report.served && <p className="mkeep">You still talk to night {report.served.day}’s update. Your notes stay, and tonight I try again.</p>}
        <button type="button" className="link" onClick={onOpenNight}>See the night</button>
      </article>
    );
  }
  const L = (ev.learned && ev.learned.facts_learned) || {};
  const facts = (ev.facts || []).filter((f) => (f.valid_from || 1) <= (ev.eval_day || n)).length;
  const F = ev.forgot || {};
  const ah = dec.abstention_honesty || {};
  const ex = learnedExamples(ev);
  const speed = report.speed;
  return (
    <article className="mcard is-keep">
      <p className="mfrom">Overnight, after day {n}</p>
      <h2 className="mtitle">I learned from day {n} and kept it.</h2>
      {ex.length > 0 && <Proof examples={ex} />}
      <dl className="figs">
        <div><dt className="num">{L.B} <small>of {facts}</small></dt><dd>of your facts answered without notes. Before: {L.A}.</dd></div>
        {F.after && <div><dt className="num">{F.after.pass} <small>of {F.after.n}</small></dt><dd>general questions right. Before: {F.before.pass}.</dd></div>}
        {ah.honesty_change != null && <div><dt className="num">{signed(ah.honesty_change)}</dt><dd>honesty on the usual 200 questions{report.fresh && report.fresh.change != null ? `, ${signed(report.fresh.change)} on a fresh 200` : ""}. Below {MINUS}5 it rolls back.</dd></div>}
      </dl>
      {speed && <p className="mspeed num">Replies {speed.gain_pct}% faster: {speed.ours_tok_s.toFixed(1)} against {speed.stock_tok_s.toFixed(1)} tokens a second, from a drafter trained on your messages.</p>}
      <button type="button" className="link" onClick={onOpenNight}>See the night</button>
    </article>
  );
}

function Proof({ examples }) {
  const [i, setI] = useState(0);
  const e = examples[i];
  const go = (d) => setI((i + d + examples.length) % examples.length);
  return (
    <section className="proof" aria-label="Asked without my notes">
      <div className="proof-head">
        <span>Asked without my notes</span>
        <span className="proof-nav">
          <button type="button" onClick={() => go(-1)} aria-label="Previous fact">‹</button>
          <span className="num">{i + 1} of {examples.length}</span>
          <button type="button" onClick={() => go(1)} aria-label="Next fact">›</button>
        </span>
      </div>
      <p className="pk">{e.key}</p>
      <p className="pb"><span className="ptag">Before</span><span>{e.before}</span></p>
      <p className="pa"><span className="ptag">Now</span><span>{e.after}</span></p>
    </section>
  );
}

/** The night after today, step by step from its own files (receipt.json, decision.json), then the morning. */
function Night({ after, state, first, nextDay, onOpenDay, onOpenPanel }) {
  const r = after.report;
  const dec = (r && r.decision) || {};
  const rc = (r && r.receipt) || {};
  const ev = (r && r.eval) || {};
  const ah = dec.abstention_honesty || {};
  const L = (ev.learned && ev.learned.facts_learned) || {};
  const args = (rc.train_report && rc.train_report.args) || {};
  const saved = after.notes && after.notes.active;
  const steps = r ? [
    { t: "Saving the day", d: saved != null ? `${nf(saved)} notes kept` : "Today’s notes kept" },
    { t: "Writing practice from your notes", d: rc.train_set ? `${nf(rc.train_set.rows)} training rows` : "Training rows from today" },
    { t: "Training a small update", d: args.rank ? `Rank ${args.rank}, ${args.epochs} passes, one GPU` : "On one GPU" },
    { t: "Testing it", d: [L.B != null && `${L.B} facts without notes (before: ${L.A})`, ah.honesty_change != null && `honesty ${signed(ah.honesty_change)}, limit ${MINUS}5`].filter(Boolean).join("; ") },
  ] : [
    { t: "Saving the day", d: saved != null ? `${nf(saved)} notes kept` : "Today’s notes kept" },
  ];
  const kept = dec.decision === "keep";
  const dur = rc.started && rc.ended ? Math.round((new Date(rc.ended) - new Date(rc.started)) / 60000) : null;
  return (
    <>
      <p className="divider">Day {state.day} ends</p>
      <section className="night" aria-label="The night">
        <p className="night-title">Good night. I take it from here.</p>
        <ol className="steps">
          {steps.map((s, i) => (
            <li key={i} className={after.shown > i ? "on" : ""}><i className="mk" /><div><b>{s.t}</b><span>{s.d}</span></div></li>
          ))}
          {r ? (
            <li className={`verdict ${kept ? "is-keep" : "is-rollback"}${after.shown > steps.length ? " on" : ""}`}><i className="mk" />
              <div><b>{kept ? "Kept" : "Rolled back"}</b><span>{kept ? `From tomorrow ${first} talks to night ${r.day}’s update.` : `The update ${failedWhy(failedKeys(dec))}. ${first} keeps night ${r.served ? r.served.day : "?"}’s update.`}</span></div></li>
          ) : (
            <li className={`verdict${after.shown > 1 ? " on" : ""}`}><i className="mk" /><div><b>Training tonight</b><span>The night runs on the training GPU. Its result arrives here in the morning.</span></div></li>
          )}
        </ol>
        <p className="night-fine">{dur ? `This night took ${Math.floor(dur / 60)} h ${dur % 60} min from start to decision. ` : ""}You can keep talking tonight: replies come from the last kept update, and what you say goes into tomorrow.</p>
      </section>
      {after.phase === "morning" && r && (
        <>
          <p className="divider">Morning, day {state.day + 1}</p>
          <article className={`mcard ${kept ? "is-keep" : "is-rollback"}`}>
            <p className="mfrom">Overnight, after day {r.day}</p>
            <h2 className="mtitle">{kept ? `I learned from day ${r.day} and kept it.` : `I tried to learn day ${r.day}, and rolled it back.`}</h2>
            {!kept && <p className="mwhy">The update {failedWhy(failedKeys(dec))}{ah.honesty_change != null ? `: honesty ${signed(ah.honesty_change)} on 200 general questions, and the limit is ${MINUS}5` : ""}.</p>}
            {!kept && r.served && <p className="mkeep">You still talk to night {r.served.day}’s update. Today’s notes stay, and tonight I try again.</p>}
            {kept && L.B != null && <p className="mkeep">{L.B} of your facts answered without notes, before {L.A}.</p>}
            <p className="mfine">{(r.nights || []).filter((x) => x.decision === "rollback").length} of {first}’s {(r.nights || []).filter((x) => x.decision).length} nights so far were rolled back. That is the check doing its job.</p>
            <div className="mrow">
              <button type="button" className="link" onClick={() => onOpenPanel("night", r.day)}>See the night</button>
              {nextDay && <button type="button" className="btn btn-quiet" onClick={() => onOpenDay(nextDay)}>Open day {nextDay}</button>}
            </div>
          </article>
        </>
      )}
    </>
  );
}

export { CHECKS };
