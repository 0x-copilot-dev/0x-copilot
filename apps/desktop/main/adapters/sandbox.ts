import { createContext, runInContext, Script } from "node:vm";

import type { SaaSRendererAdapter } from "@enterprise-search/chat-surface";

export type SandboxCompileResult =
  | { readonly ok: true; readonly adapter: SaaSRendererAdapter }
  | {
      readonly ok: false;
      readonly reason: "syntax" | "runtime" | "shape";
      readonly detail: string;
    };

interface SandboxModule {
  exports: unknown;
}

// PRD D29 / §9.5.2. The set of globals visible to a tier-2 adapter. The
// AST allowlist scanner refuses references to anything privileged, so this
// is defence in depth: even if the scanner is bypassed, the symbols are
// undefined inside the vm context. Each entry is included for a reason:
//   - Math / JSON / Date / RegExp / Number / String / Array / Object /
//     Boolean / Symbol — pure, no I/O, needed for any non-trivial render.
//   - Error / TypeError / RangeError — exceptions adapters may legitimately
//     throw; without them, throw new Error(...) would itself blow up.
//   - console (frozen no-op) — `console.log` inside the adapter must not
//     reach the main process. A frozen no-op keeps the adapter from
//     prototype-poisoning a real logger.
// Deliberately NOT included: process, global, globalThis, require,
// Function, eval, Buffer, setImmediate, setTimeout, setInterval,
// queueMicrotask, fetch, XMLHttpRequest, WebSocket, EventSource.
function buildSandboxGlobals(): Record<string, unknown> {
  const noopConsole = Object.freeze({
    log: () => {},
    warn: () => {},
    error: () => {},
    info: () => {},
    debug: () => {},
  });
  return {
    Math,
    JSON,
    Date,
    RegExp,
    Number,
    String,
    Array,
    Object,
    Boolean,
    Symbol,
    Error,
    TypeError,
    RangeError,
    SyntaxError,
    Promise,
    console: noopConsole,
  };
}

function isFunction(value: unknown): value is (...args: unknown[]) => unknown {
  return typeof value === "function";
}

function looksLikeAdapter(value: unknown): value is SaaSRendererAdapter {
  if (value === null || typeof value !== "object") return false;
  const obj = value as Record<string, unknown>;
  if (typeof obj.scheme !== "string") return false;
  if (!isFunction(obj.matches)) return false;
  if (!isFunction(obj.renderCurrent)) return false;
  if (!isFunction(obj.renderDiff)) return false;
  const metadata = obj.metadata;
  if (metadata === null || typeof metadata !== "object") return false;
  const md = metadata as Record<string, unknown>;
  if (typeof md.origin !== "string") return false;
  if (typeof md.schemaVersion !== "number") return false;
  return true;
}

export function compileAdapter(source: string): SandboxCompileResult {
  let script: Script;
  try {
    script = new Script(source, { filename: "tier2-adapter.js" });
  } catch (err) {
    return {
      ok: false,
      reason: "syntax",
      detail: err instanceof Error ? err.message : String(err),
    };
  }

  const sandboxModule: SandboxModule = { exports: {} };
  const globals: Record<string, unknown> = {
    ...buildSandboxGlobals(),
    module: sandboxModule,
    exports: sandboxModule.exports,
  };

  const context = createContext(globals, {
    name: "tier2-sandbox",
    codeGeneration: { strings: false, wasm: false },
  });

  try {
    script.runInContext(context, { timeout: 1000 });
  } catch (err) {
    return {
      ok: false,
      reason: "runtime",
      detail: err instanceof Error ? err.message : String(err),
    };
  }

  // The adapter may have assigned to `module.exports` or to `exports`.
  // We accept the first that produces a valid shape.
  const candidates: unknown[] = [sandboxModule.exports];
  if (globals.exports !== sandboxModule.exports) {
    candidates.push(globals.exports);
  }

  for (const candidate of candidates) {
    if (looksLikeAdapter(candidate)) {
      return { ok: true, adapter: candidate };
    }
  }

  return {
    ok: false,
    reason: "shape",
    detail:
      "adapter source did not export an object with { scheme, matches, renderCurrent, renderDiff, metadata: { origin, schemaVersion } }",
  };
}

// Exposed for testing only — the vm context is also constructed by
// `compileAdapter`. Returning the raw context lets the unit test prove
// that `process`, `global`, `Function`, etc. are `undefined` inside.
export function __createSandboxContextForTests(): {
  context: ReturnType<typeof createContext>;
  globals: Record<string, unknown>;
} {
  const globals: Record<string, unknown> = {
    ...buildSandboxGlobals(),
    module: { exports: {} },
  };
  const context = createContext(globals, {
    name: "tier2-sandbox-test",
    codeGeneration: { strings: false, wasm: false },
  });
  return { context, globals };
}

export function __probeForbiddenForTests(
  ctx: ReturnType<typeof createContext>,
  expression: string,
): unknown {
  return runInContext(expression, ctx);
}
