import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { chatStream, getJSON, postJSON, resolveHour } from "./lib.js";
import { Thread } from "./Thread.jsx";
import { Panel } from "./Panel.jsx";
import { Composer } from "./Composer.jsx";
import { PersonMenu, ThemeToggle } from "./Header.jsx";

const HOURS = ["dawn", "noon", "dusk", "night"];
const STEP_MS = 950;

export default function App() {
  const { panel } = useSearch({ from: "/" });
  const navigate = useNavigate();
  const [state, setState] = useState(null);
  const [report, setReport] = useState(null);
  const [days, setDays] = useState(null);
  const [ws, setWs] = useState(null);
  const [live, setLive] = useState(null);
  const [after, setAfter] = useState(null); // the night after today: {phase, steps shown, report, notes}
  const [story, setStory] = useState(null); // the sky's hour while the night plays
  const [mode, setMode] = useState(() => localStorage.getItem("afterglow-sky") || "auto");
  const [, tick] = useState(0);
  const [focus, setFocus] = useState(null); // what the panel shows first: an event, task or draft id, or web results
  const [newNotes, setNewNotes] = useState(0);
  const [error, setError] = useState(null);
  const timers = useRef([]);

  const load = useCallback(async () => {
    try {
      const [s, r, d, w] = await Promise.all([getJSON("/api/state"), getJSON("/api/report"), getJSON("/api/days"), getJSON("/api/workspace")]);
      setState(s); setReport(r); setDays(d); setWs(w); setError(null);
      return s;
    } catch (e) {
      setError("Afterglow’s server does not answer. Start it, then reload.");
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 60_000); return () => clearInterval(t); }, []);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  const hour = resolveHour(mode, story);
  useEffect(() => { document.body.dataset.hour = hour; }, [hour]);
  const setSky = (m) => { setMode(m); localStorage.setItem("afterglow-sky", m); };

  const openPanel = (tab, f = null) => { setFocus(f); navigate({ search: { panel: tab }, replace: true }); if (tab === "memory") setNewNotes(0); };
  const closePanel = () => navigate({ search: {}, replace: true });

  async function send(text) {
    if (live && !live.done) return;
    const turn = { user: text, tools: [], text: "", thinking: true, done: false };
    setLive({ ...turn });
    for await (const ev of chatStream(text)) {
      if (ev.type === "tool_result") turn.tools = [...turn.tools, { name: ev.name, arguments: ev.arguments, result: ev.result }];
      else if (ev.type === "content") { turn.text = ev.text; turn.thinking = false; }
      else if (ev.type === "note") setNewNotes((n) => n + 1);
      else if (ev.type === "error") { turn.error = ev.message; turn.thinking = false; }
      else if (ev.type === "turn_done") { turn.tok_s = ev.tok_s; turn.thinking = false; }
      setLive({ ...turn });
    }
    const [s, w] = await Promise.all([getJSON("/api/state"), getJSON("/api/workspace")]);
    setState(s); setWs(w); setLive(null);
  }

  async function endDay() {
    if (after) return;
    const day = state.day;
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
  if (!state) return <div className="boot"><p>Opening Afterglow…</p></div>;

  const person = state.persona || state.world;
  const first = person.split(" ")[0];
  const nextDay = days && days.people.find((p) => p.world === state.world)?.days.includes(state.day + 1) ? state.day + 1 : null;

  return (
    <>
      <div className="world" aria-hidden="true">
        {HOURS.map((h) => <img key={h} className={`sky${h === hour ? " on" : ""}`} src={`/sky/hour-${h}.webp`} alt="" />)}
      </div>
      <span className="wordmark">Afterglow</span>
      <div className={`stage${panel ? " with-panel" : ""}`}>
        <main className="well thread-well" aria-label={`${person}’s agent`}>
          <header className="bar">
            <PersonMenu state={state} days={days} onOpen={openDay} />
            {!panel && <nav className="tools" aria-label="Open">
              <button type="button" className={`tool${panel === "calendar" ? " on" : ""}`} onClick={() => openPanel("calendar")}>Calendar</button>
              <button type="button" className={`tool${panel === "tasks" ? " on" : ""}`} onClick={() => openPanel("tasks")}>Tasks</button>
              <button type="button" className={`tool${panel === "email" ? " on" : ""}`} onClick={() => openPanel("email")}>Email</button>
              <button type="button" className={`tool${panel === "memory" ? " on" : ""}`} onClick={() => openPanel("memory")}>
                Memory{newNotes > 0 && <span className="badge num" aria-label={`${newNotes} new notes`}>+{newNotes}</span>}
              </button>
            </nav>}
            <ThemeToggle mode={mode} onChange={setSky} />
            <button type="button" className="btn btn-end" onClick={endDay} disabled={!!after || (live && !live.done)}>
              {after ? (after.phase === "morning" ? "Morning" : "Night") : "End the day"}
            </button>
          </header>
          <Thread state={state} report={report} live={live} after={after} nextDay={nextDay} first={first}
            onOpenPanel={openPanel} onOpenDay={(d) => openDay(state.world, d)} />
          {error && <p className="error-line" role="alert">{error}</p>}
          <Composer first={first} busy={!!live && !live.done} night={after && after.phase !== "morning"} onSend={send} />
        </main>
        {panel && (
          <Panel tab={panel} focus={focus} state={state} report={report} ws={ws} first={first}
            onTab={(t) => openPanel(t)} onClose={closePanel} />
        )}
      </div>
    </>
  );
}
