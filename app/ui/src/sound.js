/* Sound: cuelume, the house interaction-sound library (public/cuelume.js, MIT, Daniel Belyi; inlined, with one local
   addition, define(), so the app's own cues play through the same voice, master chain and mute).
   Calm at rest: a press when you send or commit, toggle on open AND close, and the night's outcome: a chime when a
   night is kept, a low settling tone when it rolls back. Silent on hover, navigation and typing. Persisted mute. */

const KEY = "afterglow-sound";

/** The app's own cues, in cuelume's cue format (layers of tones; offsets, envelopes and glides in seconds). */
export const CUES = {
  // a kept night: three soft bell tones rising (E5, B5, E6), a warm room behind them. earshot: "a success chime" (z +3.7)
  "night-kept": {
    masterGain: 0.5,
    layers: [
      { kind: "tone", waveform: "sine", frequency: 659.25, attack: 0.008, decay: 0.42, peak: 0.07 },
      { kind: "tone", waveform: "sine", frequency: 987.77, offset: 0.11, attack: 0.008, decay: 0.46, peak: 0.06 },
      { kind: "tone", waveform: "sine", frequency: 1318.51, offset: 0.22, attack: 0.008, decay: 0.6, peak: 0.05 },
    ],
    shimmer: { delay: 0.14, feedback: 0.28, wet: 0.2, lowpass: 3800 },
  },
  // a rolled-back night: two low mallet notes stepping down (D4, then A3), calm, not an alarm. Listened with earshot:
  // "a gentle low marimba note" (strong, 92% against an error buzzer, a failure sound, a sub bass drop, a chime)
  "night-rollback": {
    masterGain: 0.5,
    layers: [
      { kind: "tone", waveform: "sine", frequency: 293.66, attack: 0.006, decay: 0.42, peak: 0.07 },
      { kind: "tone", waveform: "sine", frequency: 1174.66, attack: 0.003, decay: 0.08, peak: 0.012 },
      { kind: "tone", waveform: "sine", frequency: 220, offset: 0.17, attack: 0.006, decay: 0.6, peak: 0.075 },
      { kind: "tone", waveform: "sine", frequency: 880, offset: 0.17, attack: 0.003, decay: 0.09, peak: 0.012 },
    ],
    shimmer: { delay: 0.15, feedback: 0.24, wet: 0.16, lowpass: 2600 },
  },
};

const lib = () => (typeof window !== "undefined" ? window.cuelume : null);
let defined = false;
function ensure() {
  const c = lib();
  if (!c || defined) return c;
  if (c.define) for (const [name, spec] of Object.entries(CUES)) c.define(name, spec);
  c.setEnabled(soundOn());
  defined = true;
  return c;
}

export const soundOn = () => localStorage.getItem(KEY) !== "off";

export function setSound(on) {
  localStorage.setItem(KEY, on ? "on" : "off");
  const c = ensure();
  if (c) c.setEnabled(on);
  if (on) cue("toggle");
}

/** Play one cue (cuelume's or the app's own) when sound is on; never throws. */
export function cue(name) {
  if (!soundOn()) return;
  try { const c = ensure(); if (c) c.play(name); } catch { /* sound is never worth an error */ }
}
