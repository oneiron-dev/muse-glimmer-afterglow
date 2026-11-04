const http = require("node:http");
const https = require("node:https");
const { setTimeout: delay } = require("node:timers/promises");

function appURL(value = "http://127.0.0.1:8650") {
  let url;
  try { url = new URL(value); } catch { throw new Error("Set AFTERGLOW_URL to an http or https address."); }
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) {
    throw new Error("Set AFTERGLOW_URL to an http or https address without credentials.");
  }
  return url;
}

// A wall-clock deadline also covers a server that accepts a socket but never sends headers.
function stateAnswers(base, { timeoutMs = 1500, signal } = {}) {
  const url = new URL("/api/state", base);
  return new Promise((resolve) => {
    if (signal?.aborted) { resolve(false); return; }
    let request;
    const finish = (ok) => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      request?.destroy();
      resolve(ok);
    };
    const abort = () => finish(false);
    const timer = setTimeout(abort, timeoutMs);
    signal?.addEventListener("abort", abort, { once: true });
    request = (url.protocol === "https:" ? https : http).get(url, (response) => {
      response.resume();
      finish(response.statusCode >= 200 && response.statusCode < 300);
    });
    request.on("error", () => finish(false));
  });
}

async function waitForState(base, { timeoutMs = 30000, intervalMs = 250, signal } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (!signal?.aborted && Date.now() < deadline) {
    if (await stateAnswers(base, { timeoutMs: Math.min(1500, deadline - Date.now()), signal })) return true;
    const remaining = deadline - Date.now();
    if (remaining <= 0 || signal?.aborted) break;
    try { await delay(Math.min(intervalMs, remaining), undefined, { signal }); }
    catch (error) { if (error.name !== "AbortError") throw error; }
  }
  return false;
}

module.exports = { appURL, stateAnswers, waitForState };
