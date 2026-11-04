import { useEffect, useRef, useState } from "react";
import { useSearch } from "@tanstack/react-router";
import { getJSON, resolveHour } from "../lib.js";
import { cue } from "../sound.js";
import Figure from "./Figure.jsx";
import "./explain.css";

async function* answers(id, text, signal) {
  const response = await fetch("/api/explain/ask", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, text }), signal,
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

export default function ExplainView() {
  const { id } = useSearch({ strict: false });
  const [doc, setDoc] = useState(null), [crop, setCrop] = useState(null);
  const [text, setText] = useState(""), [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const request = useRef(null);
  useEffect(() => {
    document.body.dataset.hour = resolveHour(localStorage.getItem("afterglow-sky") || "auto");
    return () => request.current?.abort();
  }, []);
  useEffect(() => {
    let active = true;
    request.current?.abort(); setBusy(false); setDoc(null); setCrop(null); setError(""); setMessages([]);
    if (!id) { setError("Open an explanation from your agent to see it here."); return; }
    getJSON(`/api/explain?id=${encodeURIComponent(id)}`).then(async (r) => {
      if (r.error) throw new Error(r.error);
      if (!active) return;
      setDoc(r);
      setMessages((r.records || []).filter((rec) => rec.role !== "event" && !rec.spec).slice(1));
      if (r.crop_url) {
        const image = await getJSON(r.crop_url);
        if (active && image.data_url) setCrop(image.data_url);
      }
    }).catch((e) => { if (active) setError(e.message || "This explanation could not be opened."); });
    return () => { active = false; request.current?.abort(); };
  }, [id]);
  const ask = async (event) => {
    event.preventDefault();
    const question = text.trim();
    if (!question || busy) return;
    cue("press"); setBusy(true); setError(""); setText("");
    setMessages((prev) => [...prev, { role: "user", content: question }, { role: "assistant", content: "" }]);
    const controller = new AbortController(); request.current = controller;
    try {
      for await (const ev of answers(id, question, controller.signal)) {
        if (ev.type === "error") throw new Error(ev.message || ev.error || "Your agent could not answer.");
        if (ev.type === "spec") setDoc((prev) => ({ ...prev, spec: ev.spec, title: ev.spec.title }));
        if (ev.type === "content") setMessages((prev) => prev.map((m, i) => i === prev.length - 1 ? { ...m, content: m.content + ev.text } : m));
      }
    } catch (e) { if (e.name !== "AbortError") setError(e.message); }
    finally { if (request.current === controller) { request.current = null; setBusy(false); } }
  };
  return <main className="ex-view" aria-label="Explanation">
    <div className="ex-scroll scroll">
      <header className="ex-heading"><span className="ex-eyebrow">Your agent explains</span><h1>{doc?.title || (error ? "Explanation" : "Opening your explanation…")}</h1></header>
      {doc && <>
        <figure className="ex-figure"><Figure spec={doc.spec} crop={crop} ocr={doc.ocr} /></figure>
        {!!doc.spec.steps?.length && <ol className="ex-steps">{doc.spec.steps.map((step, i) => <li key={i}>{step}</li>)}</ol>}
      </>}
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
