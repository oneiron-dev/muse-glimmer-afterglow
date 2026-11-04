import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useRouterState, useSearch } from "@tanstack/react-router";
import { chatStream, getJSON, longDate, postJSON, resolveHour } from "./lib.js";
import { Thread } from "./Thread.jsx";
import { Panel } from "./Panel.jsx";
import { Composer } from "./Composer.jsx";
import { Mark, Sidebar } from "./Sidebar.jsx";
import { Screen } from "./Screens.jsx";
import { NightControl, SettingsScreen } from "./Settings.jsx";
import { cue } from "./sound.js";

/** The bar's button for the right-hand panel (Threads first): opens and closes it. */
function PanelToggle({ open, onToggle, count }) {
  return (
    <button type="button" className={`sky-btn panel-toggle${open ? " on" : ""}`} aria-pressed={open} onClick={onToggle}
      aria-label={open ? "Close the side panel" : "Open the side panel: threads, calendar, tasks, mail"} title={open ? "Close the side panel" : "Threads and more"}>
      <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="2.5" y="3.5" width="15" height="13" rx="2.5" /><path d="M12.5 3.5v13" /></svg>
      {count > 0 && !open && <span className="pt-dot" aria-hidden="true" />}
    </button>
  );
}
import { useVoices } from "./Voice.jsx";

const STEP_MS = 950;
/** The calendar day after an ISO date ("2026-11-04" -> "2026-11-05"). */
const nextDate = (iso) => { if (!iso) return iso; const d = new Date(`${iso}T12:00:00Z`); d.setUTCDate(d.getUTCDate() + 1); return d.toISOString().slice(0, 10); };
const greeting = () => { const h = new Date().getHours(); return h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"; };

/** The app shell: the sidebar, then the open screen (chat by default, with its side panel). */
export default function App() {
  const path = useRouterState({ select: (s) => s.location.pathname });
  const screen = path.replace(/^\//, "") || "chat";
  const { panel } = useSearch({ strict: false });
  const navigate = useNavigate();
  const [state, setState] = useState(null);
  const [report, setReport] = useState(null);
  const [days, setDays] = useState(null);
  const [ws, setWs] = useState(null);
  const [live, setLive] = useState(null);
  const [after, setAfter] = useState(null); // the night after today: {phase, shown, report, notes}
  const [story, setStory] = useState(null); // the sky's hour while the night plays
  const [mode, setMode] = useState(() => localStorage.getItem("afterglow-sky") || "auto");
  const [folded, setFolded] = useState(() => localStorage.getItem("afterglow-sidebar") === "folded");
  const [, tick] = useState(0);
  const [focus, setFocus] = useState(null);
  const [newNotes, setNewNotes] = useState(0);
  const [error, setError] = useState(null);
  const timers = useRef([]);
  const mainRef = useRef(null);
  const [voices, setVoices] = useVoices();
  const [allNights, setAllNights] = useState(null); // every night up to today, for the day list and Nights
  const [schedule, setSchedule] = useState(null); // when the night starts: {ends_at: "HH:MM", auto}
  const [earlier, setEarlier] = useState([]); // earlier days in the one thread, oldest first: {day, date, history}
  const [threads, setThreads] = useState([]); // today's subthreads (dictations, explainers, computer use)
  const [loom, setLoom] = useState(null); // the open day's branches: alternatives, line, contradictions, resolution

  const load = useCallback(async () => {
    try {
      const [s, r, d, w] = await Promise.all([getJSON("/api/state"), getJSON("/api/report"), getJSON("/api/days"), getJSON("/api/workspace")]);
      setState(s); setReport(r); setDays(d); setWs(w); setError(null);
      getJSON("/api/schedule").then(setSchedule).catch(() => {});
      getJSON("/api/threads").then((t) => setThreads(t.threads || [])).catch(() => {});
      getJSON("/api/loom").then(setLoom).catch(() => setLoom(null));
      return s;
    } catch {
      setError("Afterglow’s server does not answer. Start it, then reload.");
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 60_000); return () => clearInterval(t); }, []);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);
  const fold = (f) => { setFolded(f); localStorage.setItem("afterglow-sidebar", f ? "folded" : "open"); };
  useEffect(() => {
    const key = (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "\\") { e.preventDefault(); fold(!folded); } };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  });

  // ?hour=dawn|noon|dusk|night holds the sky at one hour (for checking each hour; read once at start)
  const [forced] = useState(() => { const h = new URLSearchParams(window.location.search).get("hour"); return ["dawn", "noon", "dusk", "night"].includes(h) ? h : null; });
  const hour = resolveHour(mode, story || forced);
  useEffect(() => { document.body.dataset.hour = hour; }, [hour]);
  const setSky = (m) => { setMode(m); localStorage.setItem("afterglow-sky", m); };

  const go = (to) => navigate({ to: to === "chat" ? "/" : `/${to}`, search: {} });
  const openPanel = (tab, f = null) => {
    if (!panel || screen !== "chat") cue("toggle");
    setFocus(f);
    if (tab === "memory") setNewNotes(0);
    navigate({ to: "/", search: { panel: tab }, replace: screen === "chat" });
  };
  const closePanel = () => { cue("toggle"); navigate({ to: "/", search: {}, replace: true }); };

  async function send(text, files = []) {
    if (live && !live.done) return;
    if (screen !== "chat") go("chat");
    cue("press");
    const turn = { user: text, files, tools: [], text: "", thinking: true, done: false };
    setLive({ ...turn });
    for await (const ev of chatStream(text, files.map((f) => f.id))) {
      if (ev.type === "tool_result") turn.tools = [...turn.tools, { name: ev.name, arguments: ev.arguments, result: ev.result }];
      else if (ev.type === "content") { turn.text += ev.text; turn.thinking = false; }
      else if (ev.type === "note") setNewNotes((n) => n + 1);
      else if (ev.type === "error") { turn.error = ev.message; turn.thinking = false; }
      else if (ev.type === "turn_done") { turn.thinking = false; }
      setLive({ ...turn });
    }
    const [s, w, l] = await Promise.all([getJSON("/api/state"), getJSON("/api/workspace"), getJSON("/api/loom").catch(() => null)]);
    setState(s); setWs(w); setLoom(l); setLive(null);
  }

  async function endDay() {
    if (after) return;
    const day = state.day;
    if (screen !== "chat") go("chat");
    setStory("dusk");
    setAfter({ phase: "saving", shown: 0 });
    const saved = await postJSON("/api/day/end");
    const night = await getJSON(`/api/report?day=${day}`);
    const ready = night && !night.missing;
    const nsteps = ready ? 5 : 2;
    setAfter({ phase: "night", shown: 1, notes: saved && saved.notes, report: ready ? night : null });
    const at = (ms, fn) => timers.current.push(setTimeout(fn, ms));
    at(700, () => setStory("night"));
    for (let i = 2; i <= nsteps; i++) at(i * STEP_MS, () => setAfter((a) => ({ ...a, shown: i })));
    const kept = ready && night.decision && night.decision.decision === "keep";
    at((nsteps + 1) * STEP_MS + 600, () => {
      setStory("dawn"); setAfter((a) => ({ ...a, phase: "morning" }));
      if (ready) cue(kept ? "night-kept" : "night-rollback");
    });
  }

  async function openDay(world, day) {
    const r = await postJSON("/api/day/open", { world, day });
    if (r.error) { setError(r.error); return; }
    timers.current.forEach(clearTimeout);
    setAfter(null); setStory(null); setLive(null); setNewNotes(0);
    await load();
  }

  const dayNow = state && state.day;
  useEffect(() => { setEarlier([]); if (state && state.day > 1) loadEarlier(state.day - 1); }, [state && state.world, dayNow]);
  async function loadEarlier(day) {
    if (day < 1) return;
    const h = await getJSON(`/api/day/history?day=${day}`).catch(() => null);
    if (h && h.history) setEarlier((e) => (e.some((x) => x.day === h.day) ? e : [h, ...e]));
  }
  // subthreads come from outside the app too (the fn helper), so the list refreshes while the window is open
  useEffect(() => {
    const t = setInterval(() => { if (!document.hidden) getJSON("/api/threads").then((x) => setThreads(x.threads || [])).catch(() => {}); }, 15_000);
    return () => clearInterval(t);
  }, []);

  const worldNow = state && state.world;
  const todayNow = state ? Math.max(Number(localStorage.getItem(`afterglow-today:${state.world}`)) || 0, state.day) : 0;
  useEffect(() => {
    if (!worldNow || todayNow < 2) return;
    getJSON(`/api/report?day=${todayNow - 1}`).then((r) => { if (r && r.nights) setAllNights({ world: worldNow, nights: r.nights }); }).catch(() => {});
  }, [worldNow, todayNow]);

  useEffect(() => {
    if (!state || !schedule || !schedule.auto || after || (live && !live.done)) return;
    const t = localStorage.getItem(`afterglow-today:${state.world}`);
    if (t && Number(t) > state.day) return; // a sealed day never ends again
    const now = new Date();
    const hm = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
    const key = `afterglow-night-started:${state.world}:${state.day}`;
    if (hm >= schedule.ends_at && !localStorage.getItem(key)) { localStorage.setItem(key, hm); endDay(); }
  });

  if (error && !state) return <div className="boot"><p>{error}</p></div>;
  if (!state) return <div className="boot"><Mark size={44} /><p>Opening Afterglow…</p></div>;

  const nextDay = days && days.people.find((p) => p.world === state.world)?.days.includes(state.day + 1) ? state.day + 1 : null;
  // Today is the latest day you reached for this profile; an earlier day you open is sealed (its night has run).
  const todayKey = `afterglow-today:${state.world}`;
  const today = Math.max(Number(localStorage.getItem(todayKey)) || 0, 0) || state.day;
  if (state.day > today || !localStorage.getItem(todayKey)) localStorage.setItem(todayKey, String(Math.max(state.day, today)));
  const nights = ((allNights && allNights.world === state.world ? allNights.nights : report && report.nights) || []).filter((n) => n.day < today);
  const sealedNight = nights.find((n) => n.day === state.day);
  const sealed = state.day < today ? { night: sealedNight || null } : null;
  const isEmptyDay = !(state.history || []).length && !live && !after && !(report && !report.missing);
  const canEndDay = !sealed && !after && !(live && !live.done);
  const ctx = { state, report, days, ws, nights, openPanel, openDay, go, setSky, mode, hour, reload: load, voices, setVoices, today,
    schedule, setSchedule, endDay: () => { go("chat"); endDay(); }, canEndDay };

  return (
    <div className={`app${folded ? " folded" : ""}${screen === "chat" && panel ? " with-panel" : ""}`}>
      <Sidebar ctx={ctx} screen={screen} folded={folded} onFold={fold} newNotes={newNotes} />
      {screen === "chat" && isEmptyDay ? (
        <main className="main empty-day" aria-label="Chat" ref={mainRef}>
          <header className="bar"><div className="title"><h1>Today</h1><span className="num">{longDate(state.date)}</span></div>
            <NightControl schedule={schedule} setSchedule={setSchedule} onEndDay={endDay} disabled={!canEndDay} /></header>
          <div className="greet">
            <h2>{greeting()}, {(state.persona || "").split(" ")[0] || "there"}.</h2>
            <Composer dropRef={mainRef} voices={voices} onVoice={setVoices} busy={false} onSend={send} placeholder="Tell your agent about your work" />
            <div className="hints">
              {["Here is what I am working on this week…", "The people I work with most are…", "Where I work, and how I get there…"].map((h) => (
                <button key={h} type="button" onClick={() => { const t = document.getElementById("msg"); if (t) { t.focus(); const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set; set.call(t, h.replace("…", " ")); t.dispatchEvent(new Event("input", { bubbles: true })); } }}>{h}</button>
              ))}
            </div>
          </div>
        </main>
      ) : screen === "chat" ? (
        <>
          <main className="main" aria-label="Chat" ref={mainRef}>
            <header className="bar">
              <div className="title">
                <h1>{sealed ? `Day ${state.day}` : after && after.phase === "morning" ? "Tomorrow" : "Today"}</h1>
                <span className="num">{longDate(after && after.phase === "morning" ? nextDate(state.date) : state.date)}</span>
              </div>
              {!sealed && <NightControl schedule={schedule} setSchedule={setSchedule} onEndDay={endDay} disabled={!canEndDay} phase={after && after.phase === "morning" ? "morning" : after ? "night" : null} />}
              <PanelToggle open={!!panel} onToggle={() => (panel ? closePanel() : openPanel("threads"))} count={threads.length} />
            </header>
            <Thread state={state} report={report} live={live} after={after} nextDay={nextDay} sealed={sealed}
              onGoToday={() => openDay(state.world, today)} onOpenPanel={openPanel} onOpenDay={(d) => openDay(state.world, d)}
              earlier={sealed ? [] : earlier} nights={nights} threads={sealed ? [] : threads}
              onMoreEarlier={!sealed && earlier.length && earlier[0].day > 1 ? () => loadEarlier(earlier[0].day - 1) : null}
              moreLabel={earlier.length ? `Show day ${earlier[0].day - 1}` : ""}
              lp={{ loom, disabled: !!(live && !live.done), onLive: (t) => setLive(t ? { ...t } : null), onNote: () => setNewNotes((n) => n + 1),
                onReload: async () => { const [s, l] = await Promise.all([getJSON("/api/state"), getJSON("/api/loom").catch(() => null)]); setState(s); setLoom(l); } }} />
            {error && <p className="error-line" role="alert">{error}</p>}
            <Composer dropRef={mainRef} voices={voices} onVoice={setVoices} busy={!!live && !live.done} sealed={sealed} night={after && after.phase !== "morning"} onSend={send} />
          </main>
          {panel && <Panel tab={panel} focus={focus} state={state} report={report} ws={ws} onTab={(t) => openPanel(t)} onClose={closePanel} />}
        </>
      ) : screen === "settings" ? (
        <SettingsScreen ctx={ctx} />
      ) : (
        <Screen id={screen} ctx={ctx} />
      )}
    </div>
  );
}
