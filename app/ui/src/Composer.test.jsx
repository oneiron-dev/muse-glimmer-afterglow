import { expect, test } from "bun:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Composer } from "./Composer.jsx";

test("once the day has ended, the message box is closed until the next day opens", () => {
  const html = renderToStaticMarkup(<Composer ended voices={null} onSend={() => {}} />);
  expect(html).toContain("This day has ended. Open the next day to keep talking");
  expect(html).toMatch(/<textarea[^>]*disabled=""/);
  expect(html).not.toContain("Still here tonight");
  const open = renderToStaticMarkup(<Composer voices={null} onSend={() => {}} />);
  expect(open).not.toMatch(/<textarea[^>]*disabled=""/);
});
