const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const { performance } = require("node:perf_hooks");
const { appURL, stateAnswers, waitForState } = require("../server");

async function fixture(t, handler) {
  const server = http.createServer(handler);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => {
    server.closeAllConnections();
    return new Promise((resolve) => server.close(resolve));
  });
  return new URL(`http://127.0.0.1:${server.address().port}/`);
}

test("default URL and HTTP(S) overrides; rejects other protocols and embedded credentials", () => {
  assert.equal(appURL().href, "http://127.0.0.1:8650/");
  assert.equal(appURL("https://example.test/chat").origin, "https://example.test");
  for (const url of ["no url", "file:///tmp/a", "javascript:alert(1)", "http://user:secret@localhost"]) {
    assert.throws(() => appURL(url), /Set AFTERGLOW_URL/);
  }
});

test("checks GET /api/state at the origin, not the initial route or query", async (t) => {
  const base = await fixture(t, (req, res) => {
    assert.equal(req.method, "GET");
    assert.equal(req.url, "/api/state");
    res.writeHead(200).end('{}');
  });
  assert.equal(await stateAnswers(new URL("/settings?section=model", base)), true);
});

test("a server error is not ready; a later successful response is", async (t) => {
  let calls = 0;
  const base = await fixture(t, (_req, res) => res.writeHead(++calls < 3 ? 503 : 200).end('{}'));
  assert.equal(await stateAnswers(base), false);
  assert.equal(await waitForState(base, { timeoutMs: 500, intervalMs: 5 }), true);
  assert.equal(calls, 3);
});

test("redirects do not claim readiness or follow an outside origin", async (t) => {
  const base = await fixture(t, (_req, res) => res.writeHead(302, { Location: "https://example.test" }).end());
  assert.equal(await stateAnswers(base), false);
});

test("connection refusal returns false", async () => {
  const server = http.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = new URL(`http://127.0.0.1:${server.address().port}`);
  await new Promise((resolve) => server.close(resolve));
  assert.equal(await stateAnswers(base), false);
});

test("a silent server cannot exceed the request wall-clock deadline", async (t) => {
  const base = await fixture(t, () => {});
  const started = performance.now();
  assert.equal(await stateAnswers(base, { timeoutMs: 60 }), false);
  assert.ok(performance.now() - started < 600);
});

test("the whole wait has a bounded deadline even for a silent server", async (t) => {
  const base = await fixture(t, () => {});
  const started = performance.now();
  assert.equal(await waitForState(base, { timeoutMs: 80, intervalMs: 5 }), false);
  assert.ok(performance.now() - started < 600);
});

test("quit can abort an in-flight check and wait without another request", async (t) => {
  let calls = 0;
  const base = await fixture(t, () => { calls++; });
  const controller = new AbortController();
  const pending = waitForState(base, { timeoutMs: 5000, signal: controller.signal });
  const timer = setTimeout(() => controller.abort(), 40);
  t.after(() => clearTimeout(timer));
  assert.equal(await pending, false);
  const alreadyAborted = new AbortController();
  alreadyAborted.abort();
  assert.equal(await stateAnswers(base, { signal: alreadyAborted.signal }), false);
  assert.equal(await waitForState(base, { signal: alreadyAborted.signal }), false);
  assert.equal(calls, 1);
});
