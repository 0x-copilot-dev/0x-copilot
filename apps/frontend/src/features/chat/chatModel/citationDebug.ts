// PR 1.1-rev2 — `[citations]`-prefixed console logger.
//
// Use this for debug breadcrumbs in the citation pipeline so log
// statements share a stable prefix users can grep in the browser
// console. Silenced under vitest so test output stays clean.

const isVitest =
  typeof process !== "undefined" && process.env?.VITEST === "true";

export function citationDebug(event: string, detail?: unknown): void {
  if (isVitest) {
    return;
  }
  if (detail === undefined) {
    // eslint-disable-next-line no-console
    console.log(`[citations] ${event}`);
  } else {
    // eslint-disable-next-line no-console
    console.log(`[citations] ${event}`, detail);
  }
}
