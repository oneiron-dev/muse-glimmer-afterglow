const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { EXAMPLE, parseConfig, readConfig } = require("../config");
const config = { python: "/a folder/python3", args: ["-m", "afterglow.web", "--world", "p-test", "--day", "1"] };

test("parses the explicit command without shell splitting or changing arguments", () => {
  assert.deepEqual(parseConfig(JSON.stringify(config)), config);
});

test("whole-line comments do not damage URLs or // inside arguments", () => {
  const withURL = { ...config, args: [...config.args, "--endpoint", "http://127.0.0.1:18610", "//literal"] };
  assert.deepEqual(parseConfig(`// Setup\n  // Another comment\n${JSON.stringify(withURL)}`), withURL);
});

test("invalid config gives useful errors without repeating supplied contents", () => {
  for (const text of ["not-json-secret", '{"python":"secret", // inline\n"args":[]}']) {
    assert.throws(() => parseConfig(text), { message: /Use a JSON object/ });
  }
  for (const value of [null, [], {}, { python: " ", args: [] }, { python: "python\0", args: ["x"] }]) {
    assert.throws(() => parseConfig(JSON.stringify(value)), { message: /Set python/ });
  }
  for (const args of [null, "-m afterglow.web", [], [1], ["x\0"]]) {
    assert.throws(() => parseConfig(JSON.stringify({ python: "python3", args })), { message: /Set args/ });
  }
});

test("first run writes a private commented example, never a runnable default", async (t) => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "afterglow-config-"));
  t.after(() => fs.rm(dir, { recursive: true, force: true }));
  const filename = path.join(dir, "Afterglow", "config.json");
  assert.equal(await readConfig(filename), null);
  assert.equal(await fs.readFile(filename, "utf8"), EXAMPLE);
  assert.equal((await fs.stat(filename)).mode & 0o777, 0o600);
  assert.throws(() => parseConfig(EXAMPLE), { message: /Set python/ });
  await fs.writeFile(filename, JSON.stringify(config));
  assert.deepEqual(await readConfig(filename), config);
  assert.equal(await fs.readFile(filename, "utf8"), JSON.stringify(config));
});
