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
  test("offers a left-out branch even when it has no saved facts", () => {
    const html = render(<MorningOffer day={1} leftOut={["b2"]} conflicts={[]} />);
    expect(html).toContain("Branch b2");
    expect(html).toContain("No saved facts.");
  });
  test("offers an unanswered conflict even without left_out", () => {
    const html = render(<MorningOffer day={1} leftOut={[]} conflicts={[conflict]} />);
    expect(html).toContain("Branch b2");
    expect(html).toContain("workshop time: 10:00");
    expect(html).toContain("later night");
  });
  test("omitting leftOut still offers conflict branches not yet added", () => {
    const html = render(<MorningOffer day={1} conflicts={[conflict]}
      resolution={{ keep_side: ["N2"], add_branches: [] }} />);
    expect(html).toContain("Branch b2");
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
  expect(html).toContain("Back to the first");
});

test("morning answer offers explicit line and side choices, preserving saved side picks", () => {
  const initial = render(<MorningOffer day={1} leftOut={["b2"]} conflicts={[conflict]} />);
  expect(initial).toContain('role="radiogroup"');
  expect(initial).toContain('aria-checked="true"');
  expect(initial).toContain("09:00");
  expect(initial).toContain("10:00");
  const saved = render(<MorningOffer day={1} leftOut={["b3"]} conflicts={[conflict]}
    resolution={{ keep_side: ["N2"], add_branches: ["b2"] }} />);
  expect(saved).toContain('role="radio" aria-checked="true"');
  expect(saved).toContain("Branch b3");
});

test("sealed edited day keeps the notice but disables the writing action", () => {
  const html = render(<Thread state={{ world: "w", day: 1, history: [] }}
    lp={{ loom: { line: "b2", sealed: true } }} />);
  expect(html).toContain("You are on an edited version");
  expect(html).toContain('class="link" disabled=""');
});
