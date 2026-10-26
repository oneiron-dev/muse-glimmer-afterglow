"use strict";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
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
  if (d.why) p.append(el("span", "why", d.why));
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

function lead(ev, facts) {
  const fl = (ev.learned && ev.learned.facts_learned) || {};
  const n = facts.length;
  const hero = $("hero");
  hero.replaceChildren(document.createTextNode(String(fl.B ?? "–")), el("span", "of", ` of ${n}`));
  const label = $("hero-label");
  label.replaceChildren(
    "facts it now answers with notes off. Before the night it answered ",
    el("strong", null, String(fl.A ?? "–")),
    ". The base model with yesterday's notes in its prompt answers ",
    el("strong", null, String(fl.C ?? "–")),
    ".");
  const f = forgotNumbers(ev);
  const line = $("forgot-line");
  line.replaceChildren();
  if (f) {
    line.append("What it lost: on the ", f.slice, " it scored ", el("strong", null, `${f.before.pass} of ${f.before.n}`),
      " before the night and ", el("strong", null, `${f.after.pass} of ${f.after.n}`), " after.");
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

const CHECKS = { leak_check: "no held-out question in the training data", learned_gate: "it learned enough",
                 forgot_gate: "it did not lose general skill", tool_gate: "no new tool-call failure" };

function numbers(ev, decision) {
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
  const checks = (decision && decision.checks) || {};
  const names = Object.keys(checks);
  if (names.length) {
    const passed = names.filter((k) => checks[k]);
    add(decision.decision === "rollback" ? "Why it was rolled back" : "Why it was kept",
      [`${passed.length} of ${names.length} checks passed: `,
       names.map((k) => `${CHECKS[k] || k}${checks[k] ? "" : " (failed)"}`).join("; "), "."], "decision.checks");
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

async function boot() {
  const qs = new URLSearchParams(location.search);
  const r = await (await fetch(`/api/report?${qs}`)).json();
  $("who").textContent = r.persona || r.world;
  if (r.missing) {
    $("verdict").textContent = "No night has run yet. After tonight's training, what it learned shows here.";
    document.querySelectorAll(".lead, .facts-grid, .numbers, .asked, .receipt").forEach((n) => (n.hidden = true));
    return;
  }
  $("when").textContent = `The morning after day ${r.day}`;
  const ev = r.eval;
  const day = ev.eval_day || r.day;
  const facts = (ev.facts || []).filter((f) => (f.valid_from || 1) <= day);
  verdict(r);
  lead(ev, facts);
  grid(facts);
  numbers(ev, r.decision);
  asked(facts, r.questions || {});
  receipt(r);
  tooltip(facts);
}

boot();
