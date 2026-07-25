// AC8 agentic browser — engine abstraction.
//
// The session logic depends on THIS narrow interface, never on Playwright
// directly, so: (a) unit tests inject a fake engine and never launch Chromium,
// and (b) the real Playwright dependency is lazily imported ONLY inside the
// worker child (`createPlaywrightEngine`), keeping it out of Electron main,
// preload, renderer, and the typecheck graph of everything else.
//
// The interface exposes only bounded reads plus exact, worker-owned element
// handles. There is no model-supplied selector, coordinate, JavaScript, CDP,
// or arbitrary-method passthrough.

import { createHash, randomBytes } from "node:crypto";

/** A raw accessibility node as returned by the engine (Playwright-shaped). */
export interface RawAxNode {
  role: string;
  name?: string;
  /** Present for inputs — NEVER forwarded to the model. */
  value?: string;
  /** Worker-private identity of the exact DOM handle captured at snapshot. */
  targetId?: string;
  /** Digest of fixed, non-secret identity facts for the exact handle. */
  elementFingerprint?: string;
  /** Safe form facts, present only when the target belongs to a supported form. */
  formFingerprint?: string;
  /** Digest of exact successful controls; raw names/values stay in this worker. */
  formPayloadDigest?: string;
  formActionUrl?: string;
  formMethod?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  children?: RawAxNode[];
}

export interface NavigationOutcome {
  readonly url: string;
  readonly title: string;
  readonly status: number;
}

/**
 * A generation-bound element the model addressed by ref. The worker supplies
 * the ref plus the redacted role/name from the last snapshot so the engine can
 * locate the element WITHOUT the model ever handing over a raw selector.
 */
export interface ElementTarget {
  readonly ref: string;
  readonly role: string;
  readonly name: string;
  /** Worker-private key for the snapshot-captured ElementHandle. */
  readonly targetId: string;
}

export interface ElementObservation {
  readonly elementFingerprint: string;
  /** A form exists but cannot be represented safely and exactly. */
  readonly unsupportedForm?: boolean;
  readonly formFingerprint?: string;
  readonly formPayloadDigest?: string;
  readonly formActionUrl?: string;
  readonly formMethod?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
}

/** Bytes captured from a browser-initiated download. */
export interface DownloadCapture {
  /** Site-suggested filename (untrusted metadata; sanitized by the caller). */
  readonly suggestedName: string;
  readonly body: Uint8Array;
}

export interface EnginePage {
  goto(url: string, opts: { timeoutMs: number }): Promise<NavigationOutcome>;
  accessibilitySnapshot(): Promise<RawAxNode | null>;
  screenshot(opts: { fullPage: boolean }): Promise<Uint8Array>;
  waitFor(
    condition: "load" | "networkidle" | "timeout",
    timeoutMs: number,
  ): Promise<void>;
  currentUrl(): string;
  currentTitle(): Promise<string>;
  /**
   * Re-observe the exact snapshot-captured DOM handle. Returns null if it was
   * detached or released; it must never search for a similar replacement.
   */
  observeRef(target: ElementTarget): Promise<ElementObservation | null>;
  // --- action layer (side-effecting; gated by an approval upstream) ---
  clickRef(target: ElementTarget): Promise<void>;
  fillRef(target: ElementTarget, text: string): Promise<void>;
  selectRef(target: ElementTarget, value: string): Promise<void>;
  submitRef(target: ElementTarget): Promise<void>;
  /** Click an element and capture the download it initiates. */
  downloadViaRef(
    target: ElementTarget,
    opts: { timeoutMs: number },
  ): Promise<DownloadCapture>;
}

export interface EngineContext {
  newPage(): Promise<EnginePage>;
  close(): Promise<void>;
}

export interface BrowserEngine {
  /** Open an isolated context bound to a profile directory (or ephemeral). */
  newContext(opts: {
    userDataDir: string;
    persistent: boolean;
    /**
     * Accept browser-initiated downloads. Read-only sessions leave this off;
     * the action layer opts in so `browser_download` can capture bytes into
     * the per-run staging directory. Off by default (read-only default).
     */
    acceptDownloads?: boolean;
  }): Promise<EngineContext>;
  /** Pinned Chromium build id. */
  version(): string;
  close(): Promise<void>;
}

export interface PlaywrightEngineOptions {
  /** `127.0.0.1:<port>` of the loopback egress proxy (no bypass list). */
  readonly proxyServer: string;
  /** Absolute path to the staged, pinned Chromium executable. */
  readonly executablePath?: string;
}

/**
 * Build the REAL Playwright-backed engine. Lazily imports `playwright` so the
 * dependency is only loaded inside the supervised worker child. Launches
 * Chromium through the loopback proxy with no bypass list, service workers
 * blocked, and downloads/permissions denied. NOT covered by unit tests (it
 * needs a real browser); the fake engine covers the session contract.
 */
export async function createPlaywrightEngine(
  opts: PlaywrightEngineOptions,
): Promise<BrowserEngine> {
  const pw: any = await import("playwright");
  const chromium = pw.chromium;
  const launchArgs = [
    "--disable-quic",
    "--disable-features=WebRtcHideLocalIpsWithMdns",
    "--no-default-browser-check",
    "--no-first-run",
  ];
  // Ask the binary itself for its version. Package metadata or an environment
  // value is not sufficient for the supervisor's pin: a substituted
  // executable must be detected before the broker becomes reachable.
  const versionProbe = await chromium.launch({
    headless: true,
    args: launchArgs,
    proxy: { server: `http://${opts.proxyServer}` },
    executablePath: opts.executablePath,
  });
  const version = String(versionProbe.version());
  await versionProbe.close();

  return {
    version: () => version,
    async newContext({ userDataDir, persistent, acceptDownloads }) {
      // Both paths route ALL traffic through the loopback policy proxy with no
      // bypass; service workers are blocked (they can hide requests from
      // context routing). Downloads are OFF unless the action layer opts in.
      const downloads = acceptDownloads === true;
      const contextOpts = {
        proxy: { server: `http://${opts.proxyServer}` },
        serviceWorkers: "block" as const,
        acceptDownloads: downloads,
        args: launchArgs,
        executablePath: opts.executablePath,
      };
      let ctx: any;
      if (persistent) {
        ctx = await chromium.launchPersistentContext(userDataDir, contextOpts);
      } else {
        const browser = await chromium.launch({
          proxy: contextOpts.proxy,
          args: launchArgs,
          executablePath: opts.executablePath,
        });
        ctx = await browser.newContext({
          serviceWorkers: "block",
          acceptDownloads: downloads,
        });
        ctx.__browser = browser;
      }
      return wrapContext(ctx);
    },
    async close() {
      // Contexts own their browsers; nothing global to close here.
    },
  };
}

function wrapContext(ctx: any): EngineContext {
  return {
    async newPage() {
      const page = await ctx.newPage();
      return wrapPage(page);
    },
    async close() {
      await ctx.close();
      if (ctx.__browser) await ctx.__browser.close();
    },
  };
}

function wrapPage(page: any): EnginePage {
  const targetHandles = new Map<string, any>();
  let snapshotSequence = 0;
  return {
    async goto(url, { timeoutMs }) {
      const response = await page.goto(url, {
        timeout: timeoutMs,
        waitUntil: "domcontentloaded",
      });
      return {
        url: page.url(),
        title: await page.title(),
        status: response ? response.status() : 0,
      };
    },
    async accessibilitySnapshot() {
      await disposeHandles(targetHandles);
      const raw = (await page.accessibility.snapshot()) as RawAxNode | null;
      if (raw === null) return null;
      snapshotSequence += 1;
      await annotateSnapshotTargets({
        page,
        root: raw,
        handles: targetHandles,
        snapshotSequence,
      });
      return raw;
    },
    async screenshot({ fullPage }) {
      return (await page.screenshot({ fullPage })) as Uint8Array;
    },
    async waitFor(condition, timeoutMs) {
      if (condition === "timeout") {
        await page.waitForTimeout(timeoutMs);
        return;
      }
      await page.waitForLoadState(condition, { timeout: timeoutMs });
    },
    currentUrl() {
      return page.url();
    },
    async currentTitle() {
      return page.title();
    },
    async observeRef(target) {
      const handle = targetHandles.get(target.targetId);
      if (handle === undefined) return null;
      return observeHandle(handle, target.targetId);
    },
    // Actions use the exact ElementHandle captured while producing the
    // reviewed snapshot. A detached handle fails; it is never replaced by the
    // first element with a similar role/name.
    async clickRef(target) {
      await exactHandle(targetHandles, target).click();
    },
    async fillRef(target, text) {
      await exactHandle(targetHandles, target).fill(text);
    },
    async selectRef(target, value) {
      await exactHandle(targetHandles, target).selectOption(value);
    },
    async submitRef(target) {
      // A submit is a click on the reviewed submit control.
      await exactHandle(targetHandles, target).click();
    },
    async downloadViaRef(target, { timeoutMs }) {
      const [download] = await Promise.all([
        page.waitForEvent("download", { timeout: timeoutMs }),
        exactHandle(targetHandles, target).click(),
      ]);
      const stream = await download.createReadStream();
      const chunks: Buffer[] = [];
      for await (const chunk of stream as AsyncIterable<Buffer>) {
        chunks.push(chunk);
      }
      return {
        suggestedName: String(download.suggestedFilename?.() ?? "download"),
        body: Buffer.concat(chunks),
      };
    },
  };
}

const MAX_CAPTURED_TARGETS = 500;

async function annotateSnapshotTargets(input: {
  readonly page: any;
  readonly root: RawAxNode;
  readonly handles: Map<string, any>;
  readonly snapshotSequence: number;
}): Promise<void> {
  const occurrences = new Map<string, number>();
  let targetCount = 0;
  const visit = async (node: RawAxNode): Promise<void> => {
    if (targetCount < MAX_CAPTURED_TARGETS) {
      const key = JSON.stringify([node.role, node.name ?? ""]);
      const occurrence = occurrences.get(key) ?? 0;
      occurrences.set(key, occurrence + 1);
      try {
        const locator =
          (node.name ?? "") === ""
            ? input.page.getByRole(node.role).nth(occurrence)
            : input.page
                .getByRole(node.role, {
                  name: node.name ?? "",
                  exact: true,
                })
                .nth(occurrence);
        const handle = await locator.elementHandle();
        if (handle !== null) {
          const targetId =
            `target_${input.snapshotSequence}_` +
            randomBytes(18).toString("base64url");
          const observation = await observeHandle(handle, targetId);
          if (observation !== null && observation.unsupportedForm !== true) {
            input.handles.set(targetId, handle);
            node.targetId = targetId;
            node.elementFingerprint = observation.elementFingerprint;
            node.formFingerprint = observation.formFingerprint;
            node.formPayloadDigest = observation.formPayloadDigest;
            node.formActionUrl = observation.formActionUrl;
            node.formMethod = observation.formMethod;
            targetCount += 1;
          } else {
            await handle.dispose().catch(() => {});
          }
        }
      } catch {
        // Some accessibility-only nodes have no directly addressable DOM
        // element. They remain readable but are intentionally not actionable.
      }
    }
    for (const child of node.children ?? []) {
      await visit(child);
    }
  };
  await visit(input.root);
}

async function observeHandle(
  handle: any,
  targetId: string,
): Promise<ElementObservation | null> {
  try {
    const facts = (await handle.evaluate((element: any) => {
      if (element?.isConnected !== true) return null;
      const tagName = String(element.tagName ?? "").toLowerCase();
      const form =
        element.form instanceof HTMLFormElement
          ? element.form
          : element.closest?.("form");
      const hasForm = form instanceof HTMLFormElement;
      let unsupportedForm = false;
      let formActionUrl: string | undefined;
      let formMethod: string | undefined;
      let formEnctype: string | undefined;
      let formTarget: string | undefined;
      let formPayloadEntries: [string, string][] | undefined;
      if (hasForm) {
        const normalizedType = String(element.type ?? "").toLowerCase();
        const isSubmitter =
          (tagName === "button" &&
            (normalizedType === "" || normalizedType === "submit")) ||
          (tagName === "input" && normalizedType === "submit");
        // Image submitters add click coordinates to the payload. Until those
        // coordinates are staged explicitly, the form is not actionable.
        if (tagName === "input" && normalizedType === "image") {
          unsupportedForm = true;
        }
        const rawAction =
          typeof element.formAction === "string" && element.formAction !== ""
            ? element.formAction
            : form.action;
        try {
          const parsed = new URL(rawAction, element.ownerDocument.baseURI);
          if (
            parsed.protocol === "https:" &&
            parsed.username === "" &&
            parsed.password === "" &&
            parsed.search === "" &&
            parsed.hash === ""
          ) {
            formActionUrl = parsed.toString();
            const rawMethod =
              typeof element.formMethod === "string" &&
              element.formMethod !== ""
                ? element.formMethod
                : form.method;
            const method = String(rawMethod || "get").toUpperCase();
            if (["GET", "POST", "PUT", "PATCH", "DELETE"].includes(method)) {
              formMethod = method;
              formEnctype = String(
                isSubmitter &&
                  typeof element.formEnctype === "string" &&
                  element.formEnctype !== ""
                  ? element.formEnctype
                  : form.enctype || "application/x-www-form-urlencoded",
              ).toLowerCase();
              const rawTarget = String(
                isSubmitter &&
                  typeof element.formTarget === "string" &&
                  element.formTarget !== ""
                  ? element.formTarget
                  : form.target || "_self",
              ).toLowerCase();
              if (rawTarget === "" || rawTarget === "_self") {
                formTarget = "_self";
              } else {
                unsupportedForm = true;
              }
              if (!unsupportedForm) {
                try {
                  const data = isSubmitter
                    ? new FormData(form, element)
                    : new FormData(form);
                  const entries: [string, string][] = [];
                  for (const [name, value] of data.entries()) {
                    // File-bearing submissions require immutable artifact-byte
                    // authorization. They cannot fall back to a generic click.
                    if (typeof value !== "string") {
                      unsupportedForm = true;
                      break;
                    }
                    entries.push([String(name), value]);
                  }
                  if (!unsupportedForm) {
                    formPayloadEntries = entries;
                  }
                } catch {
                  unsupportedForm = true;
                }
              }
            }
          }
        } catch {
          // Unsupported or non-HTTPS forms are readable but not stageable.
          unsupportedForm = true;
        }
        if (
          formActionUrl === undefined ||
          formMethod === undefined ||
          formEnctype === undefined ||
          formTarget === undefined ||
          formPayloadEntries === undefined
        ) {
          unsupportedForm = true;
        }
      }
      return {
        tagName,
        id: String(element.id ?? ""),
        name: String(element.getAttribute?.("name") ?? ""),
        type: String(element.getAttribute?.("type") ?? ""),
        role: String(element.getAttribute?.("role") ?? ""),
        ariaLabel: String(element.getAttribute?.("aria-label") ?? ""),
        text: String(element.textContent ?? "")
          .trim()
          .slice(0, 512),
        hasForm,
        unsupportedForm,
        formActionUrl,
        formMethod,
        formEnctype,
        formTarget,
        formPayloadEntries,
      };
    })) as {
      tagName: string;
      id: string;
      name: string;
      type: string;
      role: string;
      ariaLabel: string;
      text: string;
      hasForm: boolean;
      unsupportedForm: boolean;
      formActionUrl?: string;
      formMethod?: string;
      formEnctype?: string;
      formTarget?: string;
      formPayloadEntries?: [string, string][];
    } | null;
    if (facts === null) return null;
    const elementFingerprint = digest([
      targetId,
      facts.tagName,
      facts.id,
      facts.name,
      facts.type,
      facts.role,
      facts.ariaLabel,
      facts.text,
      facts.formActionUrl ?? null,
      facts.formMethod ?? null,
      facts.formEnctype ?? null,
      facts.formTarget ?? null,
    ]);
    if (facts.hasForm && facts.unsupportedForm) {
      return { elementFingerprint, unsupportedForm: true };
    }
    const formPayloadDigest =
      facts.formActionUrl !== undefined &&
      facts.formMethod !== undefined &&
      facts.formEnctype !== undefined &&
      facts.formTarget !== undefined &&
      facts.formPayloadEntries !== undefined
        ? browserFormPayloadDigest({
            actionUrl: facts.formActionUrl,
            method: facts.formMethod,
            enctype: facts.formEnctype,
            target: facts.formTarget,
            entries: facts.formPayloadEntries,
          })
        : undefined;
    const formFingerprint =
      formPayloadDigest !== undefined
        ? digest([
            "browser-form-v1",
            targetId,
            facts.formActionUrl,
            facts.formMethod,
            facts.formEnctype,
            facts.formTarget,
          ])
        : undefined;
    return {
      elementFingerprint,
      ...(formFingerprint === undefined
        ? {}
        : {
            formFingerprint,
            formPayloadDigest,
            formActionUrl: facts.formActionUrl,
            formMethod: facts.formMethod as ElementObservation["formMethod"],
          }),
    };
  } catch {
    return null;
  }
}

function exactHandle(
  handles: ReadonlyMap<string, any>,
  target: ElementTarget,
): any {
  const handle = handles.get(target.targetId);
  if (handle === undefined) {
    throw new Error("exact browser element is unavailable");
  }
  return handle;
}

async function disposeHandles(handles: Map<string, any>): Promise<void> {
  const current = [...handles.values()];
  handles.clear();
  await Promise.all(current.map((handle) => handle.dispose().catch(() => {})));
}

function digest(value: readonly unknown[]): string {
  return createHash("sha256").update(JSON.stringify(value)).digest("hex");
}

/**
 * Hash exact successful form controls without exposing their names or values
 * to Electron main, the MCP broker, logs, or the model. This module executes
 * only inside the supervised browser worker.
 */
export function browserFormPayloadDigest(input: {
  readonly actionUrl: string;
  readonly method: string;
  readonly enctype: string;
  readonly target: string;
  readonly entries: readonly (readonly [string, string])[];
}): string {
  return digest([
    "browser-form-payload-v1",
    input.actionUrl,
    input.method,
    input.enctype,
    input.target,
    input.entries,
  ]);
}
