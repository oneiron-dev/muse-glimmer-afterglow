/* Sound: cuelume, the house interaction-sound library (public/cuelume.js, MIT, Daniel Belyi; inlined, with one local
   addition, define(), so the app's own cues play through the same voice, master chain and mute).
   Calm at rest: a press when you send or commit, toggle on open AND close, and the night's outcome: a chime when a
   night is kept, a low settling tone when it rolls back. Silent on hover, navigation and typing. Persisted mute. */

const KEY = "afterglow-sound";

/** The app's own cues, in cuelume's cue format (layers of tones; offsets, envelopes and glides in seconds). */
export const CUES = {
  // a kept night: three soft bell tones rising (E5, B5, E6), a warm room behind them
  "night-kept": {
    masterGain: 0.5,
    layers: [
      { kind: "tone", waveform: "sine", frequency: 659.25, attack: 0.008, decay: 0.42, peak: 0.07 },
      { kind: "tone", waveform: "sine", frequency: 987.77, offset: 0.11, attack: 0.008, decay: 0.46, peak: 0.06 },
      { kind: "tone", waveform: "sine", frequency: 1318.51, offset: 0.22, attack: 0.008, decay: 0.6, peak: 0.05 },
    ],
    shimmer: { delay: 0.14, feedback: 0.28, wet: 0.2, lowpass: 3800 },
  },
  // a rolled-back night: one low tone that settles down a fourth (G4 to D4, with its octave below); calm, not an alarm
  "night-rollback": {
    masterGain: 0.5,
    layers: [
      { kind: "tone", waveform: "sine", frequency: 392, glideTo: 293.66, glideTime: 0.3, attack: 0.03, decay: 0.55, peak: 0.07 },
      { kind: "tone", waveform: "sine", frequency: 196, glideTo: 146.83, glideTime: 0.3, attack: 0.03, decay: 0.6, peak: 0.06 },
    ],
    shimmer: { delay: 0.16, feedback: 0.2, wet: 0.1, lowpass: 1400 },
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
