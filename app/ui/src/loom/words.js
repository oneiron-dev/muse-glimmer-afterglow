/** Plain words for the loom's data: a note key as a label, and a side branch named by what you said on it. */
export const factLabel = (key) => {
  const k = String(key || "").trim();
  return k ? k[0].toUpperCase() + k.slice(1) : k;
};

/** A side branch as the person knows it: the first thing they said on it, quoted, else a plain fallback. */
export const versionLabel = (branch, firstWords) => {
  const text = firstWords && firstWords[branch];
  if (!text) return "Your other version of the day";
  const t = text.trim();
  return `You said “${t.length > 90 ? `${t.slice(0, 88).trimEnd()}…` : t}”`;
};
