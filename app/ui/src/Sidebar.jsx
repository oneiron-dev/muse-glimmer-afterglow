import { useEffect, useRef, useState } from "react";
import { ThemeToggle } from "./Header.jsx";
import { Onboarding } from "./Onboarding.jsx";
import { SettingsNav, sectionOf } from "./Settings.jsx";
import { useSearch } from "@tanstack/react-router";
import { cue } from "./sound.js";

const HOURS = ["dawn", "noon", "dusk", "night"];
const HOUR_NAME = { dawn: "Dawn", noon: "Day", dusk: "Dusk", night: "Night" };

const NAV = [
  { id: "chat", label: "Chat" },
  { id: "memory", label: "Memory" },
  { id: "nights", label: "Nights" },
  { id: "calendar", label: "Calendar" },
  { id: "mail", label: "Mail" },
  { id: "tasks", label: "Tasks" },
  { id: "files", label: "Files" },
];

export function Icon({ id, size = 20 }) {
  const p = { width: size, height: size, viewBox: "0 0 20 20", fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true };
  switch (id) {
    case "chat": return <svg {...p}><path d="M4 4.5h12a1.5 1.5 0 0 1 1.5 1.5v7a1.5 1.5 0 0 1-1.5 1.5H9l-3.5 3v-3H4A1.5 1.5 0 0 1 2.5 13V6A1.5 1.5 0 0 1 4 4.5Z" /></svg>;
    case "memory": return <svg {...p}><path d="M5 3.5h8.5a1.5 1.5 0 0 1 1.5 1.5v11.5l-2.5-1.6-2.5 1.6-2.5-1.6-2.5 1.6V5A1.5 1.5 0 0 1 5 3.5Z" /><path d="M7.5 7.5h5M7.5 10.5h3.5" /></svg>;
    case "nights": return <svg {...p}><path d="M15.5 12.6A6.5 6.5 0 0 1 7.4 4.5a6.5 6.5 0 1 0 8.1 8.1Z" /></svg>;
    case "calendar": return <svg {...p}><rect x="3" y="4.5" width="14" height="12" rx="2" /><path d="M3 8.5h14M7 3v3M13 3v3" /></svg>;
    case "mail": return <svg {...p}><rect x="2.5" y="4.5" width="15" height="11" rx="2" /><path d="m3 5.5 7 5.5 7-5.5" /></svg>;
    case "tasks": return <svg {...p}><rect x="3.5" y="3.5" width="13" height="13" rx="3" /><path d="m7 10 2.2 2.2L13.5 8" /></svg>;
    case "files": return <svg {...p}><path d="M5.5 2.5h6l3.5 3.5v10a1.5 1.5 0 0 1-1.5 1.5h-8A1.5 1.5 0 0 1 4 16V4a1.5 1.5 0 0 1 1.5-1.5Z" /><path d="M11.5 2.5V6h3.5" /></svg>;
    case "settings": return <svg {...p}><path d="M3.5 6h7M14.5 6h2M3.5 14h2M9.5 14h7" /><circle cx="12.5" cy="6" r="2" /><circle cx="7.5" cy="14" r="2" /></svg>;
    case "fold": return <svg {...p}><rect x="2.5" y="3.5" width="15" height="13" rx="2.5" /><path d="M7.5 3.5v13" /></svg>;
    case "plus": return <svg {...p}><path d="M10 4.5v11M4.5 10h11" /></svg>;
    default: return null;
  }
}

/** The Afterglow mark: light in bands over a horizon, one colour (the text's). */
export function Mark({ size = 24 }) {
  // A sun cut by the horizon; below it, its light on the water in two fading bands.
  return (
    <svg className="mark" width={size} height={size} viewBox="0 0 24 24" fill="currentColor" role="img" aria-label="Afterglow">
      <defs><clipPath id="ag-mark"><circle cx="12" cy="12" r="8" /></clipPath></defs>
      <g clipPath="url(#ag-mark)"><rect x="4" y="4" width="16" height="7.3" /><rect x="4" y="12.6" width="16" height="2.6" opacity="0.5" /><rect x="4" y="16.4" width="16" height="3.6" opacity="0.24" /></g>
    </svg>
  );
}

const initials = (name) => (name || "").split(" ").map((w) => w[0]).join("").slice(0, 2);

/** The sidebar: the app icon and your profile, the screens, and settings (earlier days live in Memory, Days).
 Its ground is the painted sky of the hour. Folds to icons (⌘\). */
export function Sidebar({ ctx, screen, folded, onFold, newNotes }) {
  const { state, days, nights, openDay, go, mode, setSky, hour, today } = ctx;
  const { section } = useSearch({ strict: false });
  const inSettings = screen === "settings";
  const people = (days && days.people) || [];
  return (
    <nav className="side" aria-label="Afterglow">
      <div className="side-sky" aria-hidden="true">
        {HOURS.map((h) => <img key={h} className={`sky${h === hour ? " on" : ""}`} src={`/sky/hour-${h}.webp`} alt="" />)}
      </div>
      <div className="side-in">
        {inSettings ? <SettingsNav section={sectionOf(section)} folded={folded} onBack={() => go("chat")} /> : <>
        <div className="side-top">
          <Mark />
          <ProfileMenu state={state} people={people} openDay={openDay} folded={folded} reload={ctx.reload} />
        </div>
        <ul className="side-list nav">
          {NAV.map((n) => (
            <li key={n.id}>
              <button type="button" className={`side-row${screen === n.id ? " on" : ""}`} aria-current={screen === n.id ? "page" : undefined}
                onClick={() => go(n.id)} title={folded ? n.label : undefined}>
                <Icon id={n.id} />
                <span className="side-name">{n.label}</span>
                {n.id === "memory" && newNotes > 0 && <span className="badge num">+{newNotes}</span>}
              </button>
            </li>
          ))}
        </ul>
        </>}
        <div className="side-foot">
          {!inSettings && <button type="button" className="side-row" onClick={() => go("settings")} title={folded ? "Settings" : undefined}>
            <Icon id="settings" /><span className="side-name">Settings</span>
          </button>}
          <div className="side-tools">
            <ThemeToggle mode={mode} onChange={setSky} />
            {!folded && <span className="side-hour">{mode === "auto" ? HOUR_NAME[hour] : mode === "light" ? "Light" : "Dark"}</span>}
            <button type="button" className="sky-btn fold-btn" onClick={() => onFold(!folded)} aria-label={folded ? "Open the sidebar (⌘\\)" : "Fold the sidebar (⌘\\)"} title={folded ? "Open the sidebar (⌘\\)" : "Fold the sidebar (⌘\\)"}>
              <Icon id="fold" size={18} />
            </button>
          </div>
        </div>
      </div>
    </nav>
  );
}

/** Your profile, like a signed-in account: switch to another profile, or add one (with a short onboarding). */
function ProfileMenu({ state, people, openDay, folded, reload }) {
  const [open, setOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    const close = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const esc = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  const name = state.persona || state.world;
  return (
    <div className="profile" ref={ref}>
      <button type="button" className="profile-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => { cue("toggle"); setOpen(!open); }} title={folded ? name : undefined}>
        <span className="avatar on">{initials(name)}</span>
        <span className="profile-name">{name}</span>
        <i className="caret" aria-hidden="true" />
      </button>
      {open && (
        <div className="menu" role="menu">
          {people.map((p) => {
            const cur = p.world === state.world;
            return (
              <button key={p.world} type="button" role="menuitemradio" aria-checked={cur} className={`menu-row${cur ? " on" : ""}`}
                onClick={() => { setOpen(false); if (!cur) openDay(p.world, p.days[p.days.length - 1]); }}>
                <span className={`avatar${cur ? " on" : ""}`}>{initials(p.name || p.world)}</span>
                <span className="menu-text"><b>{p.name || p.world}</b><small>{p.days.length} day{p.days.length === 1 ? "" : "s"}</small></span>
                {cur && <span className="menu-check" aria-hidden="true">✓</span>}
              </button>
            );
          })}
          <hr />
          <button type="button" role="menuitem" className="menu-row" onClick={() => { setOpen(false); setAdding(true); }}>
            <span className="avatar plus"><Icon id="plus" size={16} /></span>
            <span className="menu-text"><b>Add a profile</b><small>Someone new, with their own agent</small></span>
          </button>
        </div>
      )}
      {adding && <Onboarding onClose={() => setAdding(false)} onDone={async (world) => { setAdding(false); await openDay(world, 1); }} />}
    </div>
  );
}
