// AC8 agentic browser — engine abstraction.
//
// The session logic depends on THIS narrow interface, never on Playwright
// directly, so: (a) unit tests inject a fake engine and never launch Chromium,
// and (b) the real Playwright dependency is lazily imported ONLY inside the
// worker child (`createPlaywrightEngine`), keeping it out of Electron main,
// preload, renderer, and the typecheck graph of everything else.
//
// The interface exposes ONLY the read-only surface this foundation needs:
// navigate, accessibility snapshot, screenshot, wait, url/title. There is no
// `evaluate`, selector query, CDP, or arbitrary-method passthrough.

/** A raw accessibility node as returned by the engine (Playwright-shaped). */
export interface RawAxNode {
  role: string;
  name?: string;
  /** Present for inputs — NEVER forwarded to the model. */
  value?: string;
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
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const pw: any = await import("playwright");
  const chromium = pw.chromium;
  const launchArgs = [
    "--disable-quic",
    "--disable-features=WebRtcHideLocalIpsWithMdns",
    "--no-default-browser-check",
    "--no-first-run",
  ];

  const version = String(chromium?._version ?? "chromium-pinned");

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
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
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

// eslint-disable-next-line @typescript-eslint/no-explicit-any
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

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function wrapPage(page: any): EnginePage {
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
      return (await page.accessibility.snapshot()) as RawAxNode | null;
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
    // The model addresses elements by generation-bound ref + redacted
    // role/name; the engine resolves them via ARIA role/name locators — there
    // is NO raw-selector, coordinate, or `evaluate` passthrough. Best-effort;
    // the read-only + action CONTRACTS are covered by the fake-engine suites.
    async clickRef(target) {
      await locate(page, target).click();
    },
    async fillRef(target, text) {
      await locate(page, target).fill(text);
    },
    async selectRef(target, value) {
      await locate(page, target).selectOption(value);
    },
    async submitRef(target) {
      // A submit is a click on the reviewed submit control.
      await locate(page, target).click();
    },
    async downloadViaRef(target, { timeoutMs }) {
      const [download] = await Promise.all([
        page.waitForEvent("download", { timeout: timeoutMs }),
        locate(page, target).click(),
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

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function locate(page: any, target: ElementTarget): any {
  // Prefer an accessible role+name locator; fall back to name text. This never
  // accepts a raw CSS/XPath selector from the model.
  if (target.name !== "") {
    return page.getByRole(target.role, { name: target.name }).first();
  }
  return page.getByRole(target.role).first();
}
