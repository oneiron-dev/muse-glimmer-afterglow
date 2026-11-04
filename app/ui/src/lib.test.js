import { expect, test } from "bun:test";
import { queueAttachment, setAttachmentScope, takeQueuedAttachments } from "./lib.js";

test("a file queued on one day never joins a message on another day (ids restart at A001 each day)", () => {
  setAttachmentScope("w1", 1);
  queueAttachment({ id: "A001", name: "report.txt" });
  setAttachmentScope("w1", 2);
  expect(takeQueuedAttachments()).toEqual([]);
  queueAttachment({ id: "A001", name: "notes.md" });
  expect(takeQueuedAttachments().map((a) => a.name)).toEqual(["notes.md"]);
  expect(takeQueuedAttachments()).toEqual([]);
});
