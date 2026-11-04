import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { Icon } from "./Sidebar.jsx";
import { VoiceList } from "./Voice.jsx";
import { Onboarding } from "./Onboarding.jsx";
import { CHECKS, MINUS, getJSON, postJSON, pretty, shortDate } from "./lib.js";
import { cue, setSound, soundOn } from "./sound.js";

/** Settings' own section list; the sidebar shows it in place of the app's screens (as the Claude Console does). */
export const SECTIONS = [
  { id: "model", label: "Model" },
  { id: "connectors", label: "Connectors" },
  { id: "voice", label: "Voice" },
  { id: "nights", label: "Nights" },
  { id: "appearance", label: "Appearance" },
  "rule",
  { id: "profile", label: "Profile" },
];
const IDS = SECTIONS.filter((s) => s !== "rule").map((s) => s.id);
export const sectionOf = (s) => (IDS.includes(s) ? s : "model");

/** The sidebar in Settings: back to the app, then the sections. */
export function SettingsNav({ section, folded, onBack }) {
  const navigate = useNavigate();
  return (
    <>
      <div className="side-top set-top">
        <button type="button" className="sky-btn back-btn" onClick={onBack} aria-label="Back to the app" title="Back to the app">
          <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="M16 10H4.5M9 5 4 10l5 5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
        {!folded && <span className="set-top-name">Settings</span>}
      </div>
      <ul className="side-list nav">
        {SECTIONS.map((s, i) => s === "rule" ? <li key={`r${i}`} className="side-rule" aria-hidden="true" /> : (
          <li key={s.id}>
            <button type="button" className={`side-row set-row${section === s.id ? " on" : ""}`} aria-current={section === s.id ? "page" : undefined}
              onClick={() => navigate({ to: "/settings", search: { section: s.id }, replace: true })} title={folded ? s.label : undefined}>
              {folded ? <span className="set-initial">{s.label[0]}</span> : <span className="side-name">{s.label}</span>}
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

/** Settings: one centred form column per section. */
export function SettingsScreen({ ctx }) {
  const { section } = useSearch({ strict: false });
  const sec = sectionOf(section);
  const [s, setS] = useState(null);
  const loadSettings = () => getJSON("/api/settings").then(setS).catch(() => setS(null));
  useEffect(() => { loadSettings(); }, []);
  const title = SECTIONS.find((x) => x.id === sec).label;
  return (
    <main className="main screen settings-main" aria-label={`Settings: ${title}`}>
      <div className="screen-body">
        <div className="form-col" key={sec}>
          <h1 className="form-title">{title}</h1>
          {sec === "model" && <ModelSection ctx={ctx} settings={s} />}
          {sec === "connectors" && <ConnectorsSection settings={s} />}
          {sec === "voice" && <VoiceSection ctx={ctx} />}
          {sec === "nights" && <NightsSection ctx={ctx} settings={s} onSaved={loadSettings} />}
          {sec === "appearance" && <AppearanceSection ctx={ctx} />}
          {sec === "profile" && <ProfileSection ctx={ctx} settings={s} />}
        </div>
      </div>
    </main>
  );
}

export function Switch({ on, onChange, label, disabled }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} className={`switch${on ? " on" : ""}`} disabled={disabled}
      onClick={() => { cue("toggle"); onChange(!on); }}><i /></button>
  );
}

function Row({ title, children, action }) {
  return (
    <div className="frow">
      <div className="frow-text"><b>{title}</b>{children && <p>{children}</p>}</div>
      {action && <div className="frow-act">{action}</div>}
    </div>
  );
}

const Mark = ({ ok }) => <i className={`fmark ${ok === true ? "ok" : ok === false ? "no" : "info"}`} aria-hidden="true" />;

/** Plain sentences with a dot each: green for what works, rose for what does not, grey for a plain fact. */
function Lines({ lines }) {
  if (!lines || !lines.length) return null;
  return (
    <ul className="flines" role="status">
      {lines.map((l, i) => {
        const bad = /cannot|do not|not installed|No server|No file|Not a model|not Muse|did not|not serve/i.test(l);
        const plain = /server at |^Muse|GB\.$/.test(l);
        return <li key={i}><Mark ok={bad ? false : plain ? undefined : true} />{l}</li>;
      })}
    </ul>
  );
}

/* ---------------- Model ---------------- */

const PRESETS = [["LM Studio", "http://127.0.0.1:1234"], ["Ollama", "http://127.0.0.1:11434"], ["llama.cpp", "http://127.0.0.1:8080"]];
const fmtGB = (b) => `${(b / 1e9).toFixed(1)} GB`;

function ModelSection({ ctx, settings }) {
  const [m, setM] = useState(null);
  const [cur, setCur] = useState(null); // the probe of the server in use
  const [way, setWay] = useState("server");
  const load = async () => { const r = await getJSON("/api/model"); setM(r); return r; };
  useEffect(() => { load().then((r) => r && postJSON("/api/model/probe", { url: r.current.url }).then(setCur)); }, []);
  const busy = m && (m.download.state === "downloading" || m.download.state === "verifying" || m.current.starting);
  useEffect(() => { if (!busy) return; const t = setInterval(load, 1500); return () => clearInterval(t); }, [busy]);
  if (!m) return <p className="pfine">Reading this computer…</p>;
  const hw = m.hardware;
  const usable = hw.usable_gb || (hw.gpus && hw.gpus.length ? Math.max(...hw.gpus.map((g) => g.memory_gb)) : null);
  const adapter = ctx.state.server && ctx.state.server.adapter;
  const switched = (r) => { setM(r); if (r.probe) setCur(r.probe); ctx.reload(); };
  return (
    <>
      <section className="fsec" aria-label="In use">
        <div className={`inuse${m.current.healthy ? "" : " off"}`}>
          <span className="inuse-dot" aria-hidden="true" />
          <div>
            <b>{cur && cur.model ? (cur.glimmer ? "Muse Glimmer 30B" : cur.model) : "Muse Glimmer 30B"}</b>
            <p className="num">{m.current.healthy ? `${cur ? cur.label : m.current.kind} at ${m.current.url.replace(/^https?:\/\//, "")}` : `Not answering at ${m.current.url.replace(/^https?:\/\//, "")}`}{m.current.starting ? " (starting)" : ""}</p>
          </div>
        </div>
        {cur && <Lines lines={cur.lines.slice(1)} />}
        {m.current.error && <p className="ferr" role="alert">{m.current.error}</p>}
        <dl className="kv">
          <div><dt>Update in use</dt><dd>{adapter || "Base weights"}</dd></div>
          {settings && settings.serving && settings.serving.why && <div><dt>Why this one</dt><dd>{settings.serving.why}</dd></div>}
        </dl>
      </section>

      <section className="fsec">
        <h2 className="fh">This computer</h2>
        <p className="fp">{hw.memory_line}.</p>
        {usable ? (
          <div className="fit" role="img" aria-label={`Glimmer needs about ${hw.serve_gb} GB of the ${usable} GB the GPU may use`}>
            <div className="fit-bar"><i style={{ width: `${Math.min(100, (hw.serve_gb / usable) * 100)}%` }} /></div>
            <div className="fit-cap num"><span>Glimmer 30B, about {hw.serve_gb} GB</span><span>{usable} GB</span></div>
          </div>
        ) : null}
        <ul className="flines">
          <li><Mark ok={hw.serve} />{hw.serve_line}</li>
          <li><Mark ok={hw.nights ? true : undefined} />{hw.nights_line}</li>
        </ul>
      </section>

      <section className="fsec">
        <h2 className="fh">Use another model</h2>
        <div className="seg" role="tablist" aria-label="Where the model comes from">
          {[["server", "A server"], ["file", "A file"], ["download", "Download"]].map(([id, label]) => (
            <button key={id} type="button" role="tab" aria-selected={way === id} className={way === id ? "on" : ""} onClick={() => setWay(id)}>{label}</button>
          ))}
        </div>
        {way === "server" && <ServerWay current={m.current.url} onUse={switched} />}
        {way === "file" && <FileWay m={m} onUse={switched} />}
        {way === "download" && <DownloadWay m={m} reload={load} onUse={switched} />}
      </section>

      <details className="fmore">
        <summary>Details</summary>
        <dl className="kv">
          <div><dt>Thinking</dt><dd>{settings ? settings.reasoning : ""}</dd></div>
          <div><dt>Chat slot</dt><dd className="num">{settings ? settings.slot : ""}</dd></div>
          <div><dt>Saved choice</dt><dd className="num">{m.saved ? `${m.saved.source}: ${m.saved.url || m.saved.path}` : "None (the address the app started with)"}</dd></div>
          <div><dt>Model files go to</dt><dd className="num">{m.models_dir}</dd></div>
        </dl>
      </details>
    </>
  );
}

function ServerWay({ current, onUse }) {
  const [url, setUrl] = useState("");
  const [pr, setPr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const check = async (e) => {
    e && e.preventDefault();
    if (!url.trim()) return;
    setBusy(true); setErr(null); setPr(null);
    const r = await postJSON("/api/model/probe", { url });
    setBusy(false);
    if (r.error) setErr(r.error); else setPr(r);
  };
  const use = async () => {
    cue("press");
    setBusy(true);
    const r = await postJSON("/api/model/use", { source: "server", url: pr.url });
    setBusy(false);
    if (r.error) setErr(r.error); else { onUse(r); setPr(null); setUrl(""); }
  };
  return (
    <div className="way">
      <form className="ffield" onSubmit={check}>
        <label htmlFor="srv-url">Server address</label>
        <p className="fhint">Any OpenAI-compatible server. Chat works on all of them; the nightly updates need llama.cpp’s llama-server.</p>
        <div className="finline">
          <input id="srv-url" className="finput num" value={url} onChange={(e) => { setUrl(e.target.value); setPr(null); }} placeholder={current} spellCheck="false" autoComplete="off" />
          <button type="submit" className="btn btn-quiet" disabled={!url.trim() || busy}>{busy && !pr ? "Checking…" : "Check"}</button>
        </div>
        <div className="fchips">
          {PRESETS.map(([name, u]) => <button key={name} type="button" className="fchip" onClick={() => { setUrl(u); setPr(null); }}>{name}<span className="num">{u.replace("http://127.0.0.1", "")}</span></button>)}
        </div>
      </form>
      {pr && <Lines lines={pr.lines} />}
      {err && <p className="ferr" role="alert">{err}</p>}
      {pr && pr.chat && <div className="factions"><button type="button" className="btn btn-send" onClick={use} disabled={busy}>Use this server</button></div>}
    </div>
  );
}

function FileWay({ m, onUse }) {
  const [path, setPath] = useState("");
  const [info, setInfo] = useState(null);
  const [found, setFound] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { getJSON("/api/model/local").then((r) => setFound(r.files || [])).catch(() => setFound([])); }, []);
  const check = async (p = path) => {
    if (!p.trim()) return;
    setErr(null); setInfo(null);
    const r = await postJSON("/api/model/file", { path: p });
    if (r.error) setErr(r.error); else setInfo(r);
  };
  const start = async () => {
    cue("press");
    setBusy(true);
    const r = await postJSON("/api/model/use", { source: "gguf", path: info.path });
    setBusy(false);
    if (r.error) setErr(r.error); else onUse(r);
  };
  return (
    <div className="way">
      <form className="ffield" onSubmit={(e) => { e.preventDefault(); check(); }}>
        <label htmlFor="gguf-path">Model file</label>
        <p className="fhint">A .gguf file on this computer. Afterglow starts llama-server on it.</p>
        <div className="finline">
          <input id="gguf-path" className="finput num" value={path} onChange={(e) => { setPath(e.target.value); setInfo(null); }} placeholder={`${m.models_dir}/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf`} spellCheck="false" autoComplete="off" />
          <button type="submit" className="btn btn-quiet" disabled={!path.trim()}>Check</button>
        </div>
      </form>
      {found && found.length > 0 && (
        <div className="found">
          <p className="fhint">Found on this computer</p>
          <ul>{found.map((f) => <li key={f.path}><button type="button" onClick={() => { setPath(f.path); check(f.path); }}><b>{f.name}</b><span className="num">{f.size_gb} GB · {f.path.replace(f.name, "")}</span></button></li>)}</ul>
        </div>
      )}
      {found && found.length === 0 && <p className="fhint">No Glimmer file found in the usual folders (LM Studio, llama.cpp, the Hugging Face cache, {m.models_dir}).</p>}
      {info && <Lines lines={info.lines} />}
      {err && <p className="ferr" role="alert">{err}</p>}
      {info && info.ok && (
        <div className="factions">
          <button type="button" className="btn btn-send" onClick={start} disabled={busy || !m.llama_server}>Start and use it</button>
          <span className="fhint">{m.llama_server ? <>Runs <code>{m.llama_server}</code> on this file.</> : <>llama-server is not installed. On a Mac: <code>brew install llama.cpp</code></>}</span>
        </div>
      )}
      {m.local_server.running && <p className="fhint">llama-server is running for <code>{m.current.path}</code>.</p>}
    </div>
  );
}

function DownloadWay({ m, reload, onUse }) {
  const [files, setFiles] = useState(null);
  const [pick, setPick] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    getJSON("/api/model/hf").then((r) => { if (r.error) setErr(r.error); else { setFiles(r.files); setPick(r.files[0] && r.files[0].file); } }).catch(() => setErr("Hugging Face did not answer."));
  }, []);
  const d = m.download;
  const start = async () => { cue("press"); setErr(null); const r = await postJSON("/api/model/download", { file: pick }); if (r.error) setErr(r.error); reload(); };
  const pause = async () => { await postJSON("/api/model/download/pause"); reload(); };
  const use = async () => { cue("press"); const r = await postJSON("/api/model/use", { source: "gguf", path: d.path }); if (r.error) setErr(r.error); else onUse(r); };
  const running = d.state === "downloading" || d.state === "verifying";
  return (
    <div className="way">
      <p className="fhint">From Meta’s own repository, <a href={`https://huggingface.co/${m.hf_repo}`} target="_blank" rel="noreferrer" className="num">{m.hf_repo}</a>. Each file is checked against its published checksum.</p>
      {!files && !err && <p className="fhint">Asking Hugging Face…</p>}
      {files && (
        <div className="v-list" role="radiogroup" aria-label="Model file">
          {files.map((f) => (
            <button key={f.file} type="button" role="radio" aria-checked={pick === f.file} className={`v-row${pick === f.file ? " on" : ""}`} onClick={() => setPick(f.file)} disabled={running}>
              <i className="v-radio" aria-hidden="true" />
              <span><b className="num">{f.file}</b><small>{f.note} {f.size_gb} GB.</small></span>
            </button>
          ))}
        </div>
      )}
      {d.state !== "idle" && d.total ? (
        <div className="dl" role="status">
          <div className="fit-bar"><i style={{ width: `${(d.done / d.total) * 100}%` }} /></div>
          <div className="fit-cap num"><span>{d.state === "verifying" ? "Checking the file" : d.state === "done" ? "Done" : `${fmtGB(d.done)} of ${fmtGB(d.total)}`}{d.state === "downloading" && d.rate_mb_s ? `, ${d.rate_mb_s} MB/s` : ""}</span><span>{d.line || ""}</span></div>
        </div>
      ) : null}
      {err && <p className="ferr" role="alert">{err}</p>}
      <div className="factions">
        {running ? <button type="button" className="btn btn-quiet" onClick={pause} disabled={d.state === "verifying"}>Pause</button>
          : d.state === "done" ? <button type="button" className="btn btn-send" onClick={use} disabled={!m.llama_server}>Start and use it</button>
            : <button type="button" className="btn btn-send" onClick={start} disabled={!pick}>{d.state === "paused" || d.state === "failed" ? "Resume" : "Download"}</button>}
        <span className="fhint">Saves to <code>{m.models_dir}</code></span>
      </div>
    </div>
  );
}

/* ---------------- Connectors ---------------- */

const CONNECTORS = [
  { id: "calendar", icon: "calendar", name: "Calendar", tools: ["calendar.list_events", "calendar.add_event"], line: "Your agent’s calendar, kept on this computer. It reads and adds events." },
  { id: "tasks", icon: "tasks", name: "Tasks", tools: ["tasks.list", "tasks.add", "tasks.complete"], line: "Your to-dos, kept on this computer. It adds them and marks them done." },
  { id: "mail", icon: "mail", name: "Mail", tools: ["email.draft"], line: "Drafts only. Your agent writes a draft; you send it from your mail app." },
];

function ConnectorsSection({ settings }) {
  if (!settings) return <p className="pfine">Loading…</p>;
  const on = (tools) => tools.some((t) => settings.tools.includes(t));
  const ws = settings.web_search || {};
  const capEnded = ws.cap && ws.cap.through && new Date(`${ws.cap.through}T23:59:59+09:00`) < new Date();
  const wsLine = !ws.on ? "Off. When on, only the search words leave this computer, never your notes."
    : capEnded ? `Paused: its spending cap ran through ${shortDate(ws.cap.through)}. ${ws.used} searches were made.`
      : `Exa. Only the search words leave this computer. ${ws.used}${ws.cap && ws.cap.searches ? ` of ${ws.cap.searches}` : ""} searches used${ws.cap && ws.cap.through ? `, through ${shortDate(ws.cap.through)}` : ""}.`;
  return (
    <>
      <p className="fp">What your agent can reach. Each one is tested with the nights before it is offered, because a new connector changes what the nights are checked on.</p>
      <ul className="conns">
        {CONNECTORS.map((c) => (
          <li key={c.id}>
            <span className="conn-icon"><Icon id={c.icon} /></span>
            <div><b>{c.name}</b><p>{c.line}</p></div>
            <span className={`conn-state${on(c.tools) ? " on" : ""}`}>{on(c.tools) ? "On" : "Off"}</span>
          </li>
        ))}
        <li>
          <span className="conn-icon"><svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><circle cx="10" cy="10" r="7" /><path d="M3 10h14M10 3c2 2.2 2.8 4.5 2.8 7s-.8 4.8-2.8 7c-2-2.2-2.8-4.5-2.8-7S8 5.2 10 3Z" /></svg></span>
          <div><b>Web search</b><p>{wsLine}</p></div>
          <span className={`conn-state${ws.on && !capEnded ? " on" : ""}`}>{ws.on && !capEnded ? "On" : capEnded ? "Paused" : "Off"}</span>
        </li>
      </ul>
    </>
  );
}

/* ---------------- Voice, Nights, Appearance, Profile ---------------- */

function VoiceSection({ ctx }) {
  return (
    <>
      <p className="fp">How your agent sounds. A voice steers the model directly (re:dvx dials), from your next message. Each one is tested for honesty before it is offered.</p>
      <VoiceList voices={ctx.voices} onChange={ctx.setVoices} />
    </>
  );
}

export function ScheduleFields({ schedule, onSaved, compact }) {
  const [t, setT] = useState(schedule ? schedule.ends_at : "23:00");
  const [msg, setMsg] = useState(null);
  useEffect(() => { if (schedule) setT(schedule.ends_at); }, [schedule && schedule.ends_at]);
  const save = async (body) => {
    const r = await postJSON("/api/schedule", body);
    if (r.error) { setMsg(r.error); return; }
    setMsg(null); onSaved && onSaved(r);
  };
  return (
    <>
      <div className={compact ? "sched compact" : "frow"}>
        <div className="frow-text"><b>Night starts at</b>{!compact && <p>Your agent saves the day, and the night’s training starts.</p>}</div>
        <div className="frow-act">
          <label className="sr" htmlFor={compact ? "sched-time-pop" : "sched-time"}>Night starts at</label>
          <input id={compact ? "sched-time-pop" : "sched-time"} type="time" className="finput ftime num" value={t} onChange={(e) => setT(e.target.value)} onBlur={() => t !== (schedule && schedule.ends_at) && save({ ends_at: t })} />
        </div>
      </div>
      <div className={compact ? "sched compact" : "frow"}>
        <div className="frow-text"><b>Start it by itself</b>{!compact && <p>At that time, while Afterglow is open. Off: the night starts when you end the day.</p>}</div>
        <div className="frow-act"><Switch on={!!(schedule && schedule.auto)} label="Start the night by itself" onChange={(v) => save({ auto: v })} /></div>
      </div>
      {msg && <p className="ferr" role="alert">{msg}</p>}
    </>
  );
}

function NightsSection({ ctx, settings, onSaved }) {
  if (!settings) return <p className="pfine">Loading…</p>;
  const rule = settings.serving_rule;
  return (
    <>
      <section className="fsec">
        <h2 className="fh">When your day ends</h2>
        <ScheduleFields schedule={ctx.schedule || settings.schedule} onSaved={(r) => { ctx.setSchedule(r); onSaved(); }} />
        <Row title="End the day now" action={<button type="button" className="btn btn-quiet" disabled={!ctx.canEndDay} onClick={() => { cue("press"); ctx.endDay(); }}>End the day now</button>}>
          {ctx.canEndDay ? "Your agent saves today and the night starts." : "This day is sealed: its night has run."}
        </Row>
      </section>
      <section className="fsec">
        <h2 className="fh">What a night must pass</h2>
        <p className="fp">The night keeps its update only if every check passes. Otherwise it rolls back, and your agent stays as it was.</p>
        <ul className="checks big">{((rule && rule.checks) || []).map((c) => { const k = c.replace(/_3k$/, ""); return <li key={c} className="ok">{CHECKS[k] ? CHECKS[k][0] : pretty(c)}</li>; })}</ul>
        <dl className="kv"><div><dt>Honesty limit</dt><dd className="num">{MINUS}5 points on 200 general questions</dd></div><div><dt>Runs on</dt><dd>One 24 GB GPU, after your day ends</dd></div></dl>
      </section>
    </>
  );
}

function AppearanceSection({ ctx }) {
  const [snd, setSnd] = useState(soundOn());
  return (
    <>
      <section className="fsec">
        <h2 className="fh">Sky</h2>
        <div className="seg" role="radiogroup" aria-label="Sky">
          {[["auto", "Follow the hour"], ["light", "Light"], ["dark", "Dark"]].map(([id, label]) => (
            <button key={id} type="button" role="radio" aria-checked={ctx.mode === id} className={ctx.mode === id ? "on" : ""} onClick={() => ctx.setSky(id)}>{label}</button>
          ))}
        </div>
        <p className="fhint">Following the hour, the app changes at dawn, day, dusk and night. Night is the dark mode.</p>
      </section>
      <section className="fsec">
        <h2 className="fh">Sound and motion</h2>
        <Row title="Sounds" action={<Switch on={snd} label="Sounds" onChange={(v) => { setSound(v); setSnd(v); }} />}>
          A soft press when you send, a chime when a night is kept, a low tone when it rolls back. Silent while you type.
        </Row>
        <Row title="Motion">Panels and menus move only when you open them. With Reduce motion on in your system, they fade instead.</Row>
      </section>
    </>
  );
}

function ProfileSection({ ctx, settings }) {
  const [adding, setAdding] = useState(false);
  const st = ctx.state;
  return (
    <>
      <div className="ffield">
        <label htmlFor="pf-name">Name</label>
        <input id="pf-name" className="finput" value={st.persona || st.world} readOnly />
      </div>
      <dl className="kv">
        <div><dt>Days with your agent</dt><dd className="num">{ctx.today || st.day}</dd></div>
        <div><dt>Notes kept</dt><dd>{settings && settings.notes_backend === "vault" ? "On this computer and in your Oneiron vault" : "On this computer"}</dd></div>
      </dl>
      <hr className="frule" />
      <Row title="Add a profile" action={<button type="button" className="btn btn-quiet" onClick={() => setAdding(true)}>Add a profile</button>}>
        Someone new, with their own agent and their own nights.
      </Row>
      {adding && <Onboarding onClose={() => setAdding(false)} onDone={async (world) => { setAdding(false); await ctx.openDay(world, 1); ctx.go("chat"); }} />}
    </>
  );
}

/** The quiet night control in the chat's top bar: when the night starts, and a way to start it now. */
export function NightControl({ schedule, setSchedule, onEndDay, disabled, phase }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const toggle = (v) => { cue("toggle"); setOpen(v); };
  useEffect(() => {
    if (!open) return;
    const close = (e) => { if (!ref.current?.contains(e.target)) toggle(false); };
    const esc = (e) => { if (e.key === "Escape") toggle(false); };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  if (phase) return <span className="night-state">{phase === "morning" ? "Morning" : "Night"}</span>;
  const at = schedule ? schedule.ends_at : "23:00";
  return (
    <div className="nightctl" ref={ref}>
      <button type="button" className="tool nightctl-btn" aria-haspopup="dialog" aria-expanded={open} onClick={() => toggle(!open)}>
        <Icon id="nights" size={17} />
        <span className="num">{schedule && !schedule.auto ? "Night when you end the day" : `Night at ${at}`}</span>
      </button>
      {open && (
        <div className="pop" role="dialog" aria-label="The night">
          <ScheduleFields compact schedule={schedule} onSaved={setSchedule} />
          <button type="button" className="btn btn-quiet pop-end" disabled={disabled} onClick={() => { cue("press"); setOpen(false); onEndDay(); }}>End the day now</button>
        </div>
      )}
    </div>
  );
}
