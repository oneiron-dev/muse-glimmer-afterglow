"use strict";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
const fmtS = (s) => (s == null ? "–" : s < 1 ? `${Math.round(s * 1000)} ms` : `${s.toFixed(2)} s`);
const fmtN = (n) => (n == null ? "–" : Number(n).toLocaleString("en-US"));
const pretty = (key) => String(key).replace(/_/g, " ");

let state = null;
let busy = false;
let seenNotes = new Set();

/* ---------- header, notes, context ---------- */

function renderWeights(night, adapter, serving) {
  // What the last night learned, and which update the chat is using now (the serving rule's line, from the server).
  const box = $("weights-body");
  if (!night) {
    if (serving && serving.why) box.replaceChildren(el("p", "serving", serving.why));
    return;
  }
  box.replaceChildren();
  const p = el("p", "empty");
  if (night.decision === "rollback") {
    p.append(`Last night's update was rolled back, so these weights are unchanged.`);
  } else {
    p.append(`After night ${night.day} it answers `, el("strong", null, `${night.learned} of ${night.facts}`),
      ` of your facts with notes off (before: ${night.before}).`);
  }
  const a = el("a", "report-link", "Open the morning report");
  a.href = `/report?day=${night.day}`;
  box.append(p);
  if (serving && serving.why) box.append(el("p", "serving", serving.why));
  box.append(a);
}

function renderHeader(s) {
  $("who").textContent = s.persona || s.world;
  const d = new Date(`${s.date}T12:00:00`);
  const day = d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  $("when").textContent = `Day ${s.day}, ${day}`;
  $("dot").className = "dot " + (s.server.healthy ? "up" : "down");
  $("dot").title = s.server.healthy ? `Serving at ${s.server.endpoint}` : `No answer from ${s.server.endpoint}`;
  $("weights").textContent = !s.server.healthy ? "server offline" : s.server.adapter || "base weights";
  const r = s.answer_channel_retries || { retried: 0, replies: 0 };
  $("retries").textContent = r.retried ? `${r.retried} of ${r.replies} replies today ended empty and were asked again` : "";
}

function renderNotes(notes, flashIds = new Set()) {
  const byId = Object.fromEntries(notes.map((n) => [n.id, n]));
  const active = notes.filter((n) => n.status === "active").reverse();
  const ul = $("notes");
  ul.replaceChildren();
  for (const n of active) {
    const li = el("li", "note");
    li.append(el("div", "k", pretty(n.key)), el("div", "v", n.value));
    const d = n.decided, was = n.supersedes && byId[n.supersedes];
    if (d && !was && d.linked === false) {  // the model was not sure enough: both notes stay for the night
      const cand = d.candidate && byId[d.candidate];
      li.append(el("div", "decided", `kept both, left for the night: one pass gave ${d.p != null ? `p ${d.p.toFixed(2)}` : "no p"}` +
        (cand ? ` that it replaces "${pretty(cand.key)}"` : "") + (d.latency_s != null ? `, ${d.latency_s.toFixed(2)} s` : "")));
    }
    if (d && was) {  // linked by the model's decision, not by the agent: say how, with its certainty and time
      const how = d.reasoned
        ? `thought it through (one pass p ${d.p.toFixed(2)}, then ${d.reasoned.reasoning_tokens ?? "?"} reasoning tokens)`
        : `decided in one pass${d.p != null ? `, p ${d.p.toFixed(2)}` : ""}`;
      li.append(el("div", "decided", `replaces the earlier note "${pretty(was.key)}", ${how}` +
        (d.latency_s != null ? `, ${d.latency_s.toFixed(2)} s` : "")));
    }
    let prev = n.supersedes && byId[n.supersedes];
    while (prev) {
      li.append(el("div", "old", prev.value));
      prev = prev.supersedes && byId[prev.supersedes];
    }
    if (flashIds.has(n.id)) {
      li.classList.add("fresh");
      requestAnimationFrame(() => requestAnimationFrame(() => li.classList.remove("fresh")));
    }
    ul.append(li);
  }
  $("notes-empty").hidden = active.length > 0;
  notes.forEach((n) => seenNotes.add(n.id));
}

function renderContext(snap) {
  const p = $("context");
  p.replaceChildren();
  const add = (...parts) => parts.forEach((x) => p.append(typeof x === "string" ? document.createTextNode(x) : x));
  const strong = (t) => el("strong", null, t);
  const day = () => {
    if (snap.day_prefill_ms == null) return;
    add(` Today's notes and conversation (${fmtN(snap.day_tokens)} tokens) read in `,
      strong(fmtS(snap.day_prefill_ms / 1000)), ".");
  };
  switch (snap.state) {
    case "restoring":
      add("Loading the saved context…");
      break;
    case "restored":
      add(`Saved context (${fmtN(snap.prefix_tokens)} tokens) restored in `, strong(fmtS((snap.restore_ms || 0) / 1000)), ".");
      day();
      break;
    case "rewarmed":
      add(`No saved context fits these weights, so it was rebuilt (${fmtN(snap.prefix_tokens)} tokens in `,
        strong(fmtS((snap.prefix_ms || 0) / 1000)), ") and saved.");
      day();
      break;
    case "error":
      add(`Loading the context failed: ${snap.message}`);
      break;
    default:
      add("No context loaded yet.");
  }
}

/* ---------- conversation ---------- */

function describeAct(name, args, result) {
  if (result && result.error) return { tag: name, text: result.error, err: true };
  args = args || {};
  switch (name) {
    case "remember": {
      const out = { tag: "remember", text: `${pretty(result.key)}: ${result.value}` };
      if (result.replaced) out.was = result.replaced.old_value;
      if (result.note === "already saved") out.text += " (already saved)";
      return out;
    }
    case "recall": {
      const n = (result.notes || []).length;
      return { tag: "recall", text: `“${args.query}”: ${n ? `${n} note${n > 1 ? "s" : ""}` : "nothing saved"}` };
    }
    case "calendar.list_events":
      return { tag: "calendar", text: `${args.date}: ${(result.events || []).length} event(s)` };
    case "calendar.add_event":
      return { tag: "calendar", text: `added ${result.added.title}, ${result.added.date} ${result.added.time}` };
    case "tasks.list":
      return { tag: "tasks", text: `${(result.tasks || []).length} open` };
    case "tasks.add":
      return { tag: "tasks", text: `added ${result.added.title}` };
    case "tasks.complete":
      return { tag: "tasks", text: `done: ${result.done.title}` };
    default:
      return { tag: name, text: JSON.stringify(args) };
  }
}

function actItem(d) {
  const li = el("li", "act" + (d.err ? " err" : ""));
  const what = el("div", null, d.text);
  if (d.was) what.append(el("div", "was", d.was));
  li.append(el("code", null, d.tag), what);
  return li;
}

function showSpeed(s) {
  const parts = [];
  if (s.ttft_s != null) parts.push(el("span", null, `first token ${fmtS(s.ttft_s)}`));
  if (s.tok_s) parts.push(el("span", null, `${Math.round(s.tok_s)} tok/s`));
  $("speed").replaceChildren(...parts);
}

function newAssistant() {
  const li = el("li", "a");
  const thought = el("details", "thought");
  const summary = el("summary", null, "Thinking…");
  const body = el("div", "body");
  thought.append(summary, body);
  const acts = el("ul", "acts");
  const answer = el("div", "answer");
  li.append(thought, acts, answer);
  return { li, thought, summary, body, acts, answer };
}

function renderHistory(history) {
  const log = $("log");
  log.replaceChildren();
  if (!history.length) {
    log.append(el("li", "new-day", "A new day. Tell Afterglow what you're working on, who you're working with, and what changed."));
  }
  for (const rec of history) {
    if (rec.role === "user") {
      log.append(el("li", "u", rec.content));
      continue;
    }
    const a = newAssistant();
    const reasoning = (rec.messages || []).map((m) => m.reasoning_content).filter(Boolean).join("\n\n");
    if (reasoning) {
      a.body.textContent = reasoning;
      a.summary.textContent = "Thought";
    } else a.thought.remove();
    for (const tc of rec.tool_calls || []) a.acts.append(actItem(describeAct(tc.name, tc.arguments, tc.result)));
    a.answer.textContent = rec.content || "";
    if (rec.error) a.li.append(el("div", "error-line", rec.error));
    log.append(a.li);
    showSpeed(rec);
  }
  log.scrollTop = log.scrollHeight;
}

async function send(text) {
  busy = true;
  $("send").disabled = true;
  const log = $("log");
  log.querySelector(".new-day")?.remove();
  log.append(el("li", "u", text));
  const a = newAssistant();
  a.thought.open = true;
  log.append(a.li);
  log.scrollTop = log.scrollHeight;
  const t0 = performance.now();
  let thinkingEnd = null;
  const flash = new Set();

  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ error: resp.statusText }));
    a.li.append(el("div", "error-line", err.error));
    busy = false;
    $("send").disabled = false;
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
      if (!frame.startsWith("data: ")) continue;
      const ev = JSON.parse(frame.slice(6));
      const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
      switch (ev.type) {
        case "reasoning":
          a.body.textContent += ev.text;
          a.body.scrollTop = a.body.scrollHeight;
          break;
        case "content":
          if (thinkingEnd == null) {
            thinkingEnd = performance.now();
            a.thought.open = false;
            a.summary.textContent = `Thought for ${fmtS((thinkingEnd - t0) / 1000)}`;
          }
          a.answer.textContent += ev.text;
          break;
        case "tool_result":
          a.acts.append(actItem(describeAct(ev.name, ev.arguments, ev.result)));
          break;
        case "note":
          flash.add(ev.note.id);
          break;
        case "stats":
          showSpeed(ev);
          break;
        case "error":
          a.li.append(el("div", "error-line", ev.message));
          break;
        case "turn_done":
          if (!a.body.textContent) a.thought.remove();
          else if (thinkingEnd == null) {
            a.thought.open = false;
            a.summary.textContent = `Thought for ${fmtS((performance.now() - t0) / 1000)}`;
          }
          showSpeed(ev);
          break;
      }
      if (atBottom) log.scrollTop = log.scrollHeight;
    }
  }
  busy = false;
  $("send").disabled = false;
  await refresh(flash);
}

/* ---------- state ---------- */

async function refresh(flash) {
  const s = await (await fetch("/api/state")).json();
  state = s;
  renderHeader(s);
  renderNotes(s.notes, flash);
  renderContext(s.snapshot);
  renderWeights(s.night, s.server.adapter, s.serving);
  return s;
}

async function boot() {
  const s = await refresh();
  renderHistory(s.history);
  const poll = setInterval(async () => {
    if (busy) return;
    const st = await refresh();
    if (!["idle", "restoring"].includes(st.snapshot.state)) clearInterval(poll);
  }, 1500);
}

$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("input").value.trim();
  if (!text || busy) return;
  $("input").value = "";
  send(text);
});
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("composer").requestSubmit();
  }
});
$("end-day").addEventListener("click", async () => {
  const out = $("end-day-result");
  out.textContent = "Saving the day…";
  const r = await (await fetch("/api/day/end", { method: "POST" })).json();
  out.textContent = r.error
    ? `Could not save the day: ${r.error}`
    : `Saved ${r.notes.active} notes. Tonight's training starts from here.`;
});

boot();
