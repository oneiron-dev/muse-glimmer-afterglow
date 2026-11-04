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
  });
  test("offers a left-out branch even when it has no saved facts, named by what you said", () => {
    const html = render(<MorningOffer day={1} leftOut={["b2"]} conflicts={[]} firstWords={{ b2: "The workshop moved to 10:00." }} />);
    expect(html).toContain("You said “The workshop moved to 10:00.”");
    expect(html).toContain("No new facts in it.");
    expect(html).not.toContain("b2<");
    expect(html).not.toContain("Branch");
  });
  test("offers an unanswered conflict even without left_out", () => {
    const html = render(<MorningOffer day={1} leftOut={[]} conflicts={[conflict]} />);
    expect(html).toContain("Your other version of the day");
    expect(html).toContain("Workshop time: 10:00");
    expect(html).toContain("later night");
  });
  test("omitting leftOut still offers conflict branches not yet added", () => {
    const html = render(<MorningOffer day={1} conflicts={[conflict]}
      resolution={{ keep_side: ["N2"], add_branches: [] }} />);
    expect(html).toContain("Your other version of the day");
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
    resolution={{ keep_side: ["N2"], add_branches: ["b2"] }} firstWords={{ b3: "Make it 11:00." }} />);
  expect(saved).not.toContain('role="radiogroup"');
  expect(saved).toContain("You said “Make it 11:00.”");
});

test("sealed edited day keeps the notice but disables the writing action", () => {
  const html = render(<Thread state={{ world: "w", day: 1, history: [] }}
    lp={{ loom: { line: "b2", sealed: true } }} />);
  expect(html).toContain("You are on an edited version");
  expect(html).toContain('class="link" disabled=""');
});

test("the question before tonight speaks in versions, and a saved answer is one quiet line", async () => {
  const { NightQuestion } = await import("./NightQuestion.jsx");
  const ask = render(<NightQuestion conflicts={[conflict]} extra={[]} />);
  expect(ask).toContain("Which is right?");
  expect(ask).toContain("Workshop time");
  expect(ask).toContain("In your other version.");
  expect(ask).not.toMatch(/branch|b2|line/i);
  const saved = render(<NightQuestion conflicts={[conflict]} extra={[]} resolution={{ keep_side: ["N2"], add_branches: [] }} />);
  expect(saved).toContain("Saved. Tonight I learn Workshop time: 10:00.");
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
