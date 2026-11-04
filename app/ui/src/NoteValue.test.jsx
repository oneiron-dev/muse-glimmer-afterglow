import { describe, expect, test } from "bun:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { NoteValue, noteSearchText, wasValues } from "./NoteValue.jsx";

const render = (c) => renderToStaticMarkup(c);

describe("code-shaped notes stay off screen", () => {
  test("an access code is closed behind Hidden", () => {
    const html = render(<NoteValue k="warehouse entry code" value="QX-4471" />);
    expect(html).toContain("<details");
    expect(html).not.toContain("<details open");
    expect(html).toContain("Hidden");
  });
  test("passwords and PINs too, not ordinary notes", () => {
    expect(render(<NoteValue k="router password" value="x" />)).toContain("Hidden");
    expect(render(<NoteValue k="door PIN" value="1" />)).toContain("Hidden");
    const plain = render(<NoteValue k="arm hinge bolt torque" value="2.8 newton-metres" />);
    expect(plain).toBe('<span class="nv">2.8 newton-metres</span>');
  });
  test("search and history never carry a code value", () => {
    expect(noteSearchText({ key: "warehouse entry code", value: "QX-4471" })).toBe("warehouse entry code");
    expect(noteSearchText({ key: "office", value: "Room 3.07" })).toBe("office room 3.07");
    expect(wasValues({ key: "gullwing strut code" }, ["KF-1"])).toEqual([]);
    expect(wasValues({ key: "office" }, ["room 2.14"])).toEqual(["room 2.14"]);
  });
});

describe("a note whose whole value is an id stays off screen too (F69 torque audit file: DX-47B)", () => {
  test("ids by value: letters and digits in hyphen groups", async () => {
    const { isCodeValue } = await import("./lib.js");
    for (const v of ["DX-47B", "KF-GW-542", "P4-17", " QA-209-C "]) expect(isCodeValue(v)).toBe(true);
    for (const v of ["2026-11-12", "Pressmark P-310", "Morrow M-40", "extension 4429", "Room 3.07", "16:00", "A-B", "12-34"]) {
      expect(isCodeValue(v)).toBe(false);
    }
  });
  test("Memory, Removed, the side panel, search and was-history hide it", async () => {
    const { factRows } = await import("./lib.js");
    expect(render(<NoteValue k="torque audit file" value="DX-47B" />)).toContain("Hidden");
    expect(noteSearchText({ key: "torque audit file", value: "DX-47B" })).toBe("torque audit file");
    expect(wasValues({ key: "office", value: "Room 3.07" }, ["DX-47B", "room 2.14"])).toEqual(["room 2.14"]);
    const rows = factRows({ eval_day: 9, facts: [{ id: "F69", key: "torque audit document", value: "DX-47B" },
      { id: "F21", key: "arm hinge bolt torque", value: "2.8 newton-metres" }] });
    expect(rows.map((r) => [r.value, r.hidden])).toEqual([["(hidden)", true], ["2.8 newton-metres", false]]);
  });
});
