// PRD-10 (Wave 4) — the production Web-Worker source `Tier2Loader` expects.
//
// `Tier2Loader` (see ./Tier2Loader.tsx) speaks a fixed protocol: it posts a
// `Tier2WorkerRequest` and expects a `Tier2WorkerResponse` whose `rendered`
// branch carries a serialized `Tier2JsonElement` tree. Phase 6C left the
// production worker unbuilt (`defaultWorkerFactory` THROWS); this module ships
// it. Two exports matter to callers:
//
//   * `executeAdapterRender(request)` — the pure render core: it evaluates the
//     adapter source inside a sandbox and returns the serialized element tree.
//     It is the exact code the worker runs (the worker body is generated from
//     this function's own source via `.toString()`), so a unit test that calls
//     it directly exercises the real path — no browser Worker required.
//   * `createTier2WorkerFactory()` — builds a real `Worker` from a same-package
//     Blob URL. Desktop (Electron/Chromium) and web both get a preemptible
//     worker; the loader's 100 ms `terminate()` budget is untouched.
//
// Security model (D28/D29): the sandbox is NOT the worker global alone. The
// adapter body runs inside a `Function` whose parameter list *shadows* every
// forbidden global (`fetch`, `XMLHttpRequest`, `importScripts`, `document`, …)
// to `undefined`, and injects ONLY the allowlisted `react` / design-system
// bindings. Referencing a forbidden global therefore resolves to `undefined`
// and *calling* it throws — reported as a `throw` failure, never a live
// network/DOM reach. The worker global is additionally scrubbed as defence in
// depth. The allowlist here is a byte-for-byte mirror of the loader's
// `DS_COMPONENTS` + `adapter_allowlist.json`; this PRD does not expand it.

import type {
  Tier2JsonElement,
  Tier2WorkerLike,
  Tier2WorkerRequest,
  Tier2WorkerResponse,
} from "./Tier2Loader";

export type Tier2WorkerFactory = () => Tier2WorkerLike;

/**
 * Design-system component names the worker exposes to an adapter as inert
 * markers. `createElement(marker, …)` serializes to `tag: "ds:<Name>"`, which
 * the loader reconciles back to the real component. Mirrors the keys of
 * `Tier2Loader`'s `DS_COMPONENTS` exactly — a test pins them together so this
 * list can never silently drift or expand (PRD-10 non-goal: no allowlist
 * growth).
 */
export const TIER2_WORKER_DS_COMPONENT_NAMES: readonly string[] = [
  "Button",
  "Badge",
  "Card",
  "TextInput",
  "Select",
  "Switch",
  "Toggle",
  "Field",
  "IconButton",
  "StatusPill",
  "AppIcon",
  "HarnessRow",
  "StatusLine",
  "ConnectorChip",
];

// ---------------------------------------------------------------------------
// The render core. MUST be fully self-contained: the worker body is built from
// `executeAdapterRender.toString()`, so it may reference NOTHING from module
// scope at runtime (type-only imports are erased and are fine). Every constant
// and helper lives inside the function.
// ---------------------------------------------------------------------------
export function executeAdapterRender(
  request: Tier2WorkerRequest,
): Tier2WorkerResponse {
  // -- guards ---------------------------------------------------------------
  const MAX_NODES = 5000;
  const MAX_DEPTH = 64;
  const DS_NAMES = [
    "Button",
    "Badge",
    "Card",
    "TextInput",
    "Select",
    "Switch",
    "Toggle",
    "Field",
    "IconButton",
    "StatusPill",
    "AppIcon",
    "HarnessRow",
    "StatusLine",
    "ConnectorChip",
  ];
  // Every global the shared adapter_allowlist marks forbidden, plus the code-gen
  // primitives, shadowed to `undefined` in the adapter's lexical scope.
  const FORBIDDEN = [
    "window",
    "document",
    "fetch",
    "XMLHttpRequest",
    "WebSocket",
    "EventSource",
    "localStorage",
    "sessionStorage",
    "navigator",
    "history",
    "location",
    "crypto",
    "process",
    "global",
    "globalThis",
    "self",
    "importScripts",
    "require",
    "Buffer",
    "setImmediate",
    "clearImmediate",
    "setTimeout",
    "setInterval",
    "clearTimeout",
    "clearInterval",
    "queueMicrotask",
    // `eval` and `Function` are intentionally NOT shadowed here: they are
    // illegal binding names in strict mode, and the AST allowlist
    // (forbidden_syntax) already rejects them at install time upstream.
    "WebAssembly",
    "Worker",
    "SharedArrayBuffer",
    "postMessage",
    "close",
    "indexedDB",
    "caches",
    "fetch",
  ];

  const failure = (
    reason: "throw" | "oom" | "shape" | "violation",
    detail: string,
  ): Tier2WorkerResponse => ({ kind: "failed", reason, detail });

  // -- ESM → function-body transform ---------------------------------------
  // The generated adapter is an ES module (`import * as React …`,
  // `export const renderCurrent = …`). A `Function` body cannot contain
  // top-level import/export, so strip the allowlisted imports (bindings are
  // injected as parameters instead) and demote `export` declarations to plain
  // ones. The AST allowlist already validated imports upstream, so this only
  // has to handle well-formed, single-line import statements.
  const transformSource = (src: string): string => {
    let out = src;
    // Drop `import … from "…";` and side-effect `import "…";` lines.
    out = out.replace(/^[ \t]*import\b[^\n]*$/gm, "");
    // `export default X` → capture into a stable local.
    out = out.replace(/\bexport\s+default\s+/g, "var __t2_default = ");
    // `export { a, b as c };` / `export * from "…";` → drop (re-exports are
    // meaningless once bindings are local).
    out = out.replace(/\bexport\s*\{[^}]*\}\s*;?/g, "");
    out = out.replace(/\bexport\s+\*[^\n;]*;?/g, "");
    // `export const|let|var|function|class|async function …` → strip `export `.
    out = out.replace(
      /\bexport\s+(?=(?:async\s+)?(?:const|let|var|function|class)\b)/g,
      "",
    );
    return out;
  };

  // -- element helpers (createElement shim + serializer) --------------------
  const EL = "__t2_el__";
  const isDsMarker = (v: unknown): v is { __t2_ds: string } =>
    typeof v === "object" &&
    v !== null &&
    typeof (v as { __t2_ds?: unknown }).__t2_ds === "string";

  const flatten = (children: unknown[], acc: unknown[]): unknown[] => {
    for (const child of children) {
      if (Array.isArray(child)) flatten(child, acc);
      else acc.push(child);
    }
    return acc;
  };

  // Fake `React.createElement`. String tags and design-system markers become
  // element records; a plain function is treated as a pure component and
  // invoked (bounded by MAX_NODES + the worker's wall-clock terminate).
  const makeCreateElement = () => {
    const createElement = (
      type: unknown,
      props?: unknown,
      ...children: unknown[]
    ): unknown => {
      if (typeof type === "function" && !isDsMarker(type)) {
        const merged =
          props && typeof props === "object"
            ? { ...(props as Record<string, unknown>), children }
            : { children };
        return (type as (p: unknown) => unknown)(merged);
      }
      let tag: string;
      if (typeof type === "string") tag = type;
      else if (isDsMarker(type)) tag = "ds:" + type.__t2_ds;
      else if (
        type &&
        typeof type === "object" &&
        (type as { __t2_fragment?: unknown }).__t2_fragment === true
      )
        tag = "fragment";
      else tag = "unknown";
      return {
        [EL]: true,
        tag,
        props: props ?? null,
        children: flatten(children, []),
      };
    };
    return createElement;
  };

  // Strip props to structured-clone-safe, D28-safe data: no functions, no
  // handlers, no children/ref. The loader re-sanitizes against its own
  // ALLOWED_PROP_NAMES, so this only has to guarantee serializability.
  const sanitizeValue = (value: unknown, depth: number): unknown => {
    if (depth > MAX_DEPTH) return null;
    if (value === null) return null;
    const t = typeof value;
    if (t === "string" || t === "number" || t === "boolean") return value;
    if (
      t === "function" ||
      t === "symbol" ||
      t === "undefined" ||
      t === "bigint"
    )
      return undefined;
    if (Array.isArray(value)) {
      const arr: unknown[] = [];
      for (const item of value) {
        const clean = sanitizeValue(item, depth + 1);
        if (clean !== undefined) arr.push(clean);
      }
      return arr;
    }
    if (t === "object") {
      const out: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
        const clean = sanitizeValue(v, depth + 1);
        if (clean !== undefined) out[k] = clean;
      }
      return out;
    }
    return undefined;
  };

  const sanitizeProps = (raw: unknown): Record<string, unknown> | undefined => {
    if (raw === null || typeof raw !== "object" || Array.isArray(raw))
      return undefined;
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
      if (k === "children" || k === "ref" || k === "key") continue;
      if (k === "dangerouslySetInnerHTML") continue;
      if (k.length >= 2 && k[0] === "o" && k[1] === "n") continue;
      const clean = sanitizeValue(v, 0);
      if (clean !== undefined) out[k] = clean;
    }
    return Object.keys(out).length > 0 ? out : undefined;
  };

  const counter = { n: 0 };
  const serialize = (
    node: unknown,
    depth: number,
  ): Tier2JsonElement | string | number | null => {
    if (depth > MAX_DEPTH) throw new Error("tier2 render exceeded max depth");
    if (node === null || node === undefined) return null;
    const t = typeof node;
    if (t === "string") return node as string;
    if (t === "number") return node as number;
    if (t === "boolean") return null;
    counter.n += 1;
    if (counter.n > MAX_NODES) {
      throw new Error("tier2 render exceeded node budget");
    }
    // Our own element record.
    if (t === "object" && (node as Record<string, unknown>)[EL] === true) {
      const rec = node as {
        tag: string;
        props: unknown;
        children: unknown[];
      };
      const kids: Array<Tier2JsonElement | string | number | null> = [];
      for (const child of rec.children) kids.push(serialize(child, depth + 1));
      const props = sanitizeProps(rec.props);
      const el: Tier2JsonElement = props
        ? { tag: rec.tag, props, children: kids }
        : { tag: rec.tag, children: kids };
      return el;
    }
    // A React-element-like object (CommonJS fixtures return `{type, props}`).
    if (t === "object" && "type" in (node as Record<string, unknown>)) {
      const rec = node as {
        type: unknown;
        props?: unknown;
        children?: unknown;
      };
      let tag: string;
      if (typeof rec.type === "string") tag = rec.type;
      else if (isDsMarker(rec.type)) tag = "ds:" + rec.type.__t2_ds;
      else tag = "unknown";
      const rawProps =
        rec.props && typeof rec.props === "object"
          ? (rec.props as Record<string, unknown>)
          : {};
      const rawChildren =
        "children" in rawProps ? rawProps.children : rec.children;
      const childArr = Array.isArray(rawChildren)
        ? rawChildren
        : rawChildren === undefined || rawChildren === null
          ? []
          : [rawChildren];
      const kids: Array<Tier2JsonElement | string | number | null> = [];
      for (const child of childArr) kids.push(serialize(child, depth + 1));
      const props = sanitizeProps(rawProps);
      const el: Tier2JsonElement = props
        ? { tag, props, children: kids }
        : { tag, children: kids };
      return el;
    }
    return null;
  };

  // -- sandboxed evaluation -------------------------------------------------
  const buildInjections = (): {
    names: string[];
    values: unknown[];
  } => {
    const createElement = makeCreateElement();
    const Fragment = { __t2_fragment: true };
    const useState = (initial: unknown): [unknown, () => void] => [
      typeof initial === "function" ? (initial as () => unknown)() : initial,
      () => {},
    ];
    const classNames = (...args: unknown[]): string =>
      args.filter((a) => typeof a === "string" && a).join(" ");
    const React = { createElement, Fragment, useState };
    const moduleObj: { exports: Record<string, unknown> } = { exports: {} };

    const names: string[] = [
      "React",
      "createElement",
      "Fragment",
      "useState",
      "classNames",
      "tokens",
      "module",
      "exports",
    ];
    const values: unknown[] = [
      React,
      createElement,
      Fragment,
      useState,
      classNames,
      {}, // tokens — adapters only `void tokens;` it.
      moduleObj,
      moduleObj.exports,
    ];
    for (const dsName of DS_NAMES) {
      names.push(dsName);
      values.push({ __t2_ds: dsName });
    }
    // Shadow forbidden globals to `undefined` (deduped against injected names).
    const injected = new Set(names);
    for (const forbidden of FORBIDDEN) {
      if (injected.has(forbidden)) continue;
      injected.add(forbidden);
      names.push(forbidden);
      values.push(undefined);
    }
    return { names, values };
  };

  const pickAdapter = (
    ret: Record<string, unknown>,
  ): {
    renderCurrent?: unknown;
    renderDiff?: unknown;
  } | null => {
    const candidates: unknown[] = [ret.__adapter, ret.__cjs, ret.__default];
    for (const candidate of candidates) {
      if (
        candidate &&
        typeof candidate === "object" &&
        (typeof (candidate as Record<string, unknown>).renderCurrent ===
          "function" ||
          typeof (candidate as Record<string, unknown>).renderDiff ===
            "function")
      ) {
        return candidate as { renderCurrent?: unknown; renderDiff?: unknown };
      }
    }
    if (
      typeof ret.__renderCurrent === "function" ||
      typeof ret.__renderDiff === "function"
    ) {
      return {
        renderCurrent: ret.__renderCurrent,
        renderDiff: ret.__renderDiff,
      };
    }
    return null;
  };

  // -- run ------------------------------------------------------------------
  try {
    if (
      !request ||
      typeof request !== "object" ||
      typeof request.adapterSource !== "string"
    ) {
      return failure("shape", "worker request had no adapter source");
    }
    const transformed = transformSource(request.adapterSource);
    const epilogue =
      ";return {" +
      "__adapter: (typeof adapter !== 'undefined' ? adapter : undefined)," +
      "__renderCurrent: (typeof renderCurrent !== 'undefined' ? renderCurrent : undefined)," +
      "__renderDiff: (typeof renderDiff !== 'undefined' ? renderDiff : undefined)," +
      "__default: (typeof __t2_default !== 'undefined' ? __t2_default : undefined)," +
      "__cjs: module.exports" +
      "};";
    const { names, values } = buildInjections();

    let factory: (...args: unknown[]) => Record<string, unknown>;
    try {
      // `new Function` here is the trusted host sandbox mechanism (not adapter
      // code): the adapter body is AST-allowlisted upstream and lexically
      // sandboxed via the `names` parameter list below.
      factory = new Function(
        ...names,
        '"use strict";' + transformed + epilogue,
      ) as (...args: unknown[]) => Record<string, unknown>;
    } catch (err) {
      return failure(
        "throw",
        "adapter failed to compile: " +
          (err instanceof Error ? err.message : String(err)),
      );
    }

    let ret: Record<string, unknown>;
    try {
      ret = factory(...values);
    } catch (err) {
      return failure(
        "throw",
        "adapter threw at load: " +
          (err instanceof Error ? err.message : String(err)),
      );
    }

    const adapter = pickAdapter(ret);
    if (adapter === null) {
      return failure("shape", "adapter exposed no renderCurrent/renderDiff");
    }
    const method =
      request.mode === "diff" ? adapter.renderDiff : adapter.renderCurrent;
    if (typeof method !== "function") {
      return failure(
        "shape",
        "adapter has no " +
          (request.mode === "diff" ? "renderDiff" : "renderCurrent"),
      );
    }

    let rendered: unknown;
    try {
      rendered = (method as (input: unknown) => unknown)(request.payload);
    } catch (err) {
      return failure("throw", err instanceof Error ? err.message : String(err));
    }

    let tree: Tier2JsonElement | string | number | null;
    try {
      tree = serialize(rendered, 0);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (message.indexOf("budget") !== -1 || message.indexOf("depth") !== -1) {
        return failure("oom", message);
      }
      return failure("shape", message);
    }

    if (tree === null || typeof tree !== "object") {
      return failure("shape", "adapter render produced no element");
    }
    return { kind: "rendered", tree };
  } catch (err) {
    return failure("throw", err instanceof Error ? err.message : String(err));
  }
}

// ---------------------------------------------------------------------------
// Worker bundle. The body is generated from `executeAdapterRender`'s own source
// so there is exactly one implementation of the render core. `__t2_render` is a
// stable handle that survives minification (a named function expression's outer
// name is irrelevant to the caller).
// ---------------------------------------------------------------------------
const WORKER_SCRUB_SNIPPET =
  "(function(){try{var g=self;var banned=['fetch','XMLHttpRequest','WebSocket'," +
  "'EventSource','importScripts','indexedDB','caches','navigator','crypto'];" +
  "for(var i=0;i<banned.length;i++){try{g[banned[i]]=undefined;}catch(e){}}}catch(e){}})();";

export const TIER2_WORKER_SOURCE: string = [
  '"use strict";',
  WORKER_SCRUB_SNIPPET,
  "var __t2_render = " + executeAdapterRender.toString() + ";",
  "self.onmessage = function (ev) {",
  "  var req = ev && ev.data;",
  "  var res;",
  "  try { res = __t2_render(req); }",
  '  catch (e) { res = { kind: "failed", reason: "throw",',
  "    detail: String((e && e.message) || e) }; }",
  "  try { self.postMessage(res); }",
  '  catch (e2) { self.postMessage({ kind: "failed", reason: "shape",',
  '    detail: "worker produced an unserializable result" }); }',
  "};",
].join("\n");

/**
 * Production factory the desktop bootstrap hands to `Tier2Bridge`. Builds a
 * real `Worker` from a Blob URL carrying `TIER2_WORKER_SOURCE`. Throws in
 * environments without Web Workers (e.g. jsdom, SSR) — the loader treats a
 * throwing factory as a `throw` failure and falls back to tier-3, so a
 * substrate that cannot run workers degrades gracefully rather than crashing.
 */
export function createTier2WorkerFactory(): Tier2WorkerFactory {
  return () => {
    const g = globalThis as unknown as {
      Worker?: typeof Worker;
      Blob?: typeof Blob;
      URL?: typeof URL;
    };
    if (
      typeof g.Worker !== "function" ||
      typeof g.Blob !== "function" ||
      !g.URL ||
      typeof g.URL.createObjectURL !== "function"
    ) {
      throw new Error(
        "Tier2 worker factory: Web Worker/Blob/URL unavailable in this substrate",
      );
    }
    const blob = new g.Blob([TIER2_WORKER_SOURCE], {
      type: "text/javascript",
    });
    const url = g.URL.createObjectURL(blob);
    let worker: Worker;
    try {
      worker = new g.Worker(url);
    } finally {
      // Chromium (Electron + browsers) has already fetched the blob by the time
      // the constructor returns, so revoking immediately is safe and avoids a
      // per-render URL leak.
      try {
        g.URL.revokeObjectURL?.(url);
      } catch {
        // ignore — a stale object URL is harmless.
      }
    }
    const wrapper: Tier2WorkerLike = {
      postMessage: (value) => worker.postMessage(value),
      terminate: () => worker.terminate(),
      addEventListener: (type, listener) =>
        worker.addEventListener(
          type,
          listener as unknown as EventListenerOrEventListenerObject,
        ),
      removeEventListener: (type, listener) =>
        worker.removeEventListener(
          type,
          listener as unknown as EventListenerOrEventListenerObject,
        ),
    };
    return wrapper;
  };
}
