import { describe, expect, test } from "bun:test";
import { defaultNoteName } from "./FilesList.jsx";
import { explainHref } from "../ThreadsView.jsx";

describe("a new note never takes an existing note's name", () => {
  test("the default name carries the date and minute", () => {
    expect(defaultNoteName(new Date(2026, 10, 5, 2, 15))).toBe("note-2026-11-05-0215");
    expect(defaultNoteName(new Date(2026, 0, 9, 23, 5))).toBe("note-2026-01-09-2305");
  });
});

describe("an explanation link names its profile and day", () => {
  test("world and day go with the id", () => {
    expect(explainHref("t1", "w1-ines-varga", 10)).toBe("/explain?id=t1&world=w1-ines-varga&day=10");
  });
  test("without a scope the link is the bare id, as before", () => {
    expect(explainHref("t1")).toBe("/explain?id=t1");
  });
});
