// Electron is stubbed: these tests never load its runtime, spawn Python, or open a window.
const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { appURL } = require("../server");
const source = readFileSync(path.join(__dirname, "../main.js"), "utf8");
const settle = () => new Promise((resolve) => setImmediate(resolve));

async function harness({ running = true, lock = true } = {}) {
  const app = new EventEmitter();
  app.getPath = () => "/tmp/afterglow-mock-support";
  app.setName = () => {};
  app.requestSingleInstanceLock = () => lock;
  app.whenReady = () => Promise.resolve();
  let quits = 0;
  app.quit = () => { quits++; };
  const loads = [], external = [], spawns = [], signals = [];
  const child = new EventEmitter();
  Object.assign(child, { pid: 42, exitCode: null, signalCode: null });
  child.kill = (signal) => { signals.push(signal); return true; };
  class Window extends EventEmitter {
    constructor(options) {
      super();
      this.options = options;
      this.webContents = new EventEmitter();
      Object.assign(this.webContents, {
        session: { setPermissionRequestHandler() {}, setPermissionCheckHandler() {} },
        setWindowOpenHandler(handler) { this.openHandler = handler; },
      });
      Window.instance = this;
    }
    async loadURL(url) { loads.push(url); }
    isDestroyed() { return false; }
    show() {}
  }
  let menu;
  const modules = {
    electron: {
      app, BrowserWindow: Window,
      Menu: { buildFromTemplate: (items) => items, setApplicationMenu: (items) => { menu = items; } },
      nativeImage: { createFromPath: () => ({ isEmpty: () => true }) },
      shell: { openExternal: async (url) => { external.push(url); } },
    },
    "node:path": path,
    "node:child_process": { spawn: (...args) => { spawns.push(args); return child; } },
    "./config": { readConfig: async () => ({ python: "python3", args: ["-m", "afterglow.web"] }) },
    "./server": { appURL, stateAnswers: async () => running, waitForState: async () => true },
  };
  vm.runInNewContext(source, {
    require: (name) => { assert.ok(name in modules); return modules[name]; },
    __dirname: path.resolve(__dirname, ".."),
    process: { platform: "darwin", env: {} }, URL, AbortController, setTimeout, clearTimeout,
  });
  await settle();
  return { app, child, loads, external, spawns, signals, window: Window.instance, menu, quits: () => quits };
}

function quit(app) {
  let prevented = false;
  app.emit("before-quit", { preventDefault() { prevented = true; } });
  return prevented;
}

test("reuses an existing server, routes Settings, and never stops an unowned process", async () => {
  const h = await harness();
  assert.equal(h.spawns.length, 0);
  assert.equal(h.loads.at(-1), "http://127.0.0.1:8650/");
  h.menu[0].submenu.find((item) => item.accelerator === "CmdOrCtrl+,").click();
  await settle();
  assert.equal(h.loads.at(-1), "http://127.0.0.1:8650/settings");
  assert.equal(quit(h.app), false);
  assert.deepEqual(h.signals, []);
});

test("navigation keeps local links, opens outside HTTP links, and blocks credentials and protocols", async () => {
  const h = await harness();
  const navigate = (url) => {
    let blocked = false;
    h.window.webContents.emit("will-navigate", { preventDefault() { blocked = true; } }, url);
    return blocked;
  };
  assert.equal(navigate("http://127.0.0.1:8650/memory"), false);
  assert.equal(navigate("https://example.test/"), true);
  assert.equal(navigate("http://user:secret@127.0.0.1:8650/"), true);
  assert.equal(navigate("file:///tmp/private"), true);
  assert.deepEqual(h.external, ["https://example.test/"]);
  assert.equal(h.window.webContents.openHandler({ url: "https://example.test/" }).action, "deny");
});

test("owned server gets SIGTERM and repeated quit waits for its exit", async () => {
  const h = await harness({ running: false });
  assert.equal(h.spawns.length, 1);
  assert.equal(h.spawns[0][2].cwd, path.resolve(__dirname, "../../.."));
  assert.equal(quit(h.app), true);
  assert.equal(quit(h.app), true);
  assert.deepEqual(h.signals, ["SIGTERM"]);
  h.child.signalCode = "SIGTERM";
  h.child.emit("exit", null, "SIGTERM");
  assert.equal(h.quits(), 1);
  assert.equal(quit(h.app), false);
});

test("failed spawn without an exit event cannot trap quit", async () => {
  const h = await harness({ running: false });
  h.child.pid = undefined;
  h.child.emit("error", new Error("ENOENT"));
  assert.equal(quit(h.app), false);
  assert.deepEqual(h.signals, []);
});

test("a second instance never creates another window or starts Python", async () => {
  const h = await harness({ lock: false });
  assert.equal(h.quits(), 1);
  assert.equal(h.window, undefined);
  assert.equal(h.spawns.length, 0);
});
