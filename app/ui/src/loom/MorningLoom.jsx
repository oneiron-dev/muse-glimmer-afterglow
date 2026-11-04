import { useEffect, useState } from "react";
import { getJSON } from "../lib.js";
import { MorningOffer } from "./MorningOffer.jsx";

/** The report's day, not the open day. A failed read stays quiet and never posts an answer. */
export function MorningLoom({ world, day }) {
  const [loom, setLoom] = useState(null);
  useEffect(() => {
    let active = true;
    setLoom(null);
    getJSON(`/api/loom?day=${day}`).then((r) => {
      if (active && r && !r.error) setLoom(r);
    }).catch(() => {});
    return () => { active = false; };
  }, [world, day]);
  if (!loom) return null;
  return <MorningOffer key={`${world}-${day}-${loom.line}-${JSON.stringify(loom.alternatives)}`}
    day={day} leftOut={loom.left_out} conflicts={loom.contradictions?.conflicts}
    extra={loom.contradictions?.extra} resolution={loom.resolution} versionWords={loom.version_words} />;
}
