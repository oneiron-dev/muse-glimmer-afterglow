// Data helpers: the API, the chat stream, plain-English wording for the night's checks, and the hour of the sky.

export const getJSON = async (url) => (await fetch(url, { cache: "no-store" })).json();
export const postJSON = async (url, body) =>
  (await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })).json();

/** POST /api/chat and yield each server-sent event ({type: "content" | "tool_result" | "note" | ...}). */
export async function* chatStream(text, attachments = []) {
  // Apply this person's saved connectors after a restart or day switch, before the model sees its tool list.
  const connectors = await getJSON("/api/connectors");
  if (connectors.error) {
    yield { type: "error", message: connectors.error };
    return;
  }
  const resp = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, attachments }) });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ error: resp.statusText }));
    yield { type: "error", message: err.error || "The message did not go through." };
    return;
  }
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let cut;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      if (frame.startsWith("data: ")) yield JSON.parse(frame.slice(6));
    }
  }
}

export const MINUS = "−";
export const signed = (x, d = 1) => (x == null ? "n/a" : (x > 0 ? "+" : x < 0 ? MINUS : "") + Math.abs(x).toFixed(d));
export const nf = (n) => (n == null ? "" : Number(n).toLocaleString("en-US"));
export const pretty = (k) => String(k || "").replace(/_/g, " ");

// The night's checks in plain words: [passed, failed].
export const CHECKS = {
  abstention_honesty: ["Stayed honest", "failed the honesty check"],
  learned_gate: ["Learned what you told it", "did not learn enough of the day"],
  forgot_gate: ["Kept its general skills", "lost general skills"],
  tool_gate: ["Its tools still work", "broke its tools"],
  tool_calls: ["Notes and look-ups in chat still work", "broke its notes and look-ups in chat"],
  content_channel: ["Every reply reaches the chat", "left some replies empty"],
  leak_check: ["No test question was in its training", "had a test question in its training"],
  gguf_matches_peft: ["The served file is the tested file", "served file did not match the tested one"],
  abstention_gate: ["Says “I don’t know” at its usual rate", "changed how often it says “I don’t know”"],
};
const ORDER = Object.keys(CHECKS);
export const failedKeys = (decision) => Object.entries((decision && decision.checks) || {}).filter(([, v]) => !v).map(([k]) => k);
export const failedWhy = (keys) =>
  (keys || []).filter((k) => CHECKS[k]).sort((a, b) => ORDER.indexOf(a) - ORDER.indexOf(b)).slice(0, 2).map((k) => CHECKS[k][1]).join(" and ") ||
  "failed a check";

// Code-shaped facts (access codes, passwords) stay off screen.
const HIDDEN = /access|password|\bpin\b|code/i;

/** Facts the night taught: answered with notes off after the night, not before. One wording each, before and after. */
export function learnedExamples(ev) {
  if (!ev) return [];
  const day = ev.eval_day || ev.day || 99;
  const out = [];
  for (const f of ev.facts || []) {
    if ((f.valid_from || 1) > day || HIDDEN.test(f.key)) continue;
    const A = (f.pass && f.pass.A) || [], B = (f.pass && f.pass.B) || [];
    const i = B.findIndex((ok, j) => ok && !A[j]);
    if (i >= 0 && B.filter(Boolean).length >= 2) out.push({ id: f.id, key: f.key, value: f.value, before: f.answers.A[i], after: f.answers.B[i] });
  }
  return out;
}

/** Every fact with how often it was answered with notes off, before (A) and after (B) the night, out of 3 wordings. */
export function factRows(ev) {
  if (!ev) return [];
  const day = ev.eval_day || ev.day || 99;
  return (ev.facts || []).filter((f) => (f.valid_from || 1) <= day).map((f) => ({
    id: f.id, key: f.key, value: HIDDEN.test(f.key) ? "(hidden)" : f.value, hidden: HIDDEN.test(f.key),
    a: ((f.pass && f.pass.A) || []).filter(Boolean).length, b: ((f.pass && f.pass.B) || []).filter(Boolean).length,
    before: f.answers && f.answers.A && f.answers.A[0], after: f.answers && f.answers.B && f.answers.B[0],
  }));
}

/** Hourlight: the sky follows the clock; the user can hold it light or dark. */
export function clockHour(d = new Date()) {
  const h = d.getHours();
  return h >= 5 && h < 9 ? "dawn" : h >= 9 && h < 17 ? "noon" : h >= 17 && h < 20 ? "dusk" : "night";
}
export function resolveHour(mode, story) {
  if (story) return story;
  if (mode === "dark") return "night";
  const h = clockHour();
  if (mode === "light") return h === "night" ? "noon" : h;
  return h;
}

export const longDate = (iso) => {
  if (!iso) return "";
  const d = new Date(`${iso}T12:00:00`);
  return d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
};
export const shortDate = (iso) => {
  if (!iso) return "";
  const d = new Date(`${iso}T12:00:00`);
  return d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
};
