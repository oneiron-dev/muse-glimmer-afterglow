"use strict";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
const pretty = (key) => String(key).replace(/_/g, " ");
const pct = (x) => `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(1)}`;

const ROWS = [
  { arm: "A", title: "Before the night", sub: "notes off" },
  { arm: "B", title: "After the night", sub: "notes off" },
  { arm: "C", title: "Notes in its prompt", sub: "the control" },
];

function dotState(n) {
  return n >= 2 ? "on" : n === 1 ? "partial" : "off";
}

function receiptLink(field) {
  const a = el("a", "rcpt", "receipt");
  a.href = `#receipt`;
  a.addEventListener("click", () => {
    const r = $("receipt");
    r.open = true;
    document.querySelectorAll(".receipt dt").forEach((dt) => dt.classList.toggle("hit", dt.dataset.key.startsWith(field)));
  });
  return a;
}

function verdict(r) {
  const d = r.decision || {};
  const p = $("verdict");
  p.replaceChildren();
  if (d.decision === "keep") p.append("Overnight, Afterglow trained on yesterday and kept the update.");
  else if (d.decision === "rollback") p.append("Overnight, Afterglow trained on yesterday, then rolled the update back.");
  else p.append("Overnight, Afterglow trained on yesterday.");
  // the night's own reason is gate shorthand; the checks panel below says it in words, so show it only on a rollback
  if (d.why && d.decision === "rollback") p.append(el("span", "why", d.why));
}

function forgotNumbers(ev) {
  // The eval of record scores the 200-item slice into ev.forgot; a sweep file has only the 30-item general slice.
  const f = ev.forgot;
  if (f && f.before && f.after) return { ...f, slice: `${f.before.n}-item general and tool slice` };
  const a = ev.arms && ev.arms.A && ev.arms.A.by_kind && ev.arms.A.by_kind.general;
  const b = ev.arms && ev.arms.B && ev.arms.B.by_kind && ev.arms.B.by_kind.general;
  if (!a || !b) return null;
  return { before: a, after: b, passed: null, slice: `${a.n}-item general slice` };
}

function lead(ev, facts, rolledBack) {
  const fl = (ev.learned && ev.learned.facts_learned) || {};
  const n = facts.length;
  const hero = $("hero");
  hero.replaceChildren(document.createTextNode(String(fl.B ?? "–")), el("span", "of", ` of ${n}`));
  const label = $("hero-label");
  label.replaceChildren(
    rolledBack ? "facts the update answered with notes off. It was rolled back, so the model still answers "
               : "facts it now answers with notes off. Before the night it answered ",
    el("strong", null, String(fl.A ?? "–")),
    rolledBack ? ", as before the night. The base model with yesterday's notes in its prompt answers "
               : ". The base model with yesterday's notes in its prompt answers ",
    el("strong", null, String(fl.C ?? "–")),
    ".");
  const f = forgotNumbers(ev);
  const line = $("forgot-line");
  line.replaceChildren();
  if (f) {
    const lost = f.after.pass < f.before.pass;
    line.append(rolledBack ? "What the update would have cost: on the " : lost ? "What it lost: on the "
                                                                                : "Did it forget anything? On the ",
      f.slice, " it scored ", el("strong", null, `${f.before.pass} of ${f.before.n}`),
      " before the night and ", el("strong", null, `${f.after.pass} of ${f.after.n}`), " with the update",
      lost || rolledBack ? "." : ": no loss on this slice.");
    if (f.passed === false) line.append(" That is more than the rule allows.");
    line.append(receiptLink("forgot"));
  }
}

function grid(facts) {
  const fig = $("grid");
  fig.querySelectorAll(".row, .legend").forEach((n) => n.remove());
  for (const row of ROWS) {
    if (!facts.some((f) => f[row.arm] != null)) continue;
    const r = el("div", "row");
    const label = el("div", "row-label");
    label.append(el("strong", null, row.title), document.createTextNode(row.sub));
    const dots = el("div", "dots");
    dots.setAttribute("aria-hidden", "true");
    let learned = 0;
    for (const f of facts) {
      const d = el("span", "dot");
      const s = dotState(f[row.arm] ?? 0);
      if (s === "on") learned += 1;
      d.dataset.fact = f.id;
      d.dataset.arm = row.arm;
      d.style.setProperty("--i", dots.children.length);
      if (s !== "off") d.classList.add(s);
      dots.append(d);
    }
    if (row.arm === "B") dots.classList.add("fill");  // the one moment: these fill in, left to right (CSS)
    r.append(label, dots, el("div", "row-count", `${learned} of ${facts.length}`));
    fig.insertBefore(r, $("grid-caption"));
  }
  const legend = el("div", "legend");
  for (const [cls, text] of [["on", "answers it"], ["partial", "1 of 3 questions"], ["", "does not"]]) {
    const s = el("span");
    s.append(el("span", `dot ${cls}`), document.createTextNode(text));
    legend.append(s);
  }
  fig.insertBefore(legend, $("grid-caption"));
}

function chose(cons) {
  // runs/<w>/<d>/night/consolidated.json: one outcome per note ({id, key, value, outcome, why}, plus "into" for
  // merged_as_duplicate and "superseded_by" for dropped_as_superseded) and the merges the model proposed that
  // the night job refused (overruled_merge_proposals: {id, model_proposed_merge_with, why}).
  if (!cons || !cons.outcomes) return;
  // Older files carry key/value only on the kept items, not on each outcome.
  const items = Object.fromEntries((cons.items || []).map((i) => [i.id, i]));
  const byId = Object.fromEntries(cons.outcomes.map((o) => [o.id, { key: items[o.id]?.key, value: items[o.id]?.value, ...o }]));
  const label = (id) => { const o = byId[id] || {}; return `${pretty(o.key || id)}: ${o.value ?? ""}`; };
  const by = (name) => cons.outcomes.filter((o) => o.outcome === name);
  const kept = by("kept"), merged = by("merged_as_duplicate"), dropped = by("dropped_as_superseded");
  const overruled = cons.overruled_merge_proposals || [];
  $("chose").hidden = false;
  $("chose-summary").replaceChildren(`From ${cons.outcomes.length} notes it kept `, el("strong", null, String(kept.length)),
    " to learn, merged ", el("strong", null, String(merged.length)), ` duplicate${merged.length === 1 ? "" : "s"} and dropped `,
    el("strong", null, String(dropped.length)), ` superseded value${dropped.length === 1 ? "" : "s"}.`);
  const list = $("chose-list");
  list.replaceChildren();
  const row = (what, ...parts) => {
    const li = el("li");
    const body = el("div");
    parts.forEach((p) => body.append(typeof p === "string" ? document.createTextNode(p) : p));
    li.append(el("span", "what", what), body);
    list.append(li);
  };
  for (const d of dropped) {
    const newer = byId[d.superseded_by];
    row("Dropped", el("span", "old", label(d.id)), newer ? `: you changed it to ${newer.value}.` : ".");
  }
  for (const m of merged) row("Merged", `${label(m.id)} is the same as ${label(m.into)}, so it is learned once.`);
  for (const p of overruled) {
    row("Kept apart", `The model proposed merging ${label(p.id)} with ${label(p.model_proposed_merge_with)}. `,
      `They stay separate${p.why ? `: ${p.why}` : ""}.`);
  }
  const det = $("chose-kept");
  det.querySelector("summary").textContent = `The ${kept.length} facts it trained on`;
  det.querySelector("ul").replaceChildren(...kept.map((k) => {
    const o = byId[k.id];
    const li = el("li");
    li.append(el("b", null, pretty(o.key || o.id)), `: ${o.value ?? ""}`);
    return li;
  }));
}

const CHECKS = { leak_check: "no held-out question in the training data",
                 gguf_matches_peft: "the served adapter file matches the trained one", learned_gate: "it learned enough",
                 forgot_gate: "it did not lose general skill", tool_gate: "no new tool-call failure",
                 abstention_gate: "it says it does not know about as often as before" };
const rate = (x) => `${(x * 100).toFixed(1)}%`;

function abstainLine(abst, decision) {
  // The abstention check: when the night's own decision did not include it, say it was added afterwards and
  // what it would have decided.
  const p = $("abstain-line");
  if (!abst || abst.base == null || abst.after == null) return;
  const added = !("abstention_gate" in ((decision || {}).checks || {}));
  p.replaceChildren(added ? "Abstention check (added 27 Oct after this night ran): " : "Abstention check: ",
    "base ", el("strong", null, rate(abst.base)), " → ", el("strong", null, rate(abst.after)),
    abst.rule ? ` (rule: ${abst.rule})` : "",  // the rule as the night wrote it
    abst.passed ? "; passed." : added ? "; under this check the night would roll back." : "; failed.");
  p.hidden = false;
}

function numbers(ev, decision, abst) {
  const box = $("numbers");
  box.replaceChildren();
  const add = (title, parts, field) => {
    const d = el("div", "number");
    d.append(el("h3", null, title));
    const p = el("p");
    parts.forEach((x) => p.append(typeof x === "string" ? document.createTextNode(x) : x));
    if (field) p.append(receiptLink(field));
    d.append(p);
    box.append(d);
  };
  const v = (t) => el("span", "value", t);
  const st = ev.stale || {};
  if (st.B && st.B.n) {  // stale probes count only once a correction has superseded a fact
    add("Stale values", ["After your corrections, the old value came back in ", v(`${st.B.stale} of ${st.B.n}`),
      " questions", st.A ? ` (before the night: ${st.A.stale} of ${st.A.n}).` : "."], "stale");
  } else {
    add("Stale values", ["Measured from day 2, after you correct something. Nothing to measure yet."], null);
  }
  const bc = ev.learned && ev.learned.B_minus_C;
  if (bc) {
    const ci = bc.ci95 ? ` (95% interval ${pct(bc.ci95[0])} to ${pct(bc.ci95[1])})` : "";
    add("Against the notes control", ["Per fact, the trained model with notes off scores ", v(`${pct(bc.est)} points`),
      ` against the base model reading its notes${ci}.`], "learned");
  }
  const checks = { ...((decision && decision.checks) || {}) };
  const added = abst && abst.passed != null && !("abstention_gate" in checks);
  if (added) checks.abstention_gate = abst.passed;  // the check added after the night ran
  const names = Object.keys(checks);
  if (names.length) {
    const passed = names.filter((k) => checks[k]);
    add(decision.decision === "rollback" ? "Why it was rolled back" : "Why it was kept",
      [`${passed.length} of ${names.length} checks passed: `,
       names.map((k) => `${CHECKS[k] || k}${checks[k] ? "" : " (failed)"}`).join("; "), ".",
       added && !abst.passed ? " The abstention check was added after this night ran; under it the night would roll back." : ""],
      "decision.checks");
  }
}

function pickProbe(f) {
  const pass = f.pass || {};
  const a = pass.A || [], b = pass.B || [];
  for (let i = 0; i < (f.probes || []).length; i++) if (b[i] && !a[i]) return i;
  for (let i = 0; i < (f.probes || []).length; i++) if (!b[i]) return i;
  return 0;
}

function asked(facts, questions) {
  const tbody = $("asked-table").querySelector("tbody");
  const learnedNow = facts.filter((f) => (f.B ?? 0) >= 2 && (f.A ?? 0) < 2);
  const missed = facts.filter((f) => (f.B ?? 0) < 2);
  const rest = facts.filter((f) => !learnedNow.includes(f) && !missed.includes(f));
  const first = [...learnedNow.slice(0, 6), ...missed];
  const render = (list) => {
    tbody.replaceChildren();
    for (const f of list) {
      const i = pickProbe(f);
      const tr = el("tr");
      const q = el("td", "q", questions[(f.probes || [])[i]] || f.key);
      q.append(el("span", "fact", `${f.key}: ${f.value}`));
      const cell = (arm, cls) => {
        const td = el("td", cls);
        const ok = ((f.pass || {})[arm] || [])[i];
        td.append(el("span", `mark ${ok ? "yes" : "no"}`, ok ? "✓" : "✗"));
        td.append(document.createTextNode(((f.answers || {})[arm] || [])[i] || "–"));
        return td;
      };
      tr.append(q, cell("A", "before"), cell("B", "after"));
      tbody.append(tr);
    }
  };
  render(first);
  const btn = $("show-all");
  btn.hidden = first.length >= facts.length;
  btn.onclick = () => { render([...learnedNow, ...missed, ...rest]); btn.hidden = true; };
}

function receipt(r) {
  const dl = $("receipt-list");
  dl.replaceChildren();
  const add = (k, val) => {
    const dt = el("dt", null, k);
    dt.dataset.key = k;
    dl.append(dt, el("dd", null, typeof val === "object" ? JSON.stringify(val) : String(val)));
  };
  const walk = (prefix, obj) => {
    for (const [k, val] of Object.entries(obj || {})) {
      const key = prefix ? `${prefix}.${k}` : k;
      if (val && typeof val === "object" && !Array.isArray(val) && Object.keys(val).length <= 12) walk(key, val);
      else add(key, val);
    }
  };
  const ev = r.eval || {};
  walk("", { runtime: ev.runtime, adapter: ev.adapter, forgot: ev.forgot, stale: ev.stale,
             learned: ev.learned, decision: r.decision, receipt: r.receipt, files: r.night_dir });
}

function tooltip(facts) {
  const tip = $("tip");
  const byId = Object.fromEntries(facts.map((f) => [f.id, f]));
  const names = { A: "before the night", B: "after the night", C: "with notes in its prompt" };
  document.addEventListener("mouseover", (e) => {
    const d = e.target.closest(".dot[data-fact]");
    if (!d) { tip.hidden = true; return; }
    const f = byId[d.dataset.fact];
    tip.replaceChildren(el("strong", null, `${f.key}: ${f.value}`),
      document.createTextNode(`${f[d.dataset.arm] ?? 0} of 3 questions answered ${names[d.dataset.arm]}`));
    const r = d.getBoundingClientRect();
    tip.hidden = false;
    tip.style.left = `${Math.min(r.left, innerWidth - tip.offsetWidth - 12)}px`;
    tip.style.top = `${r.bottom + 8}px`;
  });
}

function askBox() {
  const form = $("ask-form"), input = $("ask-input"), send = $("ask-send"), box = $("ask-answers");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const question = input.value.trim();
    if (!question) return;
    send.disabled = true;
    box.replaceChildren(el("div", "ask-answer", "Asking…"));
    try {
      const r = await (await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
                                                  body: JSON.stringify({ question }) })).json();
      box.replaceChildren();
      if (r.error) box.append(el("div", "ask-answer", r.error));
      if ((r.answers || []).length === 1)
        $("ask-hint").textContent = "Your question goes to the model with no notes and no tools. No night update is loaded, so it answers once.";
      for (const a of r.answers || []) {
        const card = el("div", "ask-answer" + (a.scale === 1 ? " after" : ""));
        card.append(el("div", "who", r.adapter ? a.label : `${a.label} (no update loaded yet)`), el("div", "text", a.content));
        box.append(card);
      }
    } finally {
      send.disabled = false;
    }
  });
}

function across(worlds, current, day) {
  // One line for every synthetic person, so no single world stands alone; each name opens its own report.
  if (!worlds || worlds.length < 2) return;
  const sum = (k) => worlds.reduce((t, w) => t + (w[k] ?? 0), 0);
  const line = $("across-line");
  line.replaceChildren(`Across ${worlds.length} synthetic people, after one night: `,
    el("strong", null, `${sum("B")} of ${sum("facts")}`), " facts answered with notes off, ",
    el("strong", null, String(sum("C"))), " with notes in the prompt, ",
    el("strong", null, String(sum("D"))), " with both.");
  const nav = $("people");
  nav.replaceChildren(...worlds.map((w) => {
    const a = el("a", "person");
    a.href = `/report?world=${encodeURIComponent(w.world)}&day=${day}`;
    if (w.world === current) a.setAttribute("aria-current", "page");
    const bar = el("span", "bar");
    bar.style.setProperty("--share", w.facts ? (w.B ?? 0) / w.facts : 0);
    a.append(el("span", "name", w.persona || w.world), bar, el("span", "count", `${w.B ?? "–"} of ${w.facts}`));
    a.title = `${w.persona || w.world}: ${w.B} of ${w.facts} with notes off, ${w.C} with notes, ${w.D} with both`;
    return a;
  }));
  $("across").hidden = false;
}

async function boot() {
  const qs = new URLSearchParams(location.search);
  const r = await (await fetch(`/api/report?${qs}`)).json();
  $("who").textContent = r.persona || r.world;
  askBox();
  if (r.missing) {
    $("verdict").textContent = "No night has run yet. After tonight's training, what it learned shows here.";
    document.querySelectorAll(".lead, .facts-grid, .numbers, .asked, .receipt").forEach((n) => (n.hidden = true));
    $("ask-hint").textContent = "Your question goes to the model with no notes and no tools.";
    return;
  }
  $("when").textContent = `The morning after day ${r.day}`;
  across(r.worlds, r.world, r.day);
  const ev = r.eval;
  const day = ev.eval_day || r.day;
  const facts = (ev.facts || []).filter((f) => (f.valid_from || 1) <= day);
  const rolledBack = (r.decision || {}).decision === "rollback";
  if (rolledBack) {  // the scored update is not the model you talk to: name it as the update, not "after"
    ROWS[1].title = "The update, rolled back";
    document.querySelectorAll("#asked-table th[data-arm='B']").forEach((th) => (th.textContent = "The update, rolled back"));
  }
  verdict(r);
  lead(ev, facts, rolledBack);
  chose(r.consolidated);
  grid(facts);
  abstainLine(r.abstention, r.decision);
  numbers(ev, r.decision, r.abstention);
  asked(facts, r.questions || {});
  receipt(r);
  tooltip(facts);
}

boot();
