import { describe, expect, test } from "bun:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ThreadRecords } from "./ThreadsView.jsx";
import { PanelBoundary } from "./Panel.jsx";

const render = (c) => renderToStaticMarkup(c);

describe("threads panel", () => {
  test("an error reply for a thread from another profile or day does not break the page", () => {
    const html = render(<ThreadRecords detail={{ error: "no such thread" }} />);
    expect(html).toContain("not on this day any more");
  });
  test("shows the cleaned words and what was heard", () => {
    const html = render(<ThreadRecords detail={{ records: [{ role: "user", content: "um send it", clean: "Send it." }] }} />);
    expect(html).toContain("Send it.");
    expect(html).toContain("Heard: um send it");
  });
  test("waits while the thread loads", () => {
    expect(render(<ThreadRecords detail={undefined} />)).toContain("Loading");
  });
  test("a failed panel shows a line instead of the page breaking", () => {
    const b = new PanelBoundary({ children: null });
    b.state = PanelBoundary.getDerivedStateFromError(new Error("x"));
    expect(render(b.render())).toContain("could not open");
  });
});
