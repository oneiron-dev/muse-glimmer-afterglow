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
  // on a rollback, which checks failed, in words (the night's own "why" is gate shorthand; it is in the receipt)
  const failed = Object.entries(d.checks || {}).filter(([, ok]) => !ok).map(([k]) => CHECKS[k] || k);
  if (d.decision === "rollback" && failed.length)
    p.append(el("span", "why", `It failed ${failed.length === 1 ? "one check" : `${failed.length} checks`}: ${failed.join("; ")}.`));
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

function staleLine(ev, rolledBack) {
  // Under the headline, from day 2: after the corrections, how often the weights gave the old value, beside the
  // base model reading the notes (which can hold both values when a correction was not linked to its old note).
  const st = ev.stale || {};
  const p = $("stale-line");
  if (!(st.B && st.B.n)) return;
  p.replaceChildren(`After your corrections, ${rolledBack ? "the update" : "it"} gave an old value `,
    el("strong", null, `${st.B.stale} of ${st.B.n}`), " times",
    ...(st.C ? ["; the base model reading your notes gave one ", el("strong", null, `${st.C.stale} of ${st.C.n}`), " times."] : ["."]));
  p.hidden = false;
}

function staleCause(unlinked) {
  // Why old values came back: corrections the day never linked to the note they replace, so the night trained both
  // values (web.py unlinked_corrections, from the night's own outcomes and the world's corrections).
  const p = $("stale-cause");
  if (!unlinked || !unlinked.length) return;
  const n = unlinked.length;
  p.replaceChildren(`${n} correction${n > 1 ? "s were" : " was"} never linked to the note ${n > 1 ? "they replace" : "it replaces"}, so both values were learned: `,
    ...unlinked.flatMap((u, i) => [i ? "; " : "", el("strong", null, pretty(u.key)), ` (${u.old} and ${u.new})`]), ".");
  p.hidden = false;
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

function nightLinkControl(link) {
  // A night's guarded link applied to today's notes (agent.apply_night_links): Undo makes the old note active again
  // and is logged for the next night (web.py /api/night-link/undo).
  const box = el("span", "night-link");
  const status = el("span", "status");
  const show = (state) => {
    status.textContent = state === "applied" ? "Replaced in today's notes." : state === "undone"
      ? "Undone: the old note is back in today's notes." : `Applies to the notes of day ${link.applies_from_day ?? "+1"}.`;
    undo.hidden = state !== "applied";
  };
  const undo = el("button", "undo", "Undo");
  undo.type = "button";
  undo.onclick = async () => {
    const r = await fetch("/api/night-link/undo", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old_id: link.old_id }) });
    if (r.ok) show("undone");
  };
  box.append(" ", status, " ", undo);
  show(link.state);
  return box;
}

function nightQuestions(nightLinks) {
  // Links the night proposed but its guard refused (night-links.json "proposed"): the user answers Link (applied
  // like the night's own, marked as the user's) or Keep both (both notes stay; the night does not ask again).
  const props = (nightLinks && nightLinks.proposed) || [];
  if (!props.length) return;
  const list = $("chose-list");
  list.append(el("li", "chose-sub", "The night was not sure about these"));
  for (const p of props) {
    const li = el("li", "night-question");
    const status = el("span", "status");
    const actions = el("div", "actions");
    const show = (state) => {
      status.textContent = state === "linked by you" ? "Linked: the old note is replaced in today's notes."
        : state === "kept both" ? "Kept both." : state === "asked" ? "" : "Answer on the next day's report.";
      actions.querySelectorAll("button").forEach((b) => (b.hidden = state !== "asked"));
    };
    const btn = (label, answer) => {
      const b = el("button", answer === "link" ? "accept" : "dismiss", label);
      b.type = "button";
      b.onclick = async () => {
        const r = await fetch("/api/night-link/answer", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ old_id: p.old_id, new_id: p.new_id, answer }) });
        if (r.ok && !(await r.json()).error) show(answer === "link" ? "linked by you" : "kept both");
      };
      return b;
    };
    actions.append(btn("Link", "link"), btn("Keep both", "keep_both"), status);
    li.append(el("span", "what", "Asked"), el("div", null, p.question || `${p.new_key}: ${p.new_value} → ${p.old_key}?`),
      el("div", "why", p.reason || ""), actions);
    list.append(li);
    show(p.state);
  }
}

function chose(cons, nightLinks) {
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
    return body;
  };
  const links = (nightLinks && nightLinks.links) || [];
  // the day's own links ("you changed it") first, then the corrections the night found itself (consolidated.json
  // treated_as_correction_by_night: linked_by "readout" at p >= 0.9, "readout (a)" at p >= 0.6 with the same value
  // shape and a change word in the day's wording, or "consolidation rule")
  const found = dropped.filter((d) => d.treated_as_correction_by_night);
  for (const d of dropped.filter((d) => !d.treated_as_correction_by_night)) {
    const newer = byId[d.superseded_by];
    row("Dropped", el("span", "old", label(d.id)), newer ? `: you changed it to ${newer.value}.` : ".");
  }
  for (const m of merged) row("Merged", `${label(m.id)} is the same as ${label(m.into)}, so it is learned once.`);
  for (const p of overruled) {
    row("Kept apart", `The model proposed merging ${label(p.id)} with ${label(p.model_proposed_merge_with)}. `,
      `They stay separate${p.why ? `: ${p.why}` : ""}.`);
  }
  if (found.length) {
    list.append(el("li", "chose-sub", "The night treated these as corrections"));
  }
  for (const d of found) {
    const newer = byId[d.superseded_by];
    const how = d.linked_by === "consolidation rule" ? "by its own rule"
      : `p ${d.p != null ? Number(d.p).toFixed(2) : "?"}${d.change_word ? `, ‘${d.change_word}’` : ""}`;
    const body = row("Corrected", el("span", "old", label(d.id)), ` → ${newer ? newer.value : "a newer note"} (the night treated this as a correction: ${how}).`);
    const link = links.find((l) => l.old_id === d.id);
    if (link) body.append(nightLinkControl({ ...link, applies_from_day: nightLinks.applies_from_day }));
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

const sign = (x) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(1)}`;

function sampleTag(id, path) {
  const tag = $(id);
  tag.hidden = !path;
  if (path) { tag.textContent = "SAMPLE"; tag.title = `Not this night's own file: ${path}`; }
}

function behaviour(st) {
  // How the night's update moved the way it answers, per axis: the night's label, with the shift and its 95%
  // interval drawn on one shared scale (0 = no change). Measured by the behaviour check, never a judgement of
  // the user.
  const bc = st && st.behaviour_check;
  if (!bc || !(bc.axes || []).length) return;
  sampleTag("behaviour-sample", st.sample && st.sample.behaviour_check);
  const set = (bc.probes && bc.probes.headline) || "everyday prompts";
  const n = (bc.probes || {})[set];
  $("behaviour-summary").textContent = `How last night's update changed the way it answers${n ? ` ${n}` : ""} ${set}. ` +
    "Each line runs between two ways of answering; the dot is the change, the band its 95% interval.";
  const span = Math.max(1, ...bc.axes.flatMap((a) => (a.ci95 || [a.shift]).map(Math.abs)));
  const at = (x) => `${50 + (x / span) * 50}%`;
  $("behaviour-axes").replaceChildren(...bc.axes.map((a) => {
    const [pos, neg] = a.poles || ["", ""];
    const li = el("li", "axis" + (a.label === "no clear change" ? " flat" : "") + (a.axis === "abstention" ? " abstain" : ""));
    const track = el("div", "track");
    track.setAttribute("aria-hidden", "true");
    if (a.ci95) {
      const band = el("span", "band");
      band.style.left = at(Math.min(...a.ci95));
      band.style.width = `${(Math.abs(a.ci95[1] - a.ci95[0]) / span) * 50}%`;
      track.append(band);
    }
    const dot = el("span", "dot");
    dot.style.left = at(a.shift);
    track.append(el("span", "zero"), dot);
    const what = el("div", "what", a.label);
    what.append(el("span", "shift", ` ${sign(a.shift)}` + (a.ci95 ? ` (${sign(a.ci95[0])} to ${sign(a.ci95[1])})` : "")));
    if (a.axis === "abstention") what.append(el("span", "why", "This is the abstention check above, on everyday prompts."));
    li.append(el("span", "pole neg", neg), track, el("span", "pole pos", pos), what);
    return li;
  }));
  $("behaviour").hidden = false;
}

function proposes(st, world, day) {
  // Up to two dial changes Glimmer proposes after reading the day and the behaviour check. Nothing changes
  // until the user accepts; an accepted dial applies from the next server start that can serve it.
  const doc = st && st.proposals;
  const list = (doc && doc.proposals) || [];
  if (!list.length) return;
  sampleTag("proposes-sample", st.sample && st.sample.proposals);
  $("proposes-summary").textContent = "It read yesterday and the behaviour check and suggests " +
    `${list.length === 1 ? "one change" : `${list.length} changes`} to how it answers. Nothing changes until you accept.`;
  const answers = st.decisions || {};
  $("proposals").replaceChildren(...list.map((p) => {
    const li = el("li", "proposal");
    const key = `${p.axis}:${p.toward}`;
    const status = el("span", "status");
    const show = (a) => {
      li.dataset.answer = a ? a.answer : "";
      status.textContent = !a ? "" : a.answer === "accept" ? "Accepted: applies tomorrow" : "Dismissed";
    };
    const btn = (label, answer, cls) => {
      const b = el("button", cls, label);
      b.type = "button";
      b.onclick = async () => {
        const r = await fetch("/api/proposal", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ world, day, axis: p.axis, toward: p.toward, answer }) });
        if (r.ok) show((await r.json())[key]);
      };
      return b;
    };
    const actions = el("div", "actions");
    actions.append(btn("Accept", "accept", "accept"), btn("Dismiss", "dismiss", "dismiss"), status);
    li.append(el("div", "toward", `More ${p.toward}`), el("p", "reason", p.reason || ""), actions);
    show(answers[key]);
    return li;
  }));
  $("proposes").hidden = false;
}

const CHECKS = { leak_check: "no held-out question in the training data",
                 gguf_matches_peft: "the served adapter file matches the trained one", learned_gate: "it learned enough",
                 forgot_gate: "it did not lose general skill",
                 tool_gate: "no new failures on the 20 tool questions in the eval form",
                 content_channel: "its chat replies land in the reply, not only in its reasoning",
                 tool_calls: "it still calls its tools in chat, as the day agent does",
                 abstention_gate: "it says it does not know about as often as before",
                 abstention_honesty: "graded honesty: right minus wrong did not fall, it kept what it knew, and it still says \"not in your notes\" to things you never said" };

function plainRule(rule) {
  // The two-sided abstention rule as the night writes it, in plain words; any other rule as written.
  const m = /abs\(after - base\) <= ([\d.]+) and right_after >= right_base - (\d+)/.exec(rule || "");
  if (!m) return rule;
  return `a night must keep its "I don't know" rate within ${Math.round(parseFloat(m[1]) * 100)} points of the base ` +
    `and lose no more than ${m[2]} right answers`;
}

function chatLines(checks) {
  // The two chat checks: the night's own, or added after it ran (then: what it would have decided).
  const box = $("chat-lines");
  box.replaceChildren(...(checks || []).map((c) => {
    const k = c.counts || {};
    const p = el("p", "abstain-line");
    const title = c.name === "content_channel" ? "Answer-channel check" : "Chat tool check";
    const what = c.name === "content_channel"
      ? [el("strong", null, `${k.right} of ${k.n}`), " chat replies land in the reply",
         k.min_rate != null ? ` (bar: ${Math.round(k.min_rate * 100)}%)` : ""]
      : [el("strong", null, `${k.right} of ${k.n}`), " tool calls right in chat, base ",
         el("strong", null, `${k.base_right} of ${k.n}`), k.max_drop != null ? ` (bar: no more than ${k.max_drop} below the base)` : ""];
    p.append(c.added ? `${title} (added ${c.added} after this night ran): ` : `${title}: `, ...what,
      c.passed ? "; passed." : c.added ? "; under this check the night would roll back." : "; failed.");
    return p;
  }));
  box.hidden = !(checks || []).length;
}

function addedChecks(checks, chat) {
  // decision.json's checks plus every check added after the night ran (with its result from its own file).
  for (const c of chat || []) if (c.added && !(c.name in checks)) checks[c.name] = c.passed;
  return (chat || []).filter((c) => c.added && !c.passed).map((c) => c.name);
}
const rate = (x) => `${(x * 100).toFixed(1)}%`;

function abstainLine(abst, decision) {
  // The abstention check: when the night's own decision did not include it, say it was added afterwards and
  // what it would have decided.
  const p = $("abstain-line");
  if (!abst || abst.base == null || abst.after == null) return;
  const added = !("abstention_gate" in ((decision || {}).checks || {}));
  p.replaceChildren(added ? "Abstention check (added 27 Oct after this night ran): " : "Abstention check: ",
    "base ", el("strong", null, rate(abst.base)), " → ", el("strong", null, rate(abst.after)),
    abst.rule ? ` (rule: ${plainRule(abst.rule)})` : "",  // the night's rule in plain words; the code text is in the receipt
    abst.passed ? "; passed." : added ? "; under this check the night would roll back." : "; failed.",
    " ", receiptLink("abstention.rule"));
  p.hidden = false;
}

function neverToldLine(line) {
  // As served (route decision on), and what the note check stopped in the route-off control (web.py never_told).
  const p = $("never-told-line");
  if (!line) return;
  p.textContent = line;
  p.hidden = false;
}

function numbers(ev, decision, abst, chat) {
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
      " questions", st.A ? ` (before the night: ${st.A.stale} of ${st.A.n})` : "",
      ...(st.C ? [". The base model reading your notes gave the old value in ", v(`${st.C.stale} of ${st.C.n}`), "."] : ["."])],
      "stale");
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
  const failedAdded = addedChecks(checks, chat);
  if (added && !abst.passed) failedAdded.unshift("abstention_gate");
  const names = Object.keys(checks);
  if (names.length) {
    const passed = names.filter((k) => checks[k]);
    add(decision.decision === "rollback" ? "Why it was rolled back" : "Why it was kept",
      [`${passed.length} of ${names.length} checks passed: `,
       names.map((k) => `${CHECKS[k] || k}${checks[k] ? "" : " (failed)"}`).join("; "), ".",
       failedAdded.length ? ` ${failedAdded.length === 1 ? "The failed check was" : `The ${failedAdded.length} failed checks were`} ` +
         `added after this night ran; under ${failedAdded.length === 1 ? "it" : "them"} the night would roll back.` : ""],
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
             learned: ev.learned, decision: r.decision, abstention: r.abstention,
             chat_checks: Object.fromEntries((r.chat_checks || []).map((c) => [c.name, c])),
             receipt: r.receipt, files: r.night_dir });
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
  line.replaceChildren(`Across ${worlds.length} synthetic people, after night ${day}: `,
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
  staleLine(ev, rolledBack);
  staleCause(r.unlinked_corrections);
  chose(r.consolidated, r.night_links);
  nightQuestions(r.night_links);
  grid(facts);
  abstainLine(r.abstention, r.decision);
  neverToldLine(r.never_told);
  chatLines(r.chat_checks);
  behaviour(r.steering);
  proposes(r.steering, r.world, r.day);
  numbers(ev, r.decision, r.abstention, r.chat_checks);
  asked(facts, r.questions || {});
  receipt(r);
  tooltip(facts);
}

boot();
