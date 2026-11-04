import { useCallback, useEffect, useRef, useState } from "react";
import { cue } from "../sound.js";

/** Longest single take; the server caps at 60 s, so stop a second early: encoding
 *  padding and event-loop lag must never push a take over the server's limit. */
const MAX_SECONDS = 59;

const pickMime = () => {
  if (typeof MediaRecorder === "undefined") return "";
  for (const t of ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"]) {
    try { if (MediaRecorder.isTypeSupported(t)) return t; } catch { /* try the next */ }
  }
  return "";
};

/** Dictation: record from the microphone, POST the audio, get back cleaned text.
 *  context "composer" keeps the take out of the threads (its text goes into the message).
 *  onText receives the cleaned text when a take completes. With ptt, holding Right
 *  Option talks and releasing it stops, while the app has focus. */
export function useDictation({ context = "composer", ptt = false, enabled = true, onText } = {}) {
  const [status, setStatus] = useState("idle"); // idle | recording | sending
  const [level, setLevel] = useState(0);
  const [error, setError] = useState(null);
  const [last, setLast] = useState(null);
  const live = useRef(null); // {stream, rec, ctx, raf, chunks, mime, timer}
  const statusRef = useRef("idle");
  const mounted = useRef(true);
  const startingRef = useRef(false); // a microphone request is still out
  const seq = useRef(0); // invalidates a start whose microphone request is still out
  const onTextRef = useRef(onText);
  onTextRef.current = onText;
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  const cleanup = () => {
    const l = live.current;
    live.current = null;
    if (!l) return;
    if (l.timer) clearTimeout(l.timer);
    if (l.raf) cancelAnimationFrame(l.raf);
    try { l.ctx && l.ctx.close(); } catch { /* never worth an error */ }
    try { l.stream && l.stream.getTracks().forEach((t) => t.stop()); } catch { /* same */ }
  };
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; seq.current += 1; cleanup(); };
  }, []);

  const stop = useCallback(async () => {
    seq.current += 1;
    if (statusRef.current !== "recording") return;
    const info = live.current;
    statusRef.current = "sending";
    setStatus("sending");
    setLevel(0);
    cue("toggle");
    if (info && info.timer) clearTimeout(info.timer);
    if (info && info.raf) cancelAnimationFrame(info.raf);
    const blob = await new Promise((resolve) => {
      if (!info || !info.rec) { resolve(null); return; }
      const rec = info.rec;
      const done = () => resolve(new Blob(info.chunks, { type: info.mime || "audio/webm" }));
      try {
        if (rec.state === "inactive") done();
        else { rec.onstop = done; rec.stop(); }
      } catch { done(); }
    });
    cleanup();
    if (!blob || !blob.size) {
      statusRef.current = "idle";
      setStatus("idle");
      setError("Nothing was recorded. Try again.");
      return;
    }
    try {
      const res = await fetch(`/api/dictation?context=${encodeURIComponent(context)}`, {
        method: "POST",
        headers: { "Content-Type": blob.type || "audio/webm" },
        body: blob,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof (data && data.error) === "string" && data.error ? data.error : `HTTP ${res.status}`);
      if (!mounted.current) return; // gone while sending: never insert into another route
      setLast(data);
      const text = (data && (data.clean || data.raw) || "").trim();
      if (text) {
        if (onTextRef.current) onTextRef.current(text);
      } else {
        setError("No speech was heard. Move closer and try again.");
      }
    } catch (e) {
      if (mounted.current) {
        const msg = e && e.message && !/^HTTP \d+$/.test(e.message) ? e.message : "Dictation did not go through. Try again.";
        setError(msg);
      }
    } finally {
      statusRef.current = "idle";
      setStatus("idle");
    }
  }, [context]);

  const start = useCallback(async () => {
    if (statusRef.current !== "idle" || startingRef.current || !enabledRef.current) return;
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("Dictation needs a browser that records audio, like Chrome or Safari.");
      return;
    }
    setError(null);
    setLast(null);
    const my = ++seq.current;
    startingRef.current = true;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch (e) {
      startingRef.current = false;
      if (my !== seq.current) return;
      setError(e && (e.name === "NotFoundError" || e.name === "OverconstrainedError")
        ? "No microphone was found on this computer."
        : "The microphone is blocked. Allow it in the address bar, then try again.");
      return;
    }
    startingRef.current = false;
    if (my !== seq.current || statusRef.current !== "idle") { stream.getTracks().forEach((t) => t.stop()); return; }
    const mime = pickMime();
    let rec;
    try {
      rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    } catch {
      stream.getTracks().forEach((t) => t.stop());
      setError("Dictation needs a browser that records audio, like Chrome or Safari.");
      return;
    }
    const chunks = [];
    rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    const info = { stream, rec, ctx: null, raf: 0, chunks, mime: rec.mimeType || mime, timer: 0 };
    live.current = info;
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      const ctx = new AC();
      const src = ctx.createMediaStreamSource(stream);
      const an = ctx.createAnalyser();
      an.fftSize = 512;
      src.connect(an);
      info.ctx = ctx;
      const buf = new Uint8Array(an.fftSize);
      let smooth = 0, frame = 0;
      const loop = () => {
        if (live.current !== info) return;
        an.getByteTimeDomainData(buf);
        let peak = 0;
        for (let i = 0; i < buf.length; i++) { const v = Math.abs(buf[i] - 128) / 128; if (v > peak) peak = v; }
        smooth = smooth * 0.72 + peak * 0.28;
        frame += 1;
        if (frame % 3 === 0) setLevel(Math.min(1, smooth * 1.5));
        info.raf = requestAnimationFrame(loop);
      };
      loop();
    } catch { /* the meter is a nicety; recording goes on without it */ }
    try {
      rec.start(200);
    } catch {
      cleanup();
      setError("Dictation could not start recording. Try again.");
      return;
    }
    statusRef.current = "recording";
    setStatus("recording");
    setLevel(0);
    cue("toggle");
    info.timer = setTimeout(() => stopRef.current(), MAX_SECONDS * 1000);
  }, []);

  const startRef = useRef(start);
  const stopRef = useRef(stop);
  startRef.current = start;
  stopRef.current = stop;

  const cancel = useCallback(() => {
    seq.current += 1;
    if (statusRef.current !== "recording") return;
    cleanup();
    statusRef.current = "idle";
    setStatus("idle");
    setLevel(0);
    cue("toggle");
  }, []);

  const toggle = useCallback(() => {
    if (statusRef.current === "recording") stopRef.current();
    else if (startingRef.current) seq.current += 1; // a request is out: cancel it, do not open a second
    else if (statusRef.current === "idle") startRef.current();
  }, []);

  useEffect(() => {
    if (!ptt) return;
    const down = (e) => {
      if (e.code !== "AltRight" || e.repeat) return;
      if (typeof document !== "undefined" && document.hasFocus && !document.hasFocus()) return;
      e.preventDefault();
      startRef.current();
    };
    const up = (e) => {
      if (e.code !== "AltRight") return;
      e.preventDefault();
      stopRef.current();
    };
    const blur = () => stopRef.current();
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
    };
  }, [ptt]);

  return {
    status,
    recording: status === "recording",
    sending: status === "sending",
    level, error, last,
    start, stop, toggle, cancel,
    clearError: () => setError(null),
  };
}
