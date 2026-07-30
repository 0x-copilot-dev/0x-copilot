// The accent seam, observed where it actually runs.
//
// One artifact's chosen accent has to reach TWO places from ONE record: the
// canvas tab, and the card that tab opens. The component-level hue tests each
// render a tab and a card from a single literal spread into both, so what they
// actually establish is that one object equals itself. They were green for the
// whole period the app diverged, because the divergence does not live in
// `TcTabs` or in `ArtifactSurface` — it lives in `RunDestination`, the only
// thing that reads the conversation-canvas record and hands the value to both.
//
// So this mounts that composition. The accent is stated exactly once, on the
// wire, by the conversation-canvas endpoint; no component in this file is handed
// a hue by the test. Both halves are then read back off the DOM and required to
// agree on the CHOSEN value — not merely to be equal, which they also would be
// if the wiring were deleted and both fell back to the hue the artifact URI
// implies on its own. Every assertion below pins the choice against that
// fallback, so removing the prop at the call site turns them red.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import { type ReactElement } from "react";
import { describe, expect, it } from "vitest";

import {
  ArtifactContentRefCodec,
  type ConversationId,
  type RunId,
} from "@0x-copilot/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";

import { artifactUri } from "../../artifacts/uri";
import { KeyValueStoreProvider } from "../../providers/KeyValueStoreProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import type { KeyValueStore } from "../../storage/key-value-store";
import { surfaceHueForUri, type SurfaceHue } from "../../surfaces/surfaceHue";
import { RunDestination } from "./RunDestination";

const CONV = "conv-1" as ConversationId;
const RUN = "run-1";
const ARTIFACT = "art_550e8400-e29b-41d4-a716-446655440000";
const REVISION = 1;
const CONTENT = "region,forecast\nEMEA,412\n";

/** The one surface both halves describe. */
const URI = artifactUri("dataset", ARTIFACT, REVISION);
/** What the URI alone says — i.e. what a dropped accent degrades to. */
const URI_HUE = surfaceHueForUri(URI);
/** The author's choice, deliberately a hue the URI would never produce. */
const CHOSEN: SurfaceHue = "ember";

const CAPABILITIES: TransportCapabilities = {
  substrate: "web",
  nativeSecretStorage: false,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

interface CapturedSub {
  readonly path: string;
  readonly eventName?: string;
  readonly onMessage?: (raw: string) => void;
  closed: boolean;
}

function artifactRevision() {
  return {
    artifact_id: ARTIFACT,
    revision: REVISION,
    content_ref: ArtifactContentRefCodec.format(ARTIFACT, REVISION),
    content_digest: "0".repeat(64),
    byte_size: CONTENT.length,
    author: "model",
    source_ref: "message://source",
    created_at: "2026-07-28T00:00:00Z",
  };
}

function artifactDetail() {
  return {
    artifact: {
      artifact_id: ARTIFACT,
      org_id: "org_test",
      user_id: "user_test",
      conversation_id: "conv-1",
      run_id: RUN,
      kind: "dataset",
      title: "Q3 forecast",
      media_type: "text/csv",
      current_revision: REVISION,
      created_by: "model",
      created_at: "2026-07-28T00:00:00Z",
      updated_at: "2026-07-28T00:00:00Z",
    },
    current_revision: artifactRevision(),
    suggested_filename: "forecast.csv",
    range_supported: false,
  };
}

/**
 * The conversation record, in wire shape. `accent` is the ONLY statement of the
 * author's choice anywhere in this file — everything the test asserts has to
 * have travelled from here, through the production component, to the DOM.
 */
function wireSubject(accent: SurfaceHue | null) {
  return {
    subject_key: `artifact:${ARTIFACT}`,
    kind: "artifact",
    subject_id: ARTIFACT,
    run_id: RUN,
    title: "Q3 forecast",
    revision: REVISION,
    renderer_hint: "artifact-dataset",
    accent,
    created_at: "2026-07-28T00:00:00Z",
  };
}

/**
 * A Transport that serves the conversation canvas and the artifact itself.
 *
 * Artifact-capable on purpose: a dataset artifact only reaches a mounted card
 * when its bytes can be streamed, and the card is half of what is being
 * compared.
 */
class CanvasTransport implements Transport {
  readonly requests: TypedRequest[] = [];
  readonly subs: CapturedSub[] = [];

  constructor(private readonly accent: SurfaceHue | null) {}

  async request<TRes>(req: TypedRequest): Promise<TRes> {
    this.requests.push(req);
    if (req.path.endsWith("/canvas"))
      return { subjects: [wireSubject(this.accent)] } as TRes;
    if (req.path === `/v1/agent/artifacts/${ARTIFACT}`)
      return artifactDetail() as TRes;
    if (/\/artifacts\/[^/]+\/revisions\/\d+$/.test(req.path))
      return { revision: artifactRevision(), range_supported: false } as TRes;
    if (req.path.includes("/messages")) return { messages: [] } as TRes;
    return {
      latest_run_id: RUN,
      latest_run_id_any_status: RUN,
      runs: [],
    } as TRes;
  }

  subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription {
    const sub: CapturedSub = {
      path: opts.path,
      eventName: opts.eventName,
      onMessage: opts.onMessage,
      closed: false,
    };
    this.subs.push(sub);
    return { close: () => (sub.closed = true) };
  }

  getSession(): Session {
    return { bearer: null };
  }

  capabilities(): TransportCapabilities {
    return CAPABILITIES;
  }

  async getArtifactContent(): Promise<{
    body: ReadableStream<Uint8Array>;
    contentType: string;
    contentLength: number;
    etag: string;
    filename: string;
  }> {
    return {
      body: new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(CONTENT));
          controller.close();
        },
      }),
      contentType: "text/csv",
      contentLength: CONTENT.length,
      etag: '"1"',
      filename: "forecast.csv",
    };
  }

  async createArtifactRevision(): Promise<never> {
    throw new Error("this test never writes a revision");
  }

  emit(events: readonly Record<string, unknown>[]): void {
    const sub = [...this.subs]
      .reverse()
      .find(
        (s) =>
          !s.closed && s.eventName === "runtime_event" && s.path.includes(RUN),
      );
    act(() => {
      for (const event of events) sub?.onMessage?.(JSON.stringify(event));
    });
  }
}

function makeStore(): KeyValueStore {
  const map = new Map<string, string>();
  return {
    get: (k) => map.get(k) ?? null,
    set: (k, v) => {
      if (v === null) map.delete(k);
      else map.set(k, v);
    },
    keys: (prefix) =>
      [...map.keys()].filter(
        (k) => prefix === undefined || k.startsWith(prefix),
      ),
  };
}

/** Turn 1 as the ledger records it: an artifact, decided onto the canvas. */
function publishedArtifact(): readonly Record<string, unknown>[] {
  const base = {
    run_id: RUN,
    conversation_id: "conv-1",
    activity_kind: "event",
    created_at: "2026-07-28T00:00:01Z",
  };
  return [
    {
      ...base,
      event_id: "evt-1",
      sequence_no: 1,
      event_type: "artifact.created",
      payload: {
        v: 1,
        artifact_id: ARTIFACT,
        kind: "dataset",
        revision: REVISION,
        content_ref: ArtifactContentRefCodec.format(ARTIFACT, REVISION),
        content_digest: "0".repeat(64),
        author: "model",
      },
    },
    {
      ...base,
      event_id: "evt-2",
      sequence_no: 2,
      event_type: "artifact.presentation_decided",
      payload: {
        v: 1,
        artifact_id: ARTIFACT,
        decision: "canvas",
        basis: "explicit_artifact_canvas",
      },
    },
    {
      ...base,
      event_id: "evt-3",
      sequence_no: 3,
      event_type: "run_completed",
      payload: { status: "run_completed" },
    },
  ];
}

function ui(transport: Transport, store: KeyValueStore): ReactElement {
  return (
    <TransportProvider transport={transport}>
      <KeyValueStoreProvider store={store}>
        <RunDestination
          conversationId={CONV}
          runId={RUN as unknown as RunId}
          surfacesV2
        />
      </KeyValueStoreProvider>
    </TransportProvider>
  );
}

/**
 * What the two halves of the seam claim, read off the rendered DOM.
 *
 * The tab is the selected one in the canvas strip, pinned to the artifact's URI
 * so it cannot silently become some other tab; the card is the mount inside the
 * artifact frame, so it cannot match an unrelated mount elsewhere in the
 * cockpit. Both are re-queried on every call — a hue is an attribute on a node
 * React re-renders, so a value captured once would go stale.
 */
function renderedHues(): {
  readonly tab: string | null;
  readonly card: string | null;
} {
  const tab = within(screen.getByTestId("tc-tabs")).getByRole("tab", {
    selected: true,
  });
  expect(tab).toHaveAttribute("data-uri", URI);
  return {
    tab: tab.getAttribute("data-surface-hue"),
    card: within(screen.getByTestId("artifact-frame"))
      .getByTestId("tc-surface-mount")
      .getAttribute("data-surface-hue"),
  };
}

/**
 * Wait for the artifact to be on screen as both a tab and a card, then hold
 * `check` against them.
 *
 * The two arrivals are separate — the strip can render before the artifact's
 * bytes have loaded, and the accent arrives on its own conversation fetch — so
 * the wait is on the assertion itself rather than on any one of them. Waiting
 * for the frame and asserting after would sample whichever intermediate state
 * that moment happened to hold.
 */
async function expectSeam(
  check: (hues: {
    readonly tab: string | null;
    readonly card: string | null;
  }) => void,
): Promise<void> {
  await screen.findByTestId("artifact-frame");
  await waitFor(() => check(renderedHues()));
}

describe("RunDestination — the accent reaches the tab AND the card", () => {
  // Guards every "not the default" assertion below against going vacuous if the
  // hue ring or the URI mapping is ever changed: the choice has to be
  // distinguishable from the fallback for any of this to mean anything.
  it("uses a chosen accent the artifact URI could not produce on its own", () => {
    expect(URI_HUE).not.toBe(CHOSEN);
  });

  it("shows the chosen accent on both when this run published the artifact", async () => {
    // The ordinary case, and the one that was broken: publish an artifact and
    // look at it in the same run. The run fold supplies the tab, the record
    // supplies the accent, and the card is a separate consumer of that record.
    const transport = new CanvasTransport(CHOSEN);
    render(ui(transport, makeStore()));
    await screen.findByTestId("thread-canvas");
    transport.emit(publishedArtifact());

    await expectSeam(({ tab, card }) => {
      expect(card).toBe(tab);
      expect(card).toBe(CHOSEN);
      expect(card).not.toBe(URI_HUE);
    });
  });

  it("shows the chosen accent on both for an artifact from an earlier turn", async () => {
    // The other path into the strip: no events at all, so the tab comes from
    // the conversation record directly rather than from this run's fold. It
    // reads the accent off the subject while the card reads a map built beside
    // it — two derivations that have to land on one value.
    const transport = new CanvasTransport(CHOSEN);
    render(ui(transport, makeStore()));
    await screen.findByTestId("thread-canvas");

    await expectSeam(({ tab, card }) => {
      expect(card).toBe(tab);
      expect(card).toBe(CHOSEN);
      expect(card).not.toBe(URI_HUE);
    });
  });

  it("falls back to the URI's own hue on both when nothing was chosen", async () => {
    // The absence has to agree too. If it did not, an artifact with no accent
    // would show one colour on its tab and another on its card — the same
    // defect, arrived at from the other direction.
    const transport = new CanvasTransport(null);
    render(ui(transport, makeStore()));
    await screen.findByTestId("thread-canvas");
    transport.emit(publishedArtifact());

    await expectSeam(({ tab, card }) => {
      expect(card).toBe(tab);
      expect(card).toBe(URI_HUE);
    });
  });
});
