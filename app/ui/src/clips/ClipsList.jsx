import { useEffect, useRef, useState } from "react";
import { getJSON, postJSON, queueAttachment } from "../lib.js";
import { Empty } from "../Panel.jsx";
import { cue } from "../sound.js";

const POLL_MS = 5000;

/** A clip's time: the watcher writes ts as epoch seconds or an ISO string. */
const fmtTime = (ts) => {
  if (ts == null || ts === "") return "";
  if (typeof ts === "string") return ts.length >= 16 ? ts.slice(11, 16) : ts;
  const d = new Date(ts > 1e12 ? ts : ts * 1000);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
};

const preview = (text, n = 280) => {
  const t = String(text == null ? "" : text);
  return t.length > n ? `${t.slice(0, n)}…` : t;
};

/** Files > Copied today: what you copied, newest first. Secrets show only their mask until labelled. */
export function ClipsList({ day, world, onAttached }) {
  const [clips, setClips] = useState(null);
  const [on, setOn] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(null); // "action:id"; every action button is disabled while set
  const [added, setAdded] = useState({}); // id -> true (attached this visit)
  const [itemErr, setItemErr] = useState({}); // id -> message
  const [drafts, setDrafts] = useState({}); // id -> label draft
  const busyRef = useRef(null);
  useEffect(() => { busyRef.current = busy; }, [busy]);

  useEffect(() => {
    let live = true;
    setClips(null); setErr(null); setItemErr({}); setAdded({}); setDrafts({});
    Promise.all([
      getJSON("/api/clipboard").catch(() => null),
      getJSON(`/api/clips?day=${day}`).catch(() => ({ error: "Your copied text did not load." })),
    ]).then(([cb, cl]) => {
      if (!live) return;
      setOn(cb && typeof cb === "object" && typeof cb.on === "boolean" ? cb.on : null);
      if (cl && cl.error) { setErr(cl.error); setClips([]); }
      else setClips((cl && cl.clips) || []);
    });
    return () => { live = false; };
  }, [day, world]);

  useEffect(() => {
    if (on !== true) return undefined; // no polling while off
    let live = true;
    const t = setInterval(async () => {
      if (busyRef.current) return;
      const cl = await getJSON(`/api/clips?day=${day}`).catch(() => null);
      if (!live || !cl || cl.error) return; // polls never wipe the list; the next tick retries
      setClips(cl.clips || []);
    }, POLL_MS);
    return () => { live = false; clearInterval(t); };
  }, [on, day, world]);

  if (clips === null) return null; // loading: no flash
  if (!err && clips.length === 0 && on !== true) return null; // off and empty: Files looks as before

  const fail = (id, message) => setItemErr((m) => ({ ...m, [id]: message }));
  const clearFail = (id) => setItemErr((m) => { const n = { ...m }; delete n[id]; return n; });

  const attach = async (c) => {
    cue("press");
    setBusy(`attach:${c.id}`);
    const r = await postJSON("/api/clips/attach", { id: c.id }).catch(() => ({ error: "It was not added." }));
    setBusy(null);
    if (r.error) { fail(c.id, r.error); return; }
    clearFail(c.id);
    queueAttachment(r.attachment);
    setAdded((m) => ({ ...m, [c.id]: true }));
    if (onAttached) onAttached();
  };

  const remove = async (c) => {
    cue("press");
    setBusy(`delete:${c.id}`);
    const r = await postJSON("/api/clips/delete", { id: c.id }).catch(() => ({ error: "It was not deleted." }));
    setBusy(null);
    if (r.error) { fail(c.id, r.error); return; }
    setClips((cs) => cs.filter((x) => x.id !== c.id));
  };

  const label = async (c, e) => {
    e.preventDefault();
    const text = (drafts[c.id] || "").trim();
    if (!text) return;
    cue("press");
    setBusy(`label:${c.id}`);
    const r = await postJSON("/api/clips/label", { id: c.id, label: text }).catch(() => ({ error: "The label did not save." }));
    setBusy(null);
    if (r.error) { fail(c.id, r.error); return; }
    clearFail(c.id);
    setClips((cs) => cs.map((x) => (x.id === c.id ? { ...x, label: text } : x)));
    setDrafts((m) => { const n = { ...m }; delete n[c.id]; return n; });
  };

  return (
    <>
      <h2 className="sec">Copied today</h2>
      {err ? <p className="ferr" role="alert">{err}</p> : clips.length === 0 ? (
        <Empty kind="tasks">Nothing copied yet. While Clipboard is on, what you copy is saved here.</Empty>
      ) : (
        <ul className="notes big">
          {[...clips].reverse().map((c) => {
            const secret = c.kind === "secret";
            const when = [c.app, fmtTime(c.ts)].filter(Boolean).join(" · ");
            return (
              <li key={c.id}>
                <span className="nk">{when || "Copied"}</span>
                <span className="nacts">
                  {!secret && (
                    <button type="button" className="act" disabled={!!busy || !!added[c.id]} onClick={() => attach(c)}>
                      {added[c.id] ? "Added" : "Send to chat"}
                    </button>
                  )}
                  <button type="button" className="act" disabled={!!busy} onClick={() => remove(c)}>Delete</button>
                </span>
                <span className="nv">{preview(c.text)}</span>
                {secret && c.label && <span className="nwhen">Hidden in your Keychain. For {c.label}.</span>}
                {secret && !c.label && (
                  <>
                    <span className="nwhen">Hidden in your Keychain.</span>
                    <form className="nedit" onSubmit={(e) => label(c, e)}>
                      <label className="sr" htmlFor={`clip-label-${c.id}`}>What is this for?</label>
                      <input
                        id={`clip-label-${c.id}`} value={drafts[c.id] || ""} autoComplete="off"
                        onChange={(e) => setDrafts((m) => ({ ...m, [c.id]: e.target.value }))}
                        placeholder="What is this for?"
                      />
                      <button type="submit" className="btn btn-send" disabled={!(drafts[c.id] || "").trim() || !!busy}>Save</button>
                    </form>
                  </>
                )}
                {!secret && added[c.id] && <span className="nwhen">Added to your chat files.</span>}
                {itemErr[c.id] && <span className="ferr" role="alert" style={{ gridColumn: "1 / -1" }}>{itemErr[c.id]}</span>}
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
