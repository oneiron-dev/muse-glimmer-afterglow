const { app, BrowserWindow, Menu, nativeImage, shell } = require("electron");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { readConfig } = require("./config");
const { appURL, stateAnswers, waitForState } = require("./server");

const appDir = path.resolve(__dirname, "../..");
const configFile = path.join(app.getPath("appData"), "Afterglow", "config.json");
const iconFile = path.join(__dirname, "build", "afterglow.png");
const startup = new AbortController();
let window;
let server;
let target;
let stopping = false;
let serverReady = false;

app.setName("Afterglow");

function page(title, message) {
  if (!window || window.isDestroyed()) return;
  const escape = (value) => value.replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
  const html = `<!doctype html><html lang="en"><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
    <title>Afterglow</title><style>
    body { margin: 0; background: #fbfaf8; color: oklch(24% 0.022 285); font: 16px/1.6 system-ui; }
    header { height: 56px; -webkit-app-region: drag; }
    main { max-width: 680px; margin: 80px auto; padding: 24px; }
    h1 { font-size: 24px; font-weight: 600; } p { white-space: pre-wrap; overflow-wrap: anywhere; }
    </style><header></header><main><h1>${escape(title)}</h1><p>${escape(message)}</p></main></html>`;
  void window.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`).catch(() => {});
}

function navigate(url, event) {
  let destination;
  try { destination = new URL(url); } catch { event?.preventDefault(); return false; }
  if (target && destination.origin === target.origin && !destination.username && !destination.password) return true;
  event?.preventDefault();
  if (["http:", "https:"].includes(destination.protocol) && !destination.username && !destination.password) {
    void shell.openExternal(destination.href).catch(() => {});
  }
  return false;
}

function createWindow() {
  window = new BrowserWindow({
    width: 1440, height: 960, minWidth: 1000, minHeight: 680,
    title: "Afterglow", titleBarStyle: "hiddenInset",
    trafficLightPosition: { x: 16, y: 25 }, backgroundColor: "#fbfaf8",
    icon: iconFile, show: false,
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true },
  });
  window.once("ready-to-show", () => window.show());
  window.on("closed", () => { window = null; });
  window.webContents.on("will-navigate", (event, url) => navigate(url, event));
  window.webContents.on("will-redirect", (event, url) => navigate(url, event));
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (navigate(url)) void loadURL(url);
    return { action: "deny" };
  });
  window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  window.webContents.session.setPermissionCheckHandler(() => false);
  window.webContents.on("did-finish-load", () => {
    if (process.platform !== "darwin" || !target ||
        new URL(window.webContents.getURL()).origin !== target.origin) return;
    // Reserve the traffic-light area only in this shell, including the folded sidebar.
    void window.webContents.insertCSS(`
      .side-top { padding-left: 82px; min-height: 40px; }
      .side-top > .mark { display: none; }
      .folded .side-in { padding-top: 64px; }
      .folded .side-top { padding-left: 0; }
      .folded .bar { padding-left: 40px; }
    `).catch(() => {});
  });
  window.webContents.on("did-fail-load", (_event, code, _description, _url, isMainFrame) => {
    if (isMainFrame && code !== -3 && !stopping) page("Your agent could not load", "Check your server, then quit and reopen Afterglow.");
  });
}

async function loadURL(url) {
  try { await window.loadURL(url); }
  catch (error) {
    if (!stopping && error.code !== "ERR_ABORTED" && error.errno !== -3) {
      page("Your agent could not load", "Check your server, then quit and reopen Afterglow.");
    }
  }
}

async function loadAgent() {
  try {
    target = appURL(process.env.AFTERGLOW_URL || undefined);
    page("Opening your agent", "Checking your Afterglow server.");
    if (await stateAnswers(target, { signal: startup.signal })) {
      serverReady = true;
      if (!stopping) await loadURL(target.href);
      return;
    }
    if (stopping) return;
    const config = await readConfig(configFile);
    if (stopping) return;
    if (!config) {
      page("Set up your server", `An example was written to:\n${configFile}\n\nSet python and args for your world and day. The command runs from:\n${appDir}\n\nMake its host and port match AFTERGLOW_URL (default: http://127.0.0.1:8650). Then quit and reopen Afterglow.`);
      return;
    }
    page("Starting your agent", "Waiting for your configured Python server.");
    const child = spawn(config.python, config.args, { cwd: appDir, env: process.env, stdio: "ignore" });
    server = child;
    let failed = false;
    const failedStart = () => {
      failed = true;
      serverReady = false;
      startup.abort();
      if (!stopping) page("Your server stopped", `Check python and args in:\n${configFile}\n\nRun the command from the app/ folder in a terminal to check it. Then quit and reopen Afterglow. Server output is not recorded by the shell.`);
    };
    child.once("error", () => { if (!child.pid) server = null; failedStart(); });
    child.once("exit", () => { server = null; failedStart(); });
    const ready = await waitForState(target, { signal: startup.signal });
    if (stopping || failed) return;
    if (!ready) {
      page("Your server did not answer", `No successful /api/state response arrived within 30 seconds.\n\nCheck python and args in:\n${configFile}\n\nCheck that its host and port match AFTERGLOW_URL. Quit Afterglow to stop the server it started, then reopen to try again.`);
      return;
    }
    serverReady = true;
    await loadURL(target.href);
  } catch (error) {
    if (!stopping) page("Check your server setup", `${error.code ? "Your server config could not be created. Check its folder permissions." : error.message}\n\nConfig: ${configFile}\n\nThen quit and reopen Afterglow.`);
  }
}

function settings() {
  if (window && target && serverReady) void loadURL(new URL("/settings", target).href);
}

function installMenu() {
  const template = [
    { label: "Afterglow", submenu: [
      { role: "about" }, { type: "separator" },
      { label: "Settings…", accelerator: "CmdOrCtrl+,", click: settings },
      { type: "separator" }, { role: "services" }, { type: "separator" },
      { role: "hide" }, { role: "hideOthers" }, { role: "unhide" },
      { type: "separator" }, { role: "quit" },
    ] },
    { role: "editMenu" },
    { label: "View", submenu: [
      { role: "reload" }, { role: "forceReload" }, { type: "separator" },
      { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" },
      { type: "separator" }, { role: "togglefullscreen" },
    ] },
    { role: "windowMenu" },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!window) return;
    if (window.isMinimized()) window.restore();
    window.show();
    window.focus();
  });
  app.on("activate", () => { if (window) window.show(); });
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", (event) => {
    if (stopping) {
      if (server && server.exitCode === null && server.signalCode === null) event.preventDefault();
      return;
    }
    stopping = true;
    startup.abort();
    if (!server || !server.pid || server.exitCode !== null || server.signalCode !== null) return;
    event.preventDefault();
    const child = server;
    const force = setTimeout(() => child.kill("SIGKILL"), 3000);
    child.once("exit", () => { clearTimeout(force); app.quit(); });
    child.kill("SIGTERM");
  });
  app.whenReady().then(() => {
    const icon = nativeImage.createFromPath(iconFile);
    if (app.dock && !icon.isEmpty()) app.dock.setIcon(icon);
    installMenu();
    createWindow();
    void loadAgent();
  });
}
