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
