import { describe, expect, test } from "bun:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { VoiceList, VoiceTag, voiceNotHeard } from "./Voice.jsx";

const presets = [
  { slug: "default", label: "Default", status: "ready" },
  { slug: "dry-wit", label: "Dry wit", status: "ready", line: "Short and dry." },
];
const state = (dials) => ({ presets, custom: [], choice: { preset: "dry-wit" }, dials });
const skippedVoice = state({ dials: [], skipped: [{ axis: "voice-dry-wit", toward: "dry", night: null, why: "the served build has no vector for this axis" }] });
const heardVoice = state({ dials: [{ id: 3, axis: "voice-dry-wit", toward: "dry", scale: 0.4, night: null }], skipped: [] });

describe("a voice the server has not loaded", () => {
  test("is not shown as heard", () => {
    expect(voiceNotHeard(skippedVoice)).toBe(true);
    expect(voiceNotHeard(heardVoice)).toBe(false);
    expect(voiceNotHeard({ ...heardVoice, choice: { preset: "default" } })).toBe(false);
  });
  test("a skipped night proposal alone does not count", () => {
    const v = state({ dials: [{ id: 3, axis: "voice-dry-wit", toward: "dry", scale: 0.4, night: null }],
      skipped: [{ axis: "concise", toward: "short", night: 3, why: "x" }] });
    expect(voiceNotHeard(v)).toBe(false);
  });
  test("the list and the tag say it turns on at the next restart", () => {
    expect(renderToStaticMarkup(<VoiceList voices={skippedVoice} onChange={() => {}} />)).toContain("next restart");
    expect(renderToStaticMarkup(<VoiceTag voices={skippedVoice} onChange={() => {}} />)).toContain("after restart");
    expect(renderToStaticMarkup(<VoiceList voices={heardVoice} onChange={() => {}} />)).not.toContain("next restart");
    expect(renderToStaticMarkup(<VoiceTag voices={heardVoice} onChange={() => {}} />)).not.toContain("after restart");
  });
});
