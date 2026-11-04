import { useEffect, useRef, useState } from "react";
import { getJSON, postJSON } from "../lib.js";
import { Switch } from "../Settings.jsx";

const LINE = "While on, what you copy is saved to your day. Keys and passwords are hidden and kept in your Keychain.";
const POLL_MS = 5000;

/** Clipboard connector (Settings > Connectors): off by default; while on, copies are saved to the day. */
export function ClipboardConnector() {
  const [st, setSt] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  useEffect(() => { busyRef.current = busy; }, [busy]);

  // The state may carry a generic capture error beside on/available/count/pending: keep the state (so the
  // switch always works) and show the error.
  const applyStatus = (r) => {
    if (r && typeof r === "object" && ("on" in r || "available" in r)) {
      setSt(r);
      setErr(r.error || null);
    } else if (r && r.error) {
      setErr(r.error);
    }
  };

  useEffect(() => {
    let live = true;
    getJSON("/api/clipboard")
      .then((r) => { if (live) applyStatus(r); })
      .catch(() => { if (live) setErr("The clipboard settings did not load."); });
    return () => { live = false; };
  }, []);

  const isOn = !!(st && st.on);
  useEffect(() => {
    if (!isOn) return undefined; // no polling while off
    const t = setInterval(async () => {
      if (busyRef.current) return;
      try {
        applyStatus(await getJSON("/api/clipboard"));
      } catch {
        // a failed poll keeps the last state; the next tick retries
      }
    }, POLL_MS);
    return () => clearInterval(t);
  }, [isOn]);

  const flip = async (on) => {
    setBusy(true);
    const r = await postJSON("/api/clipboard", { on }).catch(() => ({ error: "The switch did not go through." }));
    setBusy(false);
    applyStatus(r);
  };

  const unavailable = st && st.available === false;
  const count = st && typeof st.count === "number" ? st.count : null;
  const pending = st && st.pending ? st.pending.length : 0;
  const line = unavailable
    ? `${LINE} Not available on this computer.`
    : `${LINE}${count == null ? "" : count === 0 ? " Nothing copied today." : count === 1 ? " 1 copied today." : ` ${count} copied today.`}${pending > 0 ? ` ${pending} still ${pending === 1 ? "needs" : "need"} a label.` : ""}`;

  return (
    <li>
      <span className="conn-icon" aria-hidden="true">
        <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="5.5" y="4" width="9" height="12.5" rx="1.5" /><path d="M8 4V2.8h4V4" /><path d="M8 9.2h4M8 12.2h4" /></svg>
      </span>
      <div>
        <b>Clipboard</b>
        <p>{st ? line : LINE}</p>
        {err && <p className="ferr" role="alert">{err}</p>}
      </div>
      <Switch on={!!(st && st.on)} label="Clipboard" disabled={!st || busy || (unavailable && !st.on)} onChange={flip} />
    </li>
  );
}
