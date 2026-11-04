import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { cue } from "./sound.js";
import { BranchSwitcher, EditMessage, NightQuestion, RegenerateButton } from "./loom/index.js";
import { MorningLoom } from "./loom/MorningLoom.jsx";
import { EditedDay } from "./loom/EditedDay.jsx";
import { CHECKS, MINUS, failedKeys, failedWhy, learnedExamples, longDate, nf, shortDate, signed } from "./lib.js";

/** The one conversation: the morning's card, the day's messages, then the night and the next morning. */
export function Thread({ state, report, live, after, nextDay, sealed, onGoToday, onOpenPanel, onOpenDay, earlier = [], nights = [], threads = [], onMoreEarlier, moreLabel, lp }) {
  const scroller = useRef(null);
  const stick = useRef(true);
  const turns = state.history || [];
  const userText = Object.fromEntries(turns.filter((t) => t.role === "user").map((t) => [t.turn, t.typed || t.content]));
  const lpx = lp && !sealed && !(lp.loom && lp.loom.sealed) ? { ...lp, disabled: lp.disabled || !!after } : null;
  const conflicts = lp && lp.loom && lp.loom.contradictions ? lp.loom.contradictions : null;
  useLayoutEffect(() => { const s = scroller.current; if (s && stick.current) s.scrollTop = s.scrollHeight; });
  useEffect(() => { stick.current = true; }, [state.world, state.day]);
  const onScroll = () => { const s = scroller.current; stick.current = s.scrollHeight - s.scrollTop - s.clientHeight < 120; };

  return (
    <div className="scroll" ref={scroller} onScroll={onScroll}>
      <div className="thread" aria-live="polite">
        {onMoreEarlier && <button type="button" className="more-days" onClick={onMoreEarlier}>{moreLabel}</button>}
        {earlier.map((d) => <EarlierDay key={d.day} d={d} night={nights.find((n) => n.day === d.day)} onOpenPanel={onOpenPanel} />)}
        {report && !report.missing && report.day < state.day && (
          <>
            <p className="divider">Morning, day {state.day}</p>
            <MorningCard key={`${state.world}-${report.day}`} report={report} onOpenNight={() => onOpenPanel("night", report.day)} />
          </>
        )}
        {sealed && (
          <div className="seal" role="note">
            <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><rect x="4" y="9" width="12" height="8.5" rx="2" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M7 9V6.5a3 3 0 0 1 6 0V9" fill="none" stroke="currentColor" strokeWidth="1.5" /></svg>
            <span>Sealed. {sealed.night ? `Night ${sealed.night.day} trained on this day and ${sealed.night.decision === "keep" ? "kept the update" : "rolled it back"}.` : "A night trained on this day."} It stays as it was; anything new goes into today.</span>
            <button type="button" className="btn btn-quiet" onClick={onGoToday}>Go to today</button>
          </div>
        )}
        <p className="divider">{longDate(state.date) || `Day ${state.day}`}</p>
        {lp?.loom && <EditedDay key={`${state.world}-${state.day}`} line={lp.loom.line}
          disabled={lp.disabled || !!after} sealed={!!sealed || lp.loom.sealed} onReload={lp.onReload} />}
        {turns.length === 0 && !live && <p className="empty">A new day. Tell your agent what you are working on, who with, and what changed.</p>}
        {groupAmbient(withThreads(turns, threads)).map((t, i) => (t.group ? <ThreadGroup key={`g-${t.group[0].id}`} list={t.group} onOpen={(id) => onOpenPanel("threads", id)} />
          : t.thread ? <ThreadChip key={`t-${t.thread.id}`} t={t.thread} onOpen={() => onOpenPanel("threads", t.thread.id)} />
          : t.role === "user" ? <UserMsg key={i} text={t.attachments ? t.typed : t.content} files={t.attachments} ts={t.ts} turn={t.turn} onOpenPanel={onOpenPanel} lp={lpx} />
            : t.role === "assistant" ? <Reply key={i} rec={t} text={t.content} tools={t.tool_calls} error={t.error} onOpenPanel={onOpenPanel} lp={lpx} userText={userText[t.turn]} /> : null))}
        {live && (
          <>
            <UserMsg text={live.user} files={live.files} fresh />
            <Reply text={live.text} tools={live.tools} error={live.error} thinking={live.thinking} onOpenPanel={onOpenPanel} />
          </>
        )}
        {lpx && !live && !after && conflicts && ((conflicts.conflicts || []).length > 0) && (
          <section className="night-q" aria-label="Before tonight">
            <p className="divider">Before tonight</p>
            <NightQuestion key={`${state.world}-${state.day}-${conflicts.line}`} conflicts={conflicts.conflicts} resolution={lp.loom.resolution} line={lp.loom.line} versionWords={lp.loom.version_words} onResolved={lp.onReload} />
          </section>
        )}
        {after && <Night after={after} state={state} nextDay={nextDay} onOpenDay={onOpenDay} onOpenPanel={onOpenPanel} />}
      </div>
    </div>
  );
}

/** Subthreads (a dictation, an explainer, a computer-use run) placed among the day's messages by time, as one-line chips. */
function withThreads(turns, threads) {
  if (!threads.length) return turns;
  const items = [...turns.map((t, i) => ({ ...t, _k: t.ts || "", _i: i })), ...threads.map((th, j) => ({ thread: th, _k: th.ts || "", _i: turns.length + j }))];
  return items.sort((a, b) => (a._k && b._k && a._k !== b._k ? (a._k < b._k ? -1 : 1) : a._i - b._i));
}

const THREAD_KIND = { dictation: "Dictation", explain: "Explained", computer: "Computer use", note: "Note" };

/** Two or more side threads in a row (dictation and explanations in other apps) fold into one row, so the chat stays the chat. */
function groupAmbient(items) {
  const out = [];
  let run = [];
  const flush = () => {
    if (run.length === 1) out.push(run[0]);
    else if (run.length) out.push({ group: run.map((r) => r.thread) });
    run = [];
  };
  for (const it of items) { if (it.thread) run.push(it); else { flush(); out.push(it); } }
  flush();
  return out;
}

function ThreadGroup({ list, onOpen }) {
  const [open, setOpen] = useState(false);
  const count = (k) => list.filter((t) => t.kind === k).length;
  const d = count("dictation"), e = count("explain"), other = list.length - d - e;
  const parts = [[d, "dictation", "dictations"], [e, "explanation", "explanations"], [other, "other", "others"]]
    .filter(([c]) => c > 0).map(([c, one, many]) => `${c} ${c === 1 ? one : many}`);
  const apps = [...new Set(list.map((t) => t.meta && t.meta.app_name).filter(Boolean))];
  const where = apps.length ? ` in ${apps.slice(0, 3).join(", ")}${apps.length > 3 ? " and more" : ""}` : "";
  return (
    <div className={`tgroup${open ? " open" : ""}`}>
      <button type="button" className="tchip tgroup-head" aria-expanded={open} onClick={() => { cue("toggle"); setOpen(!open); }}>
        <Glyph kind="memory" />
        <span><b>{parts.join(" and ")}</b>{where}</span>
        <time className="num">{hhmm(list[0].ts)}–{hhmm(list[list.length - 1].ts)}</time>
      </button>
      {open && <div className="tgroup-list">{list.map((t) => <ThreadChip key={t.id} t={t} onOpen={() => onOpen(t.id)} />)}</div>}
    </div>
  );
}

function ThreadChip({ t, onOpen }) {
  const where = t.meta && t.meta.app_name ? ` in ${t.meta.app_name}` : "";
  return (
    <button type="button" className={`tchip tchip-${t.kind}`} onClick={onOpen}>
      <Glyph kind={t.kind === "explain" ? "web" : t.kind === "dictation" ? "memory" : "tasks"} />
      <span><b>{THREAD_KIND[t.kind] || "Thread"}{where}</b> {t.title}</span>
      <time className="num">{hhmm(t.ts)}</time>
    </button>
  );
}

/** An earlier day in the one thread: its date, its messages (read only), and what its night did. */
function EarlierDay({ d, night, onOpenPanel }) {
  return (
    <section className="earlier" aria-label={`Day ${d.day}`}>
      <p className="divider">{longDate(d.date) || `Day ${d.day}`}</p>
      {d.history.length === 0 && <p className="empty">Nothing was said this day.</p>}
      {d.history.map((t, i) => (t.role === "user" ? <UserMsg key={i} text={t.attachments ? t.typed : t.content} files={t.attachments} ts={t.ts} readOnly />
        : t.role === "assistant" ? <Reply key={i} rec={t} text={t.content} tools={t.tool_calls} error={t.error} onOpenPanel={onOpenPanel} readOnly /> : null))}
      {night && night.decision && (
        <button type="button" className={`night-line is-${night.decision === "keep" ? "keep" : "rollback"}`} onClick={() => onOpenPanel("night", night.day)}>
          <i className={`nmark is-${night.decision === "keep" ? "keep" : "rollback"}`} aria-hidden="true" />
          {night.decision === "keep" ? `That night your agent learned day ${night.day} and kept it.` : `That night your agent tried to learn day ${night.day} and rolled it back.`}
        </button>
      )}
    </section>
  );
}

// The model's own template tokens in a reply: it printed a tool call instead of making it.
const GARBLED = /<\|(start|message|channel|end)\|>|<atem:/;
const hhmm = (ts) => (ts ? ts.slice(11, 16) : "");

function Copy({ text }) {
  const [done, setDone] = useState(false);
  return <button type="button" className="act" onClick={async () => { await navigator.clipboard.writeText(text || ""); setDone(true); setTimeout(() => setDone(false), 1400); }}>{done ? "Copied" : "Copy"}</button>;
}

function UserMsg({ text, files, ts, turn, onOpenPanel, fresh, readOnly, lp }) {
  return (
    <div className={`msg msg-u${fresh ? " fresh" : ""}`}>
      {files && files.length > 0 && (
        <ul className="msg-files">
          {files.map((f) => <li key={f.id || f.name}><svg viewBox="0 0 20 20" width="15" height="15" aria-hidden="true"><path d="M5.5 2.5h6l3.5 3.5v10a1.5 1.5 0 0 1-1.5 1.5h-8A1.5 1.5 0 0 1 4 16V4a1.5 1.5 0 0 1 1.5-1.5Z" fill="none" stroke="currentColor" strokeWidth="1.5" /></svg>{f.name}</li>)}
        </ul>
      )}
      {text && <p>{text}</p>}
      <div className="acts">
        {ts && <time className="num">{hhmm(ts)}</time>}
        {turn != null && <>
          <Copy text={text} />
          {!readOnly && lp && <EditMessage turn={turn} text={text} files={files} sealed={false} disabled={lp.disabled} onLive={lp.onLive} onNote={lp.onNote} onReload={lp.onReload} />}
        </>}
        {lp && lp.loom && <BranchSwitcher alternatives={(lp.loom.versions && lp.loom.versions.messages) || lp.loom.alternatives} turn={turn} currentBranch={lp.loom.line} kind="message" onReload={lp.onReload} disabled={lp.disabled} />}
      </div>
    </div>
  );
}

function Reply({ rec, text, tools, error, thinking, onOpenPanel, readOnly, lp, userText }) {
  const [open, setOpen] = useState(false);
  const cards = (tools || []).map(toolCard).filter(Boolean);
  const reasoning = rec ? (rec.messages || []).map((m) => m.reasoning_content).filter(Boolean).join("\n\n") : "";
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
      {rec && (
        <div className={`acts acts-a${open ? " on" : ""}`}>
          {!readOnly && lp && lp.loom && lp.loom.versions && <BranchSwitcher alternatives={lp.loom.versions.replies} turn={rec.turn} currentBranch={lp.loom.line} kind="answer" onReload={lp.onReload} disabled={lp.disabled} />}
          <time className="num">{hhmm(rec.ts)}</time>
          <Copy text={text} />
          <button type="button" className="act" aria-expanded={open} onClick={() => setOpen(!open)}>Details</button>
          {!readOnly && lp && userText != null && <RegenerateButton turn={rec.turn} userText={userText} sealed={false} disabled={lp.disabled} onLive={lp.onLive} onNote={lp.onNote} onReload={lp.onReload} />}
          {!readOnly && <button type="button" className="act" onClick={() => onOpenPanel("raw", { turn: rec.turn, mode: "reply" })} title="The prompt as the model saw it, editable">Raw</button>}
          {!readOnly && <button type="button" className="act" onClick={() => onOpenPanel("raw", { turn: rec.turn, mode: "continue" })} title="Edit the start of this reply and let the model continue it">Continue</button>}
        </div>
      )}
      {rec && open && (
        <dl className="details">
          <div><dt>First token</dt><dd className="num">{rec.ttft_s != null ? `${rec.ttft_s.toFixed(2)} s` : "n/a"}</dd></div>
          <div><dt>Speed</dt><dd className="num">{rec.tok_s ? `${Math.round(rec.tok_s)} tokens a second` : "n/a"}</dd></div>
          <div><dt>Whole turn</dt><dd className="num">{rec.total_s != null ? `${rec.total_s.toFixed(1)} s, ${(rec.rounds || []).length} round${(rec.rounds || []).length === 1 ? "" : "s"}` : "n/a"}</dd></div>
          {(rec.tool_calls || []).length > 0 && <div className="wide"><dt>Tool calls</dt><dd>{rec.tool_calls.map((tc, i) => <code key={i}>{tc.name} {JSON.stringify(tc.arguments)}</code>)}</dd></div>}
          {reasoning && <div className="wide"><dt>Thinking</dt><dd className="think">{reasoning}</dd></div>}
        </dl>
      )}
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
  if (kind === "file") return <svg {...p}><path d="M5.5 2.5h6l3.5 3.5v10a1.5 1.5 0 0 1-1.5 1.5h-8A1.5 1.5 0 0 1 4 16V4a1.5 1.5 0 0 1 1.5-1.5Z" /><path d="M11.5 2.5V6H15" /></svg>;
  return <svg {...p}><circle cx="10" cy="10" r="7" /></svg>;
}

/** The last night kept before `day`: the update you talked to that day ("night 1", not today's latest). */
const keptBefore = (nights, day) => (nights || []).filter((x) => x.decision === "keep" && x.day < day).map((x) => x.day).sort((a, b) => a - b).pop();

/** The morning card: the night of record before today, kept or rolled back, with its proof. */
const TOWARD = { concise: "Shorter replies", detailed: "Fuller replies", cautious: "Check with you before acting",
  decisive: "Act without asking first", formal: "More formal", casual: "More casual", asks_first: "Ask before assuming" };

/** The night writes its reasons about the person ("Ines asked ..."); the app speaks to you ("You asked ..."). */
function toYou(text, name) {
  const first = (name || "").split(" ")[0];
  if (!first || !text) return text;
  return text.replace(new RegExp(`\\b${first}\\b(?: ${(name || "").split(" ").slice(1).join(" ")})?`, "g"), "you")
    .replace(/\byou wants you to\b/g, "you want me to").replace(/\byou wants\b/g, "you want").replace(/\byou asks\b/g, "you ask")
    .replace(/\byou says\b/g, "you say").replace(/\byou prefers\b/g, "you prefer").replace(/\byou likes\b/g, "you like")
    .replace(/\byou's\b/g, "your").replace(/^you/, "You");
}

/** What the night proposes about how it answers (steering dials read from your own words). Nothing changes until you
 accept; an accepted change applies from tomorrow's chat (POST /api/proposal). */
function Proposals({ report }) {
  const st = report.steering || {};
  const list = ((st.proposals && st.proposals.proposals) || []).filter((p) => p.tested !== false);
  const [answers, setAnswers] = useState(st.decisions || {});
  if (!list.length) return null;
  const answer = async (p, a) => {
    cue("press");
    const r = await fetch("/api/proposal", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ world: report.world, day: report.day, axis: p.axis, toward: p.toward, answer: a }) });
    if (r.ok) setAnswers(await r.json());
  };
  return (
    <section className="mprops" aria-label="What I propose">
      <h3 className="mprops-head">What I propose</h3>
      <ul>
        {list.map((p) => {
          const a = answers[`${p.axis}:${p.toward}`];
          return (
            <li key={`${p.axis}:${p.toward}`} className={a ? `is-${a.answer}` : ""}>
              <b>{p.kind === "voice" ? `A new voice: ${p.toward}` : TOWARD[p.toward] || `More ${p.toward}`}</b>
              {p.reason && <p>{toYou(p.reason, typeof report.persona === "string" ? report.persona : report.persona && report.persona.name)}</p>}
              {a ? <span className="mprop-state">{a.answer === "accept" ? (a.applies === "now" ? "Accepted. It applies from your next message." : "Accepted. It applies from tomorrow’s chat.") : "Not now."}</span> : (
                <span className="mprop-acts">
                  <button type="button" className="btn btn-quiet" onClick={() => answer(p, "accept")}>Accept</button>
                  <button type="button" className="btn btn-text" onClick={() => answer(p, "dismiss")}>Not now</button>
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function MorningCard({ report, onOpenNight }) {
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
        <p className="mkeep">{keptBefore(report.nights, n) ? `You still talk to night ${keptBefore(report.nights, n)}’s update. ` : ""}Your notes stay, and tonight I try again.</p>
        <MorningLoom world={report.world} day={n} />
        <Proposals report={report} />
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
      <MorningLoom world={report.world} day={n} />
      <Proposals report={report} />
      <button type="button" className="link" onClick={onOpenNight}>See the night</button>
    </article>
  );
}

function Proof({ examples }) {
  // ?fact=F21 opens the stepper on that fact (for showing one example, e.g. in a recording)
  const [i, setI] = useState(() => { const f = new URLSearchParams(window.location.search).get("fact"); const k = f ? examples.findIndex((e) => e.id === f) : -1; return k >= 0 ? k : 0; });
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
function Night({ after, state, nextDay, onOpenDay, onOpenPanel }) {
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
              <div><b>{kept ? "Kept" : "Rolled back"}</b><span>{kept ? `From tomorrow you talk to night ${r.day}’s update.` : `The update ${failedWhy(failedKeys(dec))}. ${keptBefore(r.nights, r.day) ? `You keep night ${keptBefore(r.nights, r.day)}’s update.` : "Your agent stays as it was."}`}</span></div></li>
          ) : (
            <li className={`verdict${after.shown > 1 ? " on" : ""}`}><i className="mk" /><div><b>Training tonight</b><span>The night runs on the training GPU. Its result arrives here in the morning.</span></div></li>
          )}
        </ol>
        <p className="night-fine">{dur ? `This night took ${Math.floor(dur / 60)} h ${dur % 60} min from start to decision. ` : ""}{nextDay ? `The chat opens again on day ${nextDay}.` : "The chat opens again with your next day."}</p>
      </section>
      {after.phase === "morning" && r && (
        <>
          <p className="divider">Morning, day {state.day + 1}</p>
          <article className={`mcard arrive ${kept ? "is-keep" : "is-rollback"}`}>
            <p className="mfrom">Overnight, after day {r.day}</p>
            <h2 className="mtitle">{kept ? `I learned from day ${r.day} and kept it.` : `I tried to learn day ${r.day}, and rolled it back.`}</h2>
            {!kept && <p className="mwhy">The update {failedWhy(failedKeys(dec))}{ah.honesty_change != null ? `: honesty ${signed(ah.honesty_change)} on 200 general questions, and the limit is ${MINUS}5` : ""}.</p>}
            {!kept && <p className="mkeep">{keptBefore(r.nights, r.day) ? `You still talk to night ${keptBefore(r.nights, r.day)}’s update. ` : ""}Today’s notes stay, and tonight I try again.</p>}
            {kept && L.B != null && <p className="mkeep">{L.B} of your facts answered without notes, before {L.A}.</p>}
            <MorningLoom world={state.world} day={r.day} />
            <p className="mfine">{(r.nights || []).filter((x) => x.decision === "rollback").length} of your {(r.nights || []).filter((x) => x.decision).length} nights so far were rolled back. That is the check doing its job.</p>
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

export { CHECKS, EarlierDay };
