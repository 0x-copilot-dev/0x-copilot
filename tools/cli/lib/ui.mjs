// Tiny terminal-output helpers. Zero deps; colors auto-disable when stdout is
// not a TTY or NO_COLOR is set.

const useColor =
  process.stdout.isTTY === true &&
  !("NO_COLOR" in process.env) &&
  process.env.TERM !== "dumb";

function paint(code, s) {
  return useColor ? `[${code}m${s}[0m` : s;
}

export const c = {
  bold: (s) => paint("1", s),
  dim: (s) => paint("2", s),
  green: (s) => paint("32", s),
  yellow: (s) => paint("33", s),
  red: (s) => paint("31", s),
  cyan: (s) => paint("36", s),
  magenta: (s) => paint("35", s),
};

const PREFIX = c.magenta("0xcopilot");

export function info(msg) {
  process.stdout.write(`${PREFIX} ${msg}\n`);
}
export function step(msg) {
  process.stdout.write(`${PREFIX} ${c.cyan("›")} ${msg}\n`);
}
export function ok(msg) {
  process.stdout.write(`${PREFIX} ${c.green("✓")} ${msg}\n`);
}
export function warn(msg) {
  process.stderr.write(`${PREFIX} ${c.yellow("!")} ${msg}\n`);
}
export function err(msg) {
  process.stderr.write(`${PREFIX} ${c.red("✗")} ${msg}\n`);
}
export function plain(msg = "") {
  process.stdout.write(`${msg}\n`);
}

export function banner() {
  plain();
  plain(`  ${c.bold(c.magenta("0xCopilot"))} ${c.dim("· desktop")}`);
  plain();
}
