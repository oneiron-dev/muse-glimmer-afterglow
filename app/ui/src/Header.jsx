import { useEffect, useRef, useState } from "react";
import { longDate } from "./lib.js";

/** Whose agent and which day: a menu of every person and their recorded days (GET /api/days, POST /api/day/open). */
export function PersonMenu({ state, days, onOpen }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    const close = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const esc = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  return (
    <div className="person" ref={ref}>
      <button type="button" className="person-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        <b>{state.persona || state.world}</b>
        <span className="num">Day {state.day}, {longDate(state.date)}</span>
        <i className="caret" aria-hidden="true" />
      </button>
      {open && days && (
        <div className="menu" role="menu">
          {days.people.map((p) => (
            <div key={p.world} className="menu-person">
              <p className="menu-name">{p.name || p.world}</p>
              <div className="menu-days">
                {p.days.map((d) => {
                  const cur = p.world === state.world && d === state.day;
                  return (
                    <button key={d} type="button" role="menuitemradio" aria-checked={cur} className={`day-chip num${cur ? " on" : ""}`}
                      onClick={() => { setOpen(false); if (!cur) onOpen(p.world, d); }}>{d}</button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const MODES = [
  { id: "auto", label: "Follow the hour" },
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
];

/** Hourlight: the sky follows the clock, or the user holds it light or dark. */
export function ThemeToggle({ mode, onChange }) {
  const i = MODES.findIndex((m) => m.id === mode);
  const next = MODES[(i + 1) % MODES.length];
  return (
    <button type="button" className={`sky-btn sky-${mode}`} onClick={() => onChange(next.id)}
      aria-label={`Sky: ${MODES[i].label}. Switch to ${next.label.toLowerCase()}`} title={`Sky: ${MODES[i].label}`}>
      <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
        {mode === "dark" ? <path d="M14.5 12.8A6 6 0 0 1 7.2 5.5a6 6 0 1 0 7.3 7.3Z" fill="currentColor" />
          : mode === "light" ? <><circle cx="10" cy="10" r="3.6" fill="currentColor" />{[0, 45, 90, 135, 180, 225, 270, 315].map((a) => <line key={a} x1="10" y1="2.4" x2="10" y2="4.2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" transform={`rotate(${a} 10 10)`} />)}</>
          : <><circle cx="10" cy="10" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M10 3.5a6.5 6.5 0 0 1 0 13Z" fill="currentColor" /></>}
      </svg>
    </button>
  );
}
