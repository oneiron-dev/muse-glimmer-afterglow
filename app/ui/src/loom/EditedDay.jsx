import { useState } from "react";
import { postJSON } from "../lib.js";
import { cue } from "../sound.js";

export function EditedDay({ line, disabled, sealed, onReload }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  if (!line || line === "main") return null;
  const back = async () => {
    if (busy || disabled || sealed) return;
    cue("press");
    setBusy(true);
    setError(null);
    try {
      const r = await postJSON("/api/branch/switch", { branch: "main" });
      if (r.error) setError(r.error);
      else if (onReload) await onReload();
    } catch {
      setError("Your first version did not open. Try again.");
    }
    setBusy(false);
  };
  return <div className="fhint" role="note">
    You are on an edited version of this day.{" "}
    <button type="button" className="link" disabled={disabled || busy || sealed} onClick={back}>Back to your first version</button>
    {error && <p className="ferr" role="alert">{error}</p>}
  </div>;
}
