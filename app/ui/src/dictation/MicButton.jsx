import "./dictation.css";

/** The microphone in the message box: click to start, click again to stop.
 *  While listening the button fills and a small level bar shows beneath it. */
export function MicButton({ recording, sending, level = 0, disabled = false, onToggle }) {
  const label = sending ? "Writing down what you said" : recording ? "Stop dictation" : "Dictate";
  return (
    <span className="dic-wrap">
      <button
        type="button"
        className={`dic-mic${recording ? " live" : ""}`}
        aria-label={label}
        aria-pressed={!!recording}
        title={recording ? "Stop dictation" : "Dictate (or hold Right Option to talk)"}
        disabled={disabled || sending}
        onClick={onToggle}
      >
        {sending ? (
          <i className="dic-spin" aria-hidden="true" />
        ) : (
          <svg viewBox="0 0 20 20" width="17" height="17" aria-hidden="true">
            <path d="M10 2.8a2.6 2.6 0 0 1 2.6 2.6v4.2a2.6 2.6 0 0 1-5.2 0V5.4A2.6 2.6 0 0 1 10 2.8Z" fill="none" stroke="currentColor" strokeWidth="1.6" />
            <path d="M5.6 9.2a4.4 4.4 0 0 0 8.8 0M10 13.6v3.2M7.4 16.8h5.2" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
        )}
      </button>
      {recording && (
        <span className="dic-level" aria-hidden="true"><i style={{ "--l": level.toFixed(2) }} /></span>
      )}
    </span>
  );
}
