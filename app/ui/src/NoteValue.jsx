import { isCodeKey } from "./lib.js";

/** A note's value. A code-shaped one stays hidden until you open it, so it never shows by accident. */
export function NoteValue({ k, value }) {
  if (!isCodeKey(k)) return <span className="nv">{value}</span>;
  return <details className="nv nhidden"><summary>Hidden</summary>{value}</details>;
}

/** What a notes search matches: the key, and the value only when it is not code-shaped. */
export const noteSearchText = (n) => (isCodeKey(n.key) ? n.key : `${n.key} ${n.value}`).toLowerCase();

/** Earlier values for "was ...", none for a code-shaped key. */
export const wasValues = (n, values) => (isCodeKey(n.key) ? [] : values);
