"use strict";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
const WHO = {
  Dreamer: { mark: "dreamer", says: "Oneiron's Dreamer, consolidating the day in your memory vault" },
  "Night job": { mark: "job", says: "the night job, turning the day into training data" },
};
const THOUGHT_SHOWN = 700;

function clip(text, n) {
  return text.length <= n ? [text, ""] : [text.slice(0, text.lastIndexOf(" ", n) > n * 0.6 ? text.lastIndexOf(" ", n) : n), text];
}

function more(label, body) {
  const d = el("details", "more");
  d.append(el("summary", null, label));
  const pre = el("pre");
  pre.textContent = body;
  d.append(pre);
  return d;
}

function item(t) {
  const li = el("li", "thought-item");
  li.dataset.who = t.who;
  const time = new Date(t.time);
  li.append(el("div", "t-time", time.toLocaleTimeString("en-GB", { hour12: false })));
  const body = el("div");
  const head = el("div", "t-head");
  head.append(el("span", `mark-who ${WHO[t.who].mark}`), el("span", "who", t.who));
  if (t.tokens) head.append(el("span", null, `${t.tokens} tokens`));
  body.append(head, el("p", "t-task", t.task || "(no task line)"));
  if (t.thought) {
    const [shown, full] = clip(t.thought, THOUGHT_SHOWN);
    body.append(el("p", "t-thought", shown + (full ? " …" : "")));
    if (full) body.append(more("Read the whole thought", full));
  }
  if (t.answer) {
    const a = el("p", "t-answer");
    const [shown, full] = clip(t.answer, 280);
    a.append("Answer: ", el("code", null, shown + (full ? " …" : "")));
    body.append(a);
    if (full) body.append(more("The whole answer", full));
  }
  if (t.input) body.append(more("What it was given", t.input));
  li.append(body);
  return li;
}

function applyFilters() {
  const on = new Set([...document.querySelectorAll(".chip[aria-pressed='true']")].map((c) => c.dataset.who));
  document.querySelectorAll(".thought-item").forEach((li) => (li.hidden = !on.has(li.dataset.who)));
}

async function boot() {
  const qs = new URLSearchParams(location.search);
  const r = await (await fetch(`/api/thoughts?${qs}`)).json();
  const list = $("thoughts");
  if (!r.items) {
    $("summary").textContent = "No night has been logged yet. Once the night job and the Dreamer run, their thoughts show here.";
    document.querySelector(".filters").hidden = true;
    return;
  }
  $("when").textContent = `Log of ${r.date}`;
  // Readiness pings (the server's health checks answer "ready") are not thoughts.
  const items = r.items.filter((t) => WHO[t.who] && !/^ready\.?$/i.test((t.answer || "").trim()));
  const counts = Object.fromEntries(Object.keys(WHO).map((w) => [w, items.filter((t) => t.who === w).length]));
  const s = $("summary");
  s.replaceChildren(`${items.length} calls to Muse Glimmer on your own GPU: `,
    el("strong", null, `${counts.Dreamer}`), ` by ${WHO.Dreamer.says}, and `,
    el("strong", null, `${counts["Night job"]}`), ` by ${WHO["Night job"].says}.`);
  for (const chip of document.querySelectorAll(".chip")) {
    chip.append(el("span", `mark-who ${WHO[chip.dataset.who].mark}`), `${chip.dataset.who} (${counts[chip.dataset.who]})`);
    chip.addEventListener("click", () => {
      chip.setAttribute("aria-pressed", chip.getAttribute("aria-pressed") === "true" ? "false" : "true");
      applyFilters();
    });
  }
  list.replaceChildren(...items.map(item));
}

boot();
