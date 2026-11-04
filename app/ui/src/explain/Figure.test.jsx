import { describe, test, expect } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import Figure from "./Figure.jsx";

const item = (id, label = id) => ({ id, label, detail: "A short detail.", refs: ["f0"] });
const render = (body, extra = {}) => renderToStaticMarkup(<Figure spec={{ v: 1, title: "Explanation", body }} {...extra} />);

describe("fixed explainer figures", () => {
  test("flow renders all ordered cards and only adjacent arrows", () => {
    const html = render({ type: "flow", steps: [item("first"), item("next"), item("last")] });
    expect(html).toContain("first"); expect(html).toContain("last");
    expect(html.match(/marker-end=/g)).toHaveLength(2);
  });
  test("cycle keeps every outcome and its repeat or stop condition", () => {
    const html = render({ type: "cycle", steps: [item("train"), item("check")], outcomes: [
      { when: "All pass", item: item("keep"), resume: "repeat" },
      { when: "Any fail", item: item("rollback"), resume: "stop" },
    ] });
    for (const text of ["All pass", "Any fail", "rollback", "Returns to train", "Stops here"]) expect(html).toContain(text);
  });
  test("parts labels the whole and all parts without causal arrows", () => {
    const html = render({ type: "parts", whole: item("whole"), parts: [item("one"), item("two")] });
    expect(html).toContain("whole"); expect(html).toContain("two");
    expect(html).not.toContain("marker-end=");
  });
  test("comparison rows stay aligned and model text is escaped", () => {
    const html = render({ type: "compare", left: "Local", right: "Server", rows: [{ id: "privacy", aspect: "Privacy", left: "<script>bad</script>", right: "Remote", refs: ["f0"] }] });
    expect(html).toContain("<table"); expect(html).toContain('scope="row"');
    expect(html).toContain("&lt;script&gt;"); expect(html).not.toContain("<script>");
  });
  test("screenshot pins use host OCR anchors, not invented coordinates", () => {
    const html = render({ type: "screenshot", pins: [{ anchor: "b0", item: item("selected") }] }, { crop: "data:image/png;base64,AA==", ocr: { boxes: [{ id: "b0", box: [.1, .2, .2, .2] }] } });
    expect(Number(html.match(/left:([\d.]+)%/)[1])).toBeCloseTo(20);
    expect(Number(html.match(/top:([\d.]+)%/)[1])).toBeCloseTo(30);
    expect(html).toContain("selected");
  });
  test("text fallback never leaves a blank figure", () => {
    expect(render({ type: "text", text: "Which part would you like explained?" })).toContain("Which part");
  });
});
