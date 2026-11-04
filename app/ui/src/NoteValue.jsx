import { isCodeNote, isCodeValue } from "./lib.js";

/** A note's value. A code-shaped one (by its key or its whole value) stays hidden until you open it, so it never
 shows by accident. */
export function NoteValue({ k, value }) {
  if (!isCodeNote(k, value)) return <span className="nv">{value}</span>;
  return <details className="nv nhidden"><summary>Hidden</summary>{value}</details>;
}

/** What a notes search matches: the key, and the value only when it is not code-shaped. */
export const noteSearchText = (n) => (isCodeNote(n.key, n.value) ? n.key : `${n.key} ${n.value}`).toLowerCase();

/** Earlier values for "was ...": none for a code-shaped note, and never an earlier value that is itself an id. */
export const wasValues = (n, values) => (isCodeNote(n.key, n.value) ? [] : values.filter((v) => !isCodeValue(v)));
