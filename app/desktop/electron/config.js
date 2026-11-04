const fs = require("node:fs/promises");
const path = require("node:path");

const EXAMPLE = `// Set the Python executable and arguments for your own Afterglow day.
// Only whole-line // comments are supported. Do not put secrets in this file.
// Example (replace YOUR_WORLD and YOUR_DAY before using):
// {"python": "python3", "args": ["-m", "afterglow.web", "--world", "YOUR_WORLD", "--day", "YOUR_DAY", "--port", "8650"]}
// Python runs from the repository's app/ folder and inherits your environment.
// Set AFTERGLOW_RUNS to a scratch copy if you do not want to use the default runs/.
{
  "python": "",
  "args": []
}
`;

function parseConfig(text) {
  let config;
  try {
    config = JSON.parse(text.split(/\r?\n/).filter((line) => !line.trimStart().startsWith("//")).join("\n"));
  } catch {
    throw new Error("Use a JSON object with python and args. Only whole-line // comments are allowed.");
  }
  if (!config || typeof config !== "object" || Array.isArray(config) ||
      typeof config.python !== "string" || !config.python.trim() || config.python.includes("\0")) {
    throw new Error("Set python to your Python executable, such as python3 or an absolute path.");
  }
  if (!Array.isArray(config.args) || config.args.length === 0 ||
      !config.args.every((arg) => typeof arg === "string" && !arg.includes("\0"))) {
    throw new Error("Set args to a non-empty array of command arguments, one string per argument.");
  }
  return { python: config.python.trim(), args: config.args };
}

async function readConfig(filename) {
  let text;
  try {
    text = await fs.readFile(filename, "utf8");
  } catch (error) {
    if (error.code !== "ENOENT") throw new Error("Your server config could not be read. Check its permissions.");
    await fs.mkdir(path.dirname(filename), { recursive: true });
    await fs.writeFile(filename, EXAMPLE, { flag: "wx", mode: 0o600 });
    return null;
  }
  return parseConfig(text);
}

module.exports = { EXAMPLE, parseConfig, readConfig };
