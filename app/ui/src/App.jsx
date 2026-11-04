import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useRouterState, useSearch } from "@tanstack/react-router";
import { chatStream, getJSON, longDate, postJSON, resolveHour } from "./lib.js";
import { Thread } from "./Thread.jsx";
import { Panel } from "./Panel.jsx";
import { Composer } from "./Composer.jsx";
import { Sidebar } from "./Sidebar.jsx";
import { Screen } from "./Screens.jsx";

const STEP_MS = 950;

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

  const load = useCallback(async () => {
    try {
      const [s, r, d, w] = await Promise.all([getJSON("/api/state"), getJSON("/api/report"), getJSON("/api/days"), getJSON("/api/workspace")]);
      setState(s); setReport(r); setDays(d); setWs(w); setError(null);
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

  const hour = resolveHour(mode, story);
  useEffect(() => { document.body.dataset.hour = hour; }, [hour]);
  const setSky = (m) => { setMode(m); localStorage.setItem("afterglow-sky", m); };

  const go = (to) => navigate({ to: to === "chat" ? "/" : `/${to}`, search: {} });
  const openPanel = (tab, f = null) => {
    setFocus(f);
    if (tab === "memory") setNewNotes(0);
    navigate({ to: "/", search: { panel: tab }, replace: screen === "chat" });
  };
  const closePanel = () => navigate({ to: "/", search: {}, replace: true });

  async function send(text) {
    if (live && !live.done) return;
    if (screen !== "chat") go("chat");
    const turn = { user: text, tools: [], text: "", thinking: true, done: false };
    setLive({ ...turn });
    for await (const ev of chatStream(text)) {
      if (ev.type === "tool_result") turn.tools = [...turn.tools, { name: ev.name, arguments: ev.arguments, result: ev.result }];
      else if (ev.type === "content") { turn.text = ev.text; turn.thinking = false; }
      else if (ev.type === "note") setNewNotes((n) => n + 1);
      else if (ev.type === "error") { turn.error = ev.message; turn.thinking = false; }
      else if (ev.type === "turn_done") { turn.thinking = false; }
      setLive({ ...turn });
    }
    const [s, w] = await Promise.all([getJSON("/api/state"), getJSON("/api/workspace")]);
    setState(s); setWs(w); setLive(null);
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
    at((nsteps + 1) * STEP_MS + 600, () => { setStory("dawn"); setAfter((a) => ({ ...a, phase: "morning" })); });
  }

  async function openDay(world, day) {
    const r = await postJSON("/api/day/open", { world, day });
    if (r.error) { setError(r.error); return; }
    timers.current.forEach(clearTimeout);
    setAfter(null); setStory(null); setLive(null); setNewNotes(0);
    await load();
  }

  if (error && !state) return <div className="boot"><p>{error}</p></div>;
  if (!state) return <div className="boot"><img src="/afterglow-icon.svg" alt="" width="48" height="48" /><p>Opening Afterglow…</p></div>;

  const nextDay = days && days.people.find((p) => p.world === state.world)?.days.includes(state.day + 1) ? state.day + 1 : null;
  // Today is the latest day you reached for this profile; an earlier day you open is sealed (its night has run).
  const todayKey = `afterglow-today:${state.world}`;
  const today = Math.max(Number(localStorage.getItem(todayKey)) || 0, 0) || state.day;
  if (state.day > today || !localStorage.getItem(todayKey)) localStorage.setItem(todayKey, String(Math.max(state.day, today)));
  const sealedNight = (report && report.nights || []).find((n) => n.day === state.day);
  const sealed = state.day < today ? { night: sealedNight || null } : null;
  const nights = ((report && report.nights) || []).filter((n) => n.day < state.day);
  const ctx = { state, report, days, ws, nights, openPanel, openDay, go, setSky, mode, hour, reload: load };

  return (
    <div className={`app${folded ? " folded" : ""}${screen === "chat" && panel ? " with-panel" : ""}`}>
      <Sidebar ctx={ctx} screen={screen} folded={folded} onFold={fold} newNotes={newNotes} />
      {screen === "chat" ? (
        <>
          <main className="main" aria-label="Chat">
            <header className="bar">
              <div className="title">
                <h1>{sealed ? `Day ${state.day}` : after && after.phase === "morning" ? "Tomorrow" : "Today"}</h1>
                <span className="num">{longDate(state.date)}</span>
              </div>
              {!sealed && <button type="button" className="btn btn-end" onClick={endDay} disabled={!!after || (live && !live.done)}>
                {after ? (after.phase === "morning" ? "Morning" : "Night") : "End the day"}
              </button>}
            </header>
            <Thread state={state} report={report} live={live} after={after} nextDay={nextDay} sealed={sealed}
              onGoToday={() => openDay(state.world, today)} onOpenPanel={openPanel} onOpenDay={(d) => openDay(state.world, d)} />
            {error && <p className="error-line" role="alert">{error}</p>}
            <Composer busy={!!live && !live.done} sealed={sealed} night={after && after.phase !== "morning"} onSend={send} />
          </main>
          {panel && <Panel tab={panel} focus={focus} state={state} report={report} ws={ws} onTab={(t) => openPanel(t)} onClose={closePanel} />}
        </>
      ) : (
        <Screen id={screen} ctx={ctx} />
      )}
    </div>
  );
}
