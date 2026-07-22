// FirstRunSurfaceMount (web binder) — PRD-P8 §8 host wiring.
//
// The card's two optional callbacks are OMITTED-MEANS-NO-BUTTON by design: the
// package never renders a control that cannot work, so an unwired host silently
// ships a state ① with no action and a state ③ with no way forward. Nothing
// inside `packages/chat-surface` can catch that — only a host test can. Hence
// this file: it drives the REAL `useFirstRunLocalModel` + `FirstRunLocalCard` +
// `FirstRunSurface` through a faked `/v1/local-models/*` substrate and asserts
// what the binder is responsible for:
//
//   ① `onGetOllama`  — the button exists and opens the constant, externally.
//   ③ `onContinue`   — advances to the composer WITHOUT reopening the pull
//                      (the D4a seam that is NOT `onStartDownload`).
//   §7 `modelBlocked` — a daemon that dies while a send is queued exits the
//                      queued hold (`phase → "blocked"`) instead of hanging on
//                      "Queued — starts when the model lands" forever, and the
//                      ack stops echoing "· downloading 40%".
//   D2  no restart   — web reports `runtime_managed: false`, so state ④
//                      degrades to its instructional foot and the runtime-start
//                      route is never called.
//
// The local-models api module is faked at the `src/api/*` seam (the sanctioned
// substrate boundary), so the real `createFirstRunLocalModelsPort` — including
// its callback-SSE → AsyncIterable bridge — is under test too.

import type {
  LocalModelPullEvent,
  LocalModelsStatus,
} from "@0x-copilot/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// --- hoisted fakes (referenced from the vi.mock factories below) ------------

const api = vi.hoisted(() => ({
  getLocalModelsStatus: vi.fn(),
  listLocalModels: vi.fn(),
  startLocalModelRuntime: vi.fn(),
  streamLocalModelPull: vi.fn(),
}));

/** Every render's `useFirstRunLaunch` input + resulting phase, in order. */
const launchTrace = vi.hoisted(
  () =>
    [] as Array<{
      readonly modelReady: boolean;
      readonly modelBlocked: boolean | undefined;
      readonly phase: string;
    }>,
);

vi.mock("../../api/localModelsApi", () => api);

vi.mock("../../api/providerKeysApi", () => ({
  listProviderKeys: vi.fn(async () => ({ keys: [] })),
  putProviderKey: vi.fn(),
  deleteProviderKey: vi.fn(),
}));

vi.mock("../../api/agentApi", () => ({
  listModels: vi.fn(async () => ({
    default_model_id: "gpt-5.2",
    models: [
      {
        id: "gpt-5.2",
        provider: "openai",
        model_name: "gpt-5.2",
        name: "GPT-5.2",
        configured: true,
        supports_streaming: true,
      },
    ],
  })),
  createConversation: vi.fn(async () => ({ conversation_id: "conv_1" })),
  createRun: vi.fn(async () => ({ run_id: "run_1" })),
}));

// Keep every real chat-surface export — the surface, the card and the hooks are
// exactly what this test drives — but tap `useFirstRunLaunch` so the binder's
// inputs (and the phase they produce) are observable: the DOM shows the ack the
// phase produced, not the phase itself, so only the trace can prove the queued
// hold actually EXITED rather than being re-rendered.
vi.mock("@0x-copilot/chat-surface", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@0x-copilot/chat-surface")>();
  return {
    ...actual,
    useFirstRunLaunch: (
      options: Parameters<typeof actual.useFirstRunLaunch>[0],
    ): ReturnType<typeof actual.useFirstRunLaunch> => {
      const result = actual.useFirstRunLaunch(options);
      launchTrace.push({
        modelReady: options.modelReady,
        modelBlocked: options.modelBlocked,
        phase: result.phase,
      });
      return result;
    },
  };
});

// Imported after the mocks so the mocked module graph is in force.
import { TransportProvider } from "@0x-copilot/chat-surface";

import {
  getLocalModelsStatus,
  listLocalModels,
  startLocalModelRuntime,
  streamLocalModelPull,
} from "../../api/localModelsApi";
import type { RequestIdentity } from "../../api/config";
import {
  FirstRunSurfaceMount,
  OLLAMA_DOWNLOAD_URL,
} from "./FirstRunSurfaceMount";

// ---------------------------------------------------------------------------
// Substrate stubs
// ---------------------------------------------------------------------------

class NoopIntersectionObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): unknown[] {
    return [];
  }
}
if (typeof globalThis.IntersectionObserver === "undefined") {
  (
    globalThis as unknown as { IntersectionObserver: unknown }
  ).IntersectionObserver = NoopIntersectionObserver;
}

const IDENTITY: RequestIdentity = { orgId: "org-1", userId: "user-1" };

function fakeTransport(): Transport {
  return {
    request: <TRes,>(_req: TypedRequest): Promise<TRes> =>
      Promise.resolve({} as unknown as TRes),
    subscribeServerSentEvents: (
      _opts: SseSubscribeOptions,
    ): SseSubscription => ({ close: () => undefined }),
    getSession: (): Session => ({ bearer: null }),
    capabilities: (): TransportCapabilities => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** The capability probe the fake `/v1/local-models/status` currently answers. */
let currentStatus: LocalModelsStatus;

/** Live handlers of the open pull subscription (the fake SSE stream). */
let pullSubscriber: Parameters<typeof streamLocalModelPull>[0] | null = null;

function statusFixture(
  over: Partial<LocalModelsStatus> = {},
): LocalModelsStatus {
  return {
    enabled: true,
    ollama_running: false,
    ollama_version: null,
    runtime_state: "not_installed",
    // Web NEVER manages the runtime: `RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME`
    // defaults false and both web compose files pin it "false" (only
    // tools/desktop-runtime + apps/desktop set it true). PRD-P8 D2.
    runtime_managed: false,
    ...over,
  };
}

const RUNNING = statusFixture({
  ollama_running: true,
  ollama_version: "0.5.0",
  runtime_state: "running",
});

const STOPPED = statusFixture({ runtime_state: "stopped" });

function pullFrame(
  over: Partial<LocalModelPullEvent> = {},
): LocalModelPullEvent {
  return {
    sequence_no: 1,
    status: "pulling",
    bytes_total: 1000,
    // 40.1% — deliberately FRACTIONAL: `firstRunAckLines` interpolates the pct
    // raw, so an unrounded binder would echo "· downloading 40.1%" (and, on
    // real byte totals, "· downloading 46.72897196261682%").
    bytes_completed: 401,
    speed_bps: null,
    eta_seconds: null,
    done: false,
    error: null,
    error_kind: null,
    ...over,
  };
}

/** Run pending timers + microtasks inside `act` (fake timers are on). */
async function flush(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

/** Push one SSE frame into the open pull subscription. */
async function emit(frame: LocalModelPullEvent): Promise<void> {
  const subscriber = pullSubscriber;
  if (subscriber === null) {
    throw new Error("no pull subscription is open");
  }
  await act(async () => {
    subscriber.onEvent(frame);
    await vi.advanceTimersByTimeAsync(0);
  });
}

function renderMount(): void {
  render(
    <TransportProvider transport={fakeTransport()}>
      <FirstRunSurfaceMount onComplete={vi.fn()} identity={IDENTITY} />
    </TransportProvider>,
  );
}

// ---------------------------------------------------------------------------

describe("FirstRunSurfaceMount — PRD-P8 §8 web host wiring", () => {
  let openSpy: ReturnType<typeof vi.fn>;
  let originalOpen: typeof window.open;

  beforeEach(() => {
    vi.useFakeTimers();
    launchTrace.length = 0;
    pullSubscriber = null;
    currentStatus = statusFixture();

    vi.mocked(getLocalModelsStatus).mockImplementation(
      async () => currentStatus,
    );
    vi.mocked(listLocalModels).mockImplementation(async () => ({ models: [] }));
    vi.mocked(startLocalModelRuntime).mockImplementation(
      async () => currentStatus,
    );
    vi.mocked(streamLocalModelPull).mockImplementation((opts) => {
      pullSubscriber = opts;
      return { close: () => undefined };
    });

    // jsdom's window.open is "not implemented"; stub it so the external open is
    // observable (same pattern as the connectors OAuth-popup tests).
    originalOpen = window.open;
    openSpy = vi.fn();
    window.open = openSpy as unknown as typeof window.open;
  });

  afterEach(() => {
    window.open = originalOpen;
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("① wires Get Ollama to an external open of the constant download page", async () => {
    renderMount();
    await flush();

    // Web has no broker, but the seam must still be wired — an unwired host
    // renders the watch line with NO action at all.
    const getOllama = screen.getByTestId("first-run-local-get-ollama");
    expect(getOllama).toHaveTextContent("Get Ollama ↗");

    fireEvent.click(getOllama);

    expect(openSpy).toHaveBeenCalledTimes(1);
    expect(openSpy).toHaveBeenCalledWith(
      OLLAMA_DOWNLOAD_URL,
      "_blank",
      // No `window.opener` handle back into the signed-in app, no referrer.
      "noopener,noreferrer",
    );
    expect(OLLAMA_DOWNLOAD_URL).toBe("https://ollama.com/download");
  });

  it("③ 'Continue →' advances to the composer without restarting the auto-started pull", async () => {
    renderMount();
    await flush();
    expect(screen.getByTestId("first-run-local-watch")).toBeInTheDocument();

    // The runtime comes up while the card is watching → the hook auto-starts the
    // pull, and D4 keeps `stage === "choice"` so the card stays mounted.
    currentStatus = RUNNING;
    await flush(3_000);
    expect(vi.mocked(streamLocalModelPull)).toHaveBeenCalledTimes(1);

    await emit(pullFrame());
    expect(screen.getByTestId("first-run-local-progress")).toBeInTheDocument();

    const cont = screen.getByTestId("first-run-local-continue");
    expect(cont).toHaveTextContent("Continue →");
    await act(async () => {
      fireEvent.click(cont);
      await vi.advanceTimersByTimeAsync(0);
    });

    // Advanced to the composer …
    expect(screen.getByTestId("first-run-composer-h1")).toBeInTheDocument();
    // … and the in-flight pull was NOT reopened. `onContinue` must not be the
    // same seam as `onStartDownload` (which also fires the hook's `start`).
    expect(vi.mocked(streamLocalModelPull)).toHaveBeenCalledTimes(1);
  });

  it("§7 threads modelBlocked so a dead runtime exits the queued hold and the ack stops claiming a download", async () => {
    renderMount();
    await flush();
    currentStatus = RUNNING;
    await flush(3_000);
    await emit(pullFrame());

    // Nothing is wrong yet: the download is live and the model can still land.
    expect(launchTrace.at(-1)).toMatchObject({
      modelReady: false,
      modelBlocked: false,
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("first-run-local-continue"));
      await vi.advanceTimersByTimeAsync(0);
    });

    // Send the first prompt while the model is still downloading → queued.
    const textarea = screen.getByTestId("composer-textarea");
    fireEvent.change(textarea, { target: { value: "Watch my wallet" } });
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send message"));
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(launchTrace.at(-1)?.phase).toBe("queued");
    expect(screen.getByTestId("first-run-ack")).toHaveTextContent(
      "model — Qwen 3 4B · downloading 40%",
    );

    // The daemon dies mid-download. Pre-P8 this was the permanent hang: the pct
    // freezes, `modelReady` never flips, and the queued phase had no exit.
    currentStatus = STOPPED;
    await emit(
      pullFrame({
        sequence_no: 2,
        status: "error",
        error: "connection refused",
        error_kind: "runtime_unreachable",
      }),
    );
    await flush(0);

    expect(launchTrace.at(-1)?.modelBlocked).toBe(true);
    expect(launchTrace.at(-1)?.phase).toBe("blocked");
    // …and the acknowledgment stops echoing a download that is not happening.
    const ack = screen.getByTestId("first-run-ack");
    expect(ack).toHaveTextContent("model — Qwen 3 4B · download paused at 40%");
    // The TITLE has to stop lying too, or the ack argues with itself: "Queued —
    // starts when the model lands" directly above "· download paused at 40%".
    expect(ack).toHaveAttribute("data-variant", "stalled");
    expect(screen.getByTestId("first-run-ack-title")).toHaveTextContent(
      "Held — the model isn't downloading",
    );
    expect(screen.getByTestId("first-run-ack-note")).toHaveTextContent(
      "Restart Ollama or add a key — your prompt is saved.",
    );

    // …and it is not a dead end: the action returns the composer, and the
    // narrowed double-launch guard accepts the re-submit from `blocked`.
    await act(async () => {
      fireEvent.click(screen.getByTestId("first-run-ack-back"));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.queryByTestId("first-run-ack")).not.toBeInTheDocument();
    expect(screen.getByTestId("composer-textarea")).toBeInTheDocument();
  });

  it("§7 never announces the model as on-device before the pull actually completes", async () => {
    renderMount();
    await flush();
    currentStatus = RUNNING;
    await flush(3_000);

    // 99.6% — inside `modelSuffix`'s `pct >= 100` window once ROUNDED, but the
    // binder's `modelReady` only flips at an exact 100, so a rounded ack would
    // read "· on-device" while the launch is still queued waiting for it.
    // Ollama holds this pct across its status-only verify/manifest frames, so
    // the window is seconds long, not one frame.
    await emit(pullFrame({ bytes_completed: 996 }));

    await act(async () => {
      fireEvent.click(screen.getByTestId("first-run-local-continue"));
      await vi.advanceTimersByTimeAsync(0);
    });
    fireEvent.change(screen.getByTestId("composer-textarea"), {
      target: { value: "Watch my wallet" },
    });
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send message"));
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(launchTrace.at(-1)).toMatchObject({
      modelReady: false,
      phase: "queued",
    });
    const ack = screen.getByTestId("first-run-ack");
    expect(ack).toHaveTextContent("model — Qwen 3 4B · downloading 99%");
    expect(ack).not.toHaveTextContent("on-device");
  });

  it("④ renders no Restart affordance when the server does not manage the runtime", async () => {
    currentStatus = STOPPED;
    renderMount();
    await flush();

    expect(screen.getByTestId("first-run-local-stopped")).toBeInTheDocument();
    expect(screen.getByTestId("first-run-local-stopped-msg")).toHaveTextContent(
      "Ollama stopped responding",
    );
    // D2 — process control stays off web. A button whose route 404s is worse
    // than no button, so the foot degrades to the instructional line instead.
    expect(
      screen.queryByTestId("first-run-local-restart"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByTestId("first-run-local-stopped-watch"),
    ).toHaveTextContent("start Ollama again — the download resumes on its own");
    expect(vi.mocked(startLocalModelRuntime)).not.toHaveBeenCalled();
  });
});
