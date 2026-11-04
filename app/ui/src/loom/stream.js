/**
 * POST url with body and yield each server-sent event. The frames are
 * read exactly like lib.js chatStream reads them ({type: "content" |
 * "tool_result" | "note" | "round_done" | "turn_done" | "error"}).
 * A refused request (409: the day is busy or sealed) yields one error
 * event; the caller's own message from the server wins when present.
 */
export async function* branchStream(url, body) {
  const resp = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    yield { type: "error", message: err.error || (resp.status === 409 ? "Not now. The day is busy or sealed." : "The message did not go through.") };
    return;
  }
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let cut;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      if (frame.startsWith("data: ")) yield JSON.parse(frame.slice(6));
    }
  }
}
