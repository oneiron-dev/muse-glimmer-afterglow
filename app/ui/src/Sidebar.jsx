import { ThemeToggle } from "./Header.jsx";
import { shortDate } from "./lib.js";

const HOURS = ["dawn", "noon", "dusk", "night"];
const HOUR_NAME = { dawn: "Dawn", noon: "Day", dusk: "Dusk", night: "Night" };

/** The sidebar: the painted sky of the hour, the people, and the open person's days with each night's mark. */
export function Sidebar({ hour, mode, onSky, state, days, nights, onOpen }) {
  const people = (days && days.people) || [];
  const me = people.find((p) => p.world === state.world);
  const byNight = Object.fromEntries((nights || []).map((n) => [n.day, n]));
  const dayDate = (d) => {
    // Each recorded day is one calendar day after the previous; the open day's date anchors them.
    const t = new Date(`${state.date}T12:00:00`);
    t.setDate(t.getDate() + (d - state.day));
    return t.toISOString().slice(0, 10);
  };
  return (
    <nav className="side" aria-label="People and days">
      <div className="side-sky" aria-hidden="true">
        {HOURS.map((h) => <img key={h} className={`sky${h === hour ? " on" : ""}`} src={`/sky/hour-${h}.webp`} alt="" />)}
      </div>
      <div className="side-in">
        <p className="side-mark">Afterglow</p>
        <section>
          <h2 className="side-h">People</h2>
          <ul className="side-list">
            {people.map((p) => {
              const cur = p.world === state.world;
              return (
                <li key={p.world}>
                  <button type="button" className={`side-row${cur ? " on" : ""}`} aria-current={cur ? "true" : undefined}
                    onClick={() => !cur && onOpen(p.world, p.days[p.days.length - 1])}>
                    <span className="avatar" aria-hidden="true">{(p.name || p.world).split(" ").map((w) => w[0]).join("").slice(0, 2)}</span>
                    <span className="side-name">{p.name || p.world}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
        {me && (
          <section className="side-days">
            <h2 className="side-h">{(me.name || "").split(" ")[0]}’s days</h2>
            <ul className="side-list">
              {[...me.days].reverse().map((d) => {
                const cur = d === state.day;
                const n = byNight[d];
                const mark = n ? (n.decision === "keep" ? "keep" : n.decision === "rollback" ? "rollback" : "none") : "none";
                return (
                  <li key={d}>
                    <button type="button" className={`side-row day-row${cur ? " on" : ""}`} aria-current={cur ? "true" : undefined} onClick={() => !cur && onOpen(state.world, d)}>
                      <span className="side-name">Day {d}<small>{shortDate(dayDate(d))}</small></span>
                      {n && n.decision && <i className={`nmark is-${mark}${n.served ? " is-served" : ""}`} title={`Night ${d}: ${mark === "keep" ? "kept" : "rolled back"}${n.served ? ", in use" : ""}`} />}
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        )}
        <div className="side-foot">
          <ThemeToggle mode={mode} onChange={onSky} />
          <span>{mode === "auto" ? `${HOUR_NAME[hour]}, follows the hour` : mode === "light" ? "Held light" : "Held dark"}</span>
        </div>
      </div>
    </nav>
  );
}
