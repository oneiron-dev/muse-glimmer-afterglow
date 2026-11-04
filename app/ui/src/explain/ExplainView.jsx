import { useEffect, useRef, useState } from "react";
import { useSearch } from "@tanstack/react-router";
import { getJSON, resolveHour } from "../lib.js";
import { cue } from "../sound.js";
import Figure from "./Figure.jsx";
import "./explain.css";

async function* answers(scope, text, signal) {
  const response = await fetch("/api/explain/ask", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...scope, text }), signal,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || "Your agent could not answer. Try again.");
  }
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      buffer = buffer.replace(/\r\n/g, "\n");
      let cut;
      while ((cut = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const data = frame.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
        if (data && data !== "[DONE]") yield JSON.parse(data);
      }
      if (done) break;
    }
  } finally { reader.releaseLock(); }
}

export function Explanation({ doc, emptyTitle = "Explanation" }) {
  return <>
    <header className="ex-heading"><span className="ex-eyebrow">Your agent explains</span><h1>{doc?.title || emptyTitle}</h1></header>
    {doc && <>
      <p className="ex-answer">{doc.spec.answer}</p>
      <figure className="ex-figure"><Figure spec={doc.spec} crop={doc.crop_url} anchors={doc.anchors} /></figure>
    </>}
  </>;
}

export default function ExplainView() {
  const { id, world, day } = useSearch({ strict: false });
  // Thread ids restart for every day and profile, so the window always names its own (the open day's when absent).
  const scope = { id, ...(world ? { world, day } : {}) };
  const query = new URLSearchParams(Object.entries(scope).filter(([, v]) => v != null)).toString();
  const [doc, setDoc] = useState(null);
  const [text, setText] = useState(""), [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const request = useRef(null);
  useEffect(() => {
    document.body.dataset.hour = resolveHour(localStorage.getItem("afterglow-sky") || "auto");
    return () => request.current?.abort();
  }, []);
  useEffect(() => {
    let active = true;
    request.current?.abort(); setBusy(false); setDoc(null); setError(""); setMessages([]);
    if (!id) { setError("Open an explanation from your agent to see it here."); return; }
    getJSON(`/api/explain?${query}`).then((r) => {
      if (r.error) throw new Error(r.error);
      if (!active) return;
      setDoc(r);
      const firstAnswer = (r.records || []).findIndex((rec) => rec.role === "assistant");
      setMessages((r.records || []).slice(firstAnswer + 1).filter((rec) => ["user", "assistant"].includes(rec.role)));
    }).catch((e) => { if (active) setError(e.message || "This explanation could not be opened."); });
    return () => { active = false; request.current?.abort(); };
  }, [query]);
  const ask = async (event) => {
    event.preventDefault();
    const question = text.trim();
    if (!question || busy) return;
    cue("press"); setBusy(true); setError(""); setText("");
    setMessages((prev) => [...prev, { role: "user", content: question }, { role: "assistant", content: "" }]);
    const controller = new AbortController(); request.current = controller;
    try {
      for await (const ev of answers(doc?.world ? { id, world: doc.world, day: doc.day } : scope, question, controller.signal)) {
        if (ev.type === "error") throw new Error(ev.message || ev.error || "Your agent could not answer.");
        if (ev.type === "spec") setDoc((prev) => ({ ...prev, spec: ev.spec, title: ev.spec.title }));
        if (ev.type === "content" || ev.type === "answer") setMessages((prev) => prev.map((m, i) => i === prev.length - 1 ? { ...m, content: ev.type === "answer" ? ev.text : m.content + ev.text } : m));
      }
    } catch (e) { if (e.name !== "AbortError") setError(e.message); }
    finally { if (request.current === controller) { request.current = null; setBusy(false); } }
  };
  return <main className="ex-view" aria-label="Explanation">
    <div className="ex-scroll scroll">
      <Explanation doc={doc} emptyTitle={error ? "Explanation" : "Opening your explanation…"} />
      <form className="ex-composer" onSubmit={ask}>
        <label className="sr" htmlFor="ex-question">Ask a follow-up</label>
        <input id="ex-question" className="finput" value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask a follow-up" autoComplete="off" disabled={!doc} maxLength={4000} />
        <button className="btn btn-send" type="submit" disabled={!doc || busy || !text.trim()}>{busy ? "Answering…" : "Ask"}</button>
      </form>
      {error && <p className="ex-error" role="alert">{error}</p>}
      <section className="ex-chat" aria-label="Follow-up conversation" aria-live="polite" aria-busy={busy}>
        {messages.map((m, i) => <div key={i} className={`ex-message ${m.role === "user" ? "ex-user" : "ex-assistant"}`}><span className="ex-eyebrow">{m.role === "user" ? "You" : "Your agent"}</span><p>{m.content || (busy ? "Answering…" : "")}</p></div>)}
      </section>
    </div>
  </main>;
}
