// Drive a page in a private headless Chrome over CDP: run steps, take screenshots, report console errors.
//   node drive_page.mjs <url> <out-prefix> '<json steps>'
// steps: [{"click": "css"}, {"wait": ms}, {"shot": "name", "w": 1440, "h": 1000, "full": true}, {"eval": "js"}, {"type": ["css", "text"]}]
import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [url, prefix, stepsJson] = process.argv.slice(2);
const steps = JSON.parse(stepsJson || "[]");
const port = 9300 + Math.floor(Math.random() * 500);
const prof = mkdtempSync(join(process.env.CLAUDE_JOB_DIR ? join(process.env.CLAUDE_JOB_DIR, "tmp") : tmpdir(), "cdp-"));
const chrome = spawn("/usr/bin/google-chrome-stable",
  ["--headless=new", "--no-sandbox", `--remote-debugging-port=${port}`, `--user-data-dir=${prof}`, "--hide-scrollbars", "--disable-gpu", "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let target;
for (let i = 0; i < 50; i++) { try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((t) => t.type === "page"); if (target) break; } catch {} await sleep(150); }
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0; const pending = new Map(); const errors = [];
ws.onmessage = (m) => {
  const d = JSON.parse(m.data);
  if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); }
  if (d.method === "Runtime.exceptionThrown") errors.push("exception: " + (d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text));
  if (d.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(d.params.type)) errors.push(d.params.type + ": " + d.params.args.map((a) => a.value ?? a.description).join(" "));
  if (d.method === "Log.entryAdded" && d.params.entry.level === "error") errors.push("log: " + d.params.entry.text + " " + (d.params.entry.url || ""));
};
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
await send("Runtime.enable"); await send("Log.enable"); await send("Page.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
await send("Page.navigate", { url }); await sleep(1500);
const evalJs = async (js) => (await send("Runtime.evaluate", { expression: js, awaitPromise: true, returnByValue: true })).result?.result?.value;
for (const s of steps) {
  if (s.navigate) { await send("Page.navigate", { url: s.navigate }); await sleep(700); }
  if (s.click) { const ok = await evalJs(`(()=>{const e=document.querySelector(${JSON.stringify(s.click)}); if(!e) return false; e.dispatchEvent(new MouseEvent("click",{bubbles:true})); return true;})()`); if (!ok) errors.push("missing: " + s.click); await sleep(s.after ?? 300); }
  if (s.type) { await evalJs(`(()=>{const e=document.querySelector(${JSON.stringify(s.type[0])}); e.focus(); e.value=${JSON.stringify(s.type[1])}; e.dispatchEvent(new Event('input',{bubbles:true}));})()`); }
  if (s.submit) { await evalJs(`document.querySelector(${JSON.stringify(s.submit)}).requestSubmit()`); await sleep(s.after ?? 300); }
  if (s.wait) await sleep(s.wait);
  if (s.eval) { const value = await evalJs(s.eval); if (s.save) writeFileSync(`${prefix}-${s.save}.json`, JSON.stringify(value, null, 2)); else console.log("eval:", JSON.stringify(value)); }
  if (s.shot) {
    const w = s.w || 1440; let h = s.h || 1000;
    if (s.full) h = Math.min(6000, await evalJs("Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)"));
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 700 });
    await sleep(400);
    const r = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(`${prefix}-${s.shot}.png`, Buffer.from(r.result.data, "base64"));
    console.log("shot:", `${prefix}-${s.shot}.png`, w, h);

  }
}
console.log(errors.length ? "ERRORS:\n" + errors.join("\n") : "console clean");
ws.close(); chrome.kill();
process.exit(0);
