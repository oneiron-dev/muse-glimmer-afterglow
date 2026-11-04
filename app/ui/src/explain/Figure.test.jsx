import { describe, test, expect } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import Figure from "./Figure.jsx";
import { Explanation } from "./ExplainView.jsx";

const item = (id, label = id) => ({ id, label, detail: "A short detail." });
const render = (body, extra = {}) => renderToStaticMarkup(<Figure spec={{ v: 2, title: "Explanation", body }} {...extra} />);
const anchors = [{ id: "b0", text: "Model", box: [0.1, 0.2, 0.2, 0.2] }, { id: "v0", text: "Send (disabled)", box: [0.6, 0.7, 0.3, 0.1] }];

describe("explainer figures", () => {
  test("the answer comes under the title and before the figure, and the crop is the PNG route", () => {
    const doc = { title: "Model setup", spec: { v: 2, answer: "You can chat here, but this Mac cannot train.",
      body: { type: "screenshot", pins: [{ anchor: "b0", ...item("gpu") }] } },
      crop_url: "/api/explain/crop?id=t1", anchors };
    const html = renderToStaticMarkup(<Explanation doc={doc} />);
    expect(html.indexOf("Model setup")).toBeLessThan(html.indexOf(doc.spec.answer));
    expect(html.indexOf(doc.spec.answer)).toBeLessThan(html.indexOf("<figure"));
    expect(html).toContain('src="/api/explain/crop?id=t1"');
  });
  test("a new spec re-renders the figure", () => {
    const doc = { title: "T", spec: { v: 2, answer: "A.", body: { type: "list", items: [item("one")] } }, anchors: [] };
    const before = renderToStaticMarkup(<Explanation doc={doc} />);
    const after = renderToStaticMarkup(<Explanation doc={{ ...doc, spec: { ...doc.spec, body: { type: "flow", steps: [item("day"), item("night")] } } }} />);
    expect(before).toContain("<ol"); expect(before).not.toContain("<svg");
    expect(after).toContain("<svg"); expect(after).toContain("night");
  });
  test("pins mark the host's places in the picture, numbered like the notes", () => {
    const html = render({ type: "screenshot", pins: [{ anchor: "v0", ...item("send", "Send") }, { anchor: "b0", ...item("model", "Model") }] },
      { crop: "/api/explain/crop?id=t1", anchors });
    expect(html).toContain("left:60.00%;top:70.00%;width:30.00%;height:10.00%");
    expect(html).toContain("left:10.00%;top:20.00%");
    expect(html.match(/class="ex-pin">(\d)</g)).toEqual(['class="ex-pin">1<', 'class="ex-pin">2<']);
    expect(html.indexOf("Send")).toBeLessThan(html.indexOf("Model"));
  });
  test("a pin without a known place is listed but not drawn", () => {
    const html = render({ type: "screenshot", pins: [{ anchor: "b9", ...item("ghost") }] }, { crop: "/x.png", anchors });
    expect(html).not.toContain("ex-mark"); expect(html).toContain("ghost");
  });
  test("a list is a numbered list, not a diagram", () => {
    const html = render({ type: "list", items: [item("model"), item("hardware"), item("training")] });
    expect(html).toContain('<ol class="ex-notes">');
    expect(html.match(/<li>/g)).toHaveLength(3);
    expect(html).not.toContain("<svg"); expect(html).not.toContain("<img");
  });
  test("flow draws every step with arrows only between neighbours", () => {
    const html = render({ type: "flow", steps: [item("first"), item("next"), item("last")] });
    expect(html).toContain("first"); expect(html).toContain("last");
    expect(html.match(/marker-end=/g)).toHaveLength(2);
  });
  test("cycle closes the loop and keeps every outcome", () => {
    const html = render({ type: "cycle", steps: [item("train"), item("check"), item("serve")], outcomes: [
      { ...item("keep"), when: "All pass", resume: "stop" },
      { ...item("undo"), when: "Any fail", resume: "repeat" },
    ] });
    expect(html.match(/marker-end=/g)).toHaveLength(3);
    for (const text of ["All pass", "Any fail", "undo", "Back to train", "Stops here"]) expect(html).toContain(text);
  });
  test("compare rows stay aligned and the model's words are escaped", () => {
    const html = render({ type: "compare", left: "Local", right: "Server", rows: [{ id: "r1", aspect: "Privacy", left: "<script>bad</script>", right: "Remote" }] });
    expect(html).toContain("<table"); expect(html).toContain('scope="row"');
    expect(html).toContain("&lt;script&gt;"); expect(html).not.toContain("<script>");
  });
  test("text shows the picture when there is one, and is never blank", () => {
    expect(render({ type: "text", text: "Ask about one part." })).toContain("Ask about one part.");
    expect(render({ type: "text", text: "Ask about one part." }, { crop: "/x.png" })).toContain('src="/x.png"');
  });
});
