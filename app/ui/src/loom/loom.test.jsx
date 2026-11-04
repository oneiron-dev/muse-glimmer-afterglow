import { describe, expect, test } from "bun:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MorningOffer } from "./MorningOffer.jsx";
import { EditedDay } from "./EditedDay.jsx";
import { MorningCard, Thread } from "../Thread.jsx";

const conflict = { key: "workshop time", line: { id: "N1", value: "09:00" },
  side: { id: "N2", value: "10:00", branch: "b2" } };
const render = (component) => renderToStaticMarkup(component);

describe("morning loom", () => {
  test("stays quiet with no left-out branch or unanswered conflict", () => {
    expect(render(<MorningOffer day={1} leftOut={[]} conflicts={[]} extra={[]} />)).toBe("");
    expect(render(<MorningOffer day={1} leftOut={[]} conflicts={[conflict]}
      resolution={{ keep_side: ["N2"], add_branches: ["b2"] }} />)).toBe("");
    // answered before the night: the other side of a conflict is not offered again
    expect(render(<MorningOffer day={1} leftOut={["b2"]} conflicts={[conflict]}
      resolution={{ keep_side: [], add_branches: [] }} />)).toBe("");
  });
  test("offers a left-out branch even when it has no saved facts, named by what you said", () => {
    const html = render(<MorningOffer day={1} leftOut={["b2"]} conflicts={[]} versionWords={{ b2: "The workshop moved to 10:00." }} />);
    expect(html).toContain("You said “The workshop moved to 10:00.”");
    expect(html).toContain("No new facts in it.");
    expect(html).not.toContain("b2<");
    expect(html).not.toContain("Branch");
  });
  test("offers an unanswered conflict even without left_out", () => {
    const html = render(<MorningOffer day={1} leftOut={[]} conflicts={[conflict]} />);
    expect(html).toContain("Your other version of the day");
    expect(html).toContain("Workshop time: 10:00");
    expect(html).toContain("Last night’s training used the version of day 1");
    expect(html).not.toMatch(/learn/i); // nothing reads the morning answer yet: it is only saved
  });
  test("after an answer, a version is still offered for a fact that disagrees with nothing", () => {
    const extra = { key: "standup time", value: "10:30", id: "N3", branch: "b2" };
    const html = render(<MorningOffer day={1} conflicts={[conflict]} extra={[extra]}
      resolution={{ keep_side: ["N2"], add_branches: [] }} />);
    expect(html).toContain("Your other version of the day");
    expect(html).toContain("Standup time: 10:30");
    expect(html).not.toContain('role="radiogroup"'); // answered before the night: not asked again
  });
  test("both morning verdicts render without assuming loom is present", () => {
    for (const decision of ["keep", "rollback"]) {
      const html = render(<MorningCard report={{ world: "w", day: 1, decision: { decision } }} />);
      expect(html).toContain(decision === "keep" ? "and kept it." : "and rolled it back.");
      expect(html).not.toContain("Left out last night");
    }
  });
});

test("edited-day notice is hidden on main and visible below an edited day's divider", () => {
  expect(render(<EditedDay line="main" />)).toBe("");
  const html = render(<Thread state={{ world: "w", day: 1, date: "2026-11-04", history: [] }}
    lp={{ loom: { line: "b2" } }} />);
  expect(html.indexOf('class="divider"')).toBeLessThan(html.indexOf("You are on an edited version"));
  expect(html).toContain("Back to your first version");
});

test("morning answer offers explicit line and side choices, preserving saved side picks", () => {
  const initial = render(<MorningOffer day={1} leftOut={["b2"]} conflicts={[conflict]} />);
  expect(initial).toContain('role="radiogroup"');
  expect(initial).toContain('aria-checked="true"');
  expect(initial).toContain("09:00");
  expect(initial).toContain("10:00");
  const saved = render(<MorningOffer day={1} leftOut={["b3"]} conflicts={[conflict]}
    extra={[{ key: "room", value: "Cedar", id: "N5", branch: "b3" }]}
    resolution={{ keep_side: ["N2"], add_branches: ["b2"] }} versionWords={{ b3: "Make it 11:00." }} />);
  expect(saved).not.toContain('role="radiogroup"');
  expect(saved).toContain("You said “Make it 11:00.”");
});

test("sealed edited day keeps the notice but disables the writing action", () => {
  const html = render(<Thread state={{ world: "w", day: 1, history: [] }}
    lp={{ loom: { line: "b2", sealed: true } }} />);
  expect(html).toContain("You are on an edited version");
  expect(html).toContain('class="link" disabled=""');
});

test("the question before tonight picks a version, the thing the night trains, and promises no fact", async () => {
  const { NightQuestion } = await import("./NightQuestion.jsx");
  const ask = render(<NightQuestion conflicts={[conflict]} line="main" versionWords={{ b2: "The workshop is at 10:00." }} />);
  expect(ask).toContain("Which is right?");
  expect(ask).toContain("Tonight’s training uses the version you pick.");
  expect(ask).toContain("The version of today you are on");
  expect(ask).toContain("Workshop time: 09:00");
  expect(ask).toContain("You said “The workshop is at 10:00.”");
  expect(ask).toContain("Workshop time: 10:00");
  expect(ask).not.toMatch(/branch|b2|learn|role="checkbox"/i);
  const saved = render(<NightQuestion conflicts={[conflict]} line="main" resolution={{ keep_side: [], add_branches: [] }} />);
  expect(saved).toContain("Saved. Tonight’s training uses the version you picked.");
  expect(saved).toContain(">Change<");
});

test("version steppers: the answers under a reply are bounded, never wrap", async () => {
  const { BranchSwitcher } = await import("./BranchSwitcher.jsx");
  const v = [{ branch: "b2", turn: 3, text: "x" }, { branch: "b3", turn: 4, text: "x" }];
  const html = render(<BranchSwitcher alternatives={v} turn={4} currentBranch="b3" kind="answer" />);
  expect(html).toContain('aria-label="Answers to this message"');
  expect(html).toContain("2/2");
  expect(html).toMatch(/aria-label="Next answer"[^>]*disabled|disabled=""[^>]*aria-label="Next answer"/);
  expect(render(<BranchSwitcher alternatives={[v[0]]} turn={3} currentBranch="b2" />)).toBe("");
});

test("?fact= opens the morning proof on that fact (the S8 shot)", () => {
  const fact = (id, key) => ({ id, key, value: "v", pass: { A: [false, false, false], B: [true, true, false] },
    answers: { A: [`${key} before`], B: [`${key} after`] } });
  const report = { world: "w", day: 9, decision: { decision: "keep" },
    eval: { eval_day: 9, facts: [fact("F1", "workshop town"), fact("F21", "arm hinge bolt torque")] } };
  globalThis.window = { location: { search: "?fact=F21" } };
  try {
    const html = render(<MorningCard report={report} />);
    expect(html).toContain("2 of 2");
    expect(html).toContain("arm hinge bolt torque after");
  } finally {
    delete globalThis.window;
  }
});

test("a rolled-back morning names the update kept before that night, not today's latest", () => {
  const nights = [{ day: 1, decision: "keep" }, { day: 2, decision: "rollback" }, { day: 3, decision: "rollback" }];
  const html = render(<MorningCard report={{ world: "w", day: 3, decision: { decision: "rollback" }, nights, served: { day: 9 } }} />);
  expect(html).toContain("You still talk to night 1’s update.");
  expect(html).not.toContain("night 9");
  const first = render(<MorningCard report={{ world: "w", day: 1, decision: { decision: "rollback" }, nights: [{ day: 1, decision: "rollback" }] }} />);
  expect(first).toContain("Your notes stay, and tonight I try again.");
  expect(first).not.toContain("talk to night");
});

test("the morning proof never steps onto a code-shaped fact: the torque fact stays 3, of 49 (the S8 shot)", async () => {
  const { learnedExamples } = await import("../lib.js");
  const fact = (id, key, value) => ({ id, key, value, pass: { A: [false, false, false], B: [true, true, false] },
    answers: { A: [`${key} before`], B: [`${value} after`] } });
  const facts = [fact("F1", "employer", "Kestrel Fabrication"), fact("F2", "workshop town", "Arnholt"),
    fact("F21", "arm hinge bolt torque", "2.8 newton-metres"), fact("F30", "battery pack model", "CV-22"),
    fact("F58", "warehouse access code", "77410"), fact("F80", "freight booking reference", "BK-5391-QT"),
    fact("F141", "test-rig Wi-Fi network", "KF-RIG-6G")];
  const ex = learnedExamples({ eval_day: 9, facts });
  expect(ex.map((e) => e.id)).toEqual(["F1", "F2", "F21"]);
  expect(JSON.stringify(ex)).not.toMatch(/CV-22|BK-5391-QT|KF-RIG-6G|77410/);
});
