// E1 D5 source-open regression. This fixture enables Studio locally because
// production Studio remains dark by default; it verifies the Studio-only tab
// behavior without weakening the default-off product gate.

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import type { ConversationId } from "@0x-copilot/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { KeyValueStoreProvider } from "../../providers/KeyValueStoreProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import type { KeyValueStore } from "../../storage/key-value-store";

vi.mock("./useRunMode", async () => {
  const actual =
    await vi.importActual<typeof import("./useRunMode")>("./useRunMode");
  return {
    ...actual,
    STUDIO_ENABLED: true,
    useRunMode: () => {
      const [mode, setMode] = useState<"studio" | "focus">("studio");
      return {
        mode,
        setMode,
        toggle: () =>
          setMode((current) => (current === "studio" ? "focus" : "studio")),
      };
    },
  };
});

import { RunDestination } from "./RunDestination";

const CONVERSATION_ID = "conv-1" as ConversationId;
const ARTIFACT_ID = "art_550e8400-e29b-41d4-a716-446655440000";
const SOURCE_ID = "source:v2:001:artifact";

const CAPABILITIES: TransportCapabilities = {
  substrate: "web",
  nativeSecretStorage: false,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

class SourceOpenTransport implements Transport {
  readonly requests: TypedRequest[] = [];
  private onEvent: ((raw: string) => void) | undefined;

  async request<TRes>(request: TypedRequest): Promise<TRes> {
    this.requests.push(request);
    if (request.path.includes("/messages")) return { messages: [] } as TRes;
    if (
      request.path.endsWith(`/sources/${encodeURIComponent(SOURCE_ID)}/open`)
    ) {
      return {
        v: 2,
        source_id: SOURCE_ID,
        kind: "artifact",
        disposition: "artifact",
        artifact_id: ARTIFACT_ID,
        artifact_revision: 2,
        artifact_kind: "document",
      } as TRes;
    }
    if (request.path === `/v1/agent/artifacts/${ARTIFACT_ID}`) {
      return artifactDetail() as TRes;
    }
    if (request.path.endsWith(`/revisions/2`)) {
      return {
        revision: artifactDetail().current_revision,
        range_supported: false,
      } as TRes;
    }
    return {
      latest_run_id: "run-1",
      latest_run_id_any_status: "run-1",
      runs: [],
    } as TRes;
  }

  subscribeServerSentEvents(options: SseSubscribeOptions): SseSubscription {
    if (options.eventName === "runtime_event") this.onEvent = options.onMessage;
    return { close: () => {} };
  }

  emit(event: Record<string, unknown>): void {
    act(() => this.onEvent?.(JSON.stringify(event)));
  }

  getSession(): Session {
    return { bearer: null };
  }

  capabilities(): TransportCapabilities {
    return CAPABILITIES;
  }
}

function artifactDetail() {
  const revision = {
    artifact_id: ARTIFACT_ID,
    revision: 2,
    parent_revision: 1,
    content_ref: `artifact://${ARTIFACT_ID}/revisions/2`,
    content_digest: "a".repeat(64),
    byte_size: 12,
    author: "system",
    source_ref: "message://source",
    created_at: "2026-01-01T00:00:00Z",
  } as const;
  return {
    artifact: {
      artifact_id: ARTIFACT_ID,
      org_id: "org_test",
      user_id: "user_test",
      conversation_id: "conv-1",
      run_id: "run-1",
      kind: "document",
      title: "Source document",
      media_type: "text/markdown",
      current_revision: 2,
      created_by: "system",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
    current_revision: revision,
    suggested_filename: "source.md",
    range_supported: false,
  };
}

function makeStore(): KeyValueStore {
  return {
    get: () => null,
    set: () => {},
    keys: () => [],
  };
}

function surfaceTabs(): readonly HTMLElement[] {
  const strip = screen.getByTestId("tc-tabs");
  return within(strip).queryAllByRole("tab");
}

describe("RunDestination source-open", () => {
  it("opens and pins an ephemeral ArtifactSurface when provenance has no lifecycle tab", async () => {
    const transport = new SourceOpenTransport();
    render(
      <TransportProvider transport={transport}>
        <KeyValueStoreProvider store={makeStore()}>
          <RunDestination conversationId={CONVERSATION_ID} surfacesV2 />
        </KeyValueStoreProvider>
      </TransportProvider>,
    );
    await screen.findByTestId("thread-canvas");

    // Artifact creation contributes a canonical provenance fact, but without
    // artifact.presentation_decided it must not produce a lifecycle tab.
    transport.emit({
      event_id: "evt-source-artifact",
      run_id: "run-1",
      conversation_id: "conv-1",
      sequence_no: 1,
      event_type: "artifact.created",
      activity_kind: "event",
      payload: {
        artifact_id: ARTIFACT_ID,
        kind: "document",
        revision: 2,
        source_ref: `artifact://${ARTIFACT_ID}/revisions/2`,
      },
      created_at: "2026-01-01T00:00:00Z",
    });
    expect(surfaceTabs()).toHaveLength(0);

    fireEvent.click(screen.getByRole("tab", { name: "Sources" }));
    fireEvent.click(await screen.findByTestId("sources-v2-open-artifact"));

    await waitFor(() => {
      expect(transport.requests).toContainEqual(
        expect.objectContaining({
          method: "POST",
          path: `/v1/agent/runs/run-1/sources/${encodeURIComponent(SOURCE_ID)}/open`,
          body: {},
        }),
      );
      const tab = screen.getByRole("tab", { name: /document artifact · r2/i });
      expect(tab).toHaveAttribute("aria-selected", "true");
      expect(screen.getByTestId("artifact-frame")).toBeInTheDocument();
    });
  });
});
