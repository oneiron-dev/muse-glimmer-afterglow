import { useEffect, useState } from "react";
import { getJSON } from "./lib.js";
import { Empty } from "./Panel.jsx";

const GROUPS = [
  { kind: "explain", label: "Explained" },
  { kind: "dictation", label: "Dictation" },
  { kind: "computer", label: "Computer use" },
  { kind: "note", label: "Notes" },
];
const hhmm = (ts) => (ts ? ts.slice(11, 16) : "");

/** The right panel's Threads tab: today's side conversations, grouped by kind; each opens in place. */
export function ThreadsView({ focus }) {
  const [list, setList] = useState(null);
  const [open, setOpen] = useState(focus || null);
  const [detail, setDetail] = useState({});
  useEffect(() => { getJSON("/api/threads").then((r) => setList(r.threads || [])).catch(() => setList([])); }, []);
  useEffect(() => { if (focus) setOpen(focus); }, [focus]);
  useEffect(() => {
    if (!open || detail[open]) return;
    getJSON(`/api/thread?id=${encodeURIComponent(open)}`).then((r) => setDetail((d) => ({ ...d, [open]: r }))).catch(() => {});
  }, [open]);
  if (!list) return <p className="pfine">Loading…</p>;
  if (!list.length) return <Empty kind="memory">Side conversations land here: what you dictate with fn, what you ask your agent to explain, and computer use. Each one also shows in the chat where it happened.</Empty>;
  return (
    <div className="threads">
      {GROUPS.filter((g) => list.some((t) => t.kind === g.kind)).map((g) => (
        <section key={g.kind} className="th-group">
          <h3 className="th-head">{g.label} <span className="num">{list.filter((t) => t.kind === g.kind).length}</span></h3>
          <ul>
            {list.filter((t) => t.kind === g.kind).slice().reverse().map((t) => (
              <li key={t.id} className={open === t.id ? "on" : ""}>
                <button type="button" className="th-row" aria-expanded={open === t.id} onClick={() => setOpen(open === t.id ? null : t.id)}>
                  <b>{t.title}</b>
                  <span className="num">{hhmm(t.ts)}{t.meta && t.meta.app_name ? ` · ${t.meta.app_name}` : ""}</span>
                </button>
                {open === t.id && (
                  <div className="th-body">
                    {!detail[t.id] ? <p className="pfine">Loading…</p> : detail[t.id].records.map((r, i) => (
                      <div key={i} className={`th-rec th-${r.role}`}>
                        <p>{r.clean || r.content}</p>
                        {r.clean && r.content && r.clean !== r.content && <p className="th-heard">Heard: {r.content}</p>}
                      </div>
                    ))}
                    {t.kind === "explain" && <a className="link" href={`/explain?id=${encodeURIComponent(t.id)}`}>Open the explainer</a>}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
