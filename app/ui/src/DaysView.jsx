import { useEffect, useState } from "react";
import { getJSON, longDate, shortDate } from "./lib.js";
import { EarlierDay } from "./Thread.jsx";

/** Memory > Days: every day you spent with your agent, newest first, with what its night did; open one to read it. */
export function DaysView({ ctx }) {
  const { nights, openPanel } = ctx;
  const [days, setDays] = useState(null);
  const [open, setOpen] = useState(null);
  const [day, setDay] = useState(null);
  useEffect(() => { getJSON("/api/days/summary").then((r) => setDays(r.days || [])).catch(() => setDays([])); }, []);
  useEffect(() => {
    if (open == null) return;
    setDay(null);
    getJSON(`/api/day/history?day=${open}`).then(setDay).catch(() => setDay({ day: open, history: [] }));
  }, [open]);
  if (!days) return <p className="pfine">Loading…</p>;
  const byNight = Object.fromEntries((nights || []).map((n) => [n.day, n]));
  if (open != null) {
    return (
      <div className="dayview">
        <button type="button" className="link back-link" onClick={() => setOpen(null)}>All days</button>
        {!day ? <p className="pfine">Loading…</p> : (
          <div className="thread dayview-thread">
            <EarlierDay d={day} night={byNight[day.day]} onOpenPanel={(tab, f) => { ctx.go("chat"); openPanel(tab, f); }} />
          </div>
        )}
      </div>
    );
  }
  return (
    <ul className="days-list">
      {days.map((d) => {
        const n = byNight[d.day];
        const mark = n && n.decision ? (n.decision === "keep" ? "keep" : "rollback") : null;
        return (
          <li key={d.day}>
            <button type="button" className="day-item" onClick={() => setOpen(d.day)}>
              <span className="day-when"><b>{d.date ? shortDate(d.date) : `Day ${d.day}`}</b><span className="num">Day {d.day}</span></span>
              <span className="day-first">{d.first || "Nothing was said."}</span>
              <span className="day-meta num">{d.turns} message{d.turns === 1 ? "" : "s"}{d.notes ? `, ${d.notes} note${d.notes === 1 ? "" : "s"}` : ""}</span>
              {mark ? <span className={`day-night is-${mark}`}><i className={`nmark is-${mark}`} aria-hidden="true" />{mark === "keep" ? "Kept" : "Rolled back"}</span> : <span className="day-night" />}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
