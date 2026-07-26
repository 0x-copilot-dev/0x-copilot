import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { type ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

import type {
  ConversationId,
  RuntimeEventEnvelope,
} from "@0x-copilot/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";

import type { ArtifactDownloadPort } from "../../ports/ArtifactDownloadPort";
import type { WorkspaceStageHost } from "../../ports/WorkspaceStageHostPort";
import { KeyValueStoreProvider } from "../../providers/KeyValueStoreProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import type { KeyValueStore } from "../../storage/key-value-store";
import { RunDestination } from "./RunDestination";

const CONVERSATION_ID = "conv_workspace_1" as ConversationId;
const RUN_ID = "run_workspace_1";
const STAGE_ID = "stage_workspace_1";
const ARTIFACT_ID = "artifact_workspace_1";
const PROPOSAL_DIGEST = "a".repeat(64);
const TARGET_DIGEST = "b".repeat(64);

const CAPABILITIES: TransportCapabilities = {
  substrate: "web",
  nativeSecretStorage: false,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

interface CapturedSubscription {
  readonly path: string;
  readonly eventName?: string;
  readonly onMessage?: (raw: string) => void;
  closed: boolean;
}

class WorkspaceTransport implements Transport {
  readonly requests: TypedRequest[] = [];
  readonly subscriptions: CapturedSubscription[] = [];
  readonly artifactRequests: Array<{ artifactId: string; revision: number }> =
    [];
  effectDecisionResponse: unknown = {
    stage_id: STAGE_ID,
    revision: 1,
    decision_ledger_id: "ledger_workspace_1",
    change_set_digest: "c".repeat(64),
    proposal_digest: PROPOSAL_DIGEST,
    target_digest: TARGET_DIGEST,
    decision: "approve",
    status: "approved",
  };

  async request<TRes>(request: TypedRequest): Promise<TRes> {
    this.requests.push(request);
    if (request.path.includes("/messages")) return { messages: [] } as TRes;
    if (request.path.endsWith("/surfaces")) return { surfaces: [] } as TRes;
    if (request.path.includes("/pending-work")) {
      return { cards: [], agents: [] } as TRes;
    }
    if (request.path.includes(`/effect-stages/${STAGE_ID}/decisions`)) {
      return this.effectDecisionResponse as TRes;
    }
    return {
      latest_run_id: RUN_ID,
      latest_run_id_any_status: RUN_ID,
      runs: [{ run_id: RUN_ID, status: "running", goal: "Save workspace CSV" }],
    } as TRes;
  }

  subscribeServerSentEvents(options: SseSubscribeOptions): SseSubscription {
    const subscription: CapturedSubscription = {
      path: options.path,
      eventName: options.eventName,
      onMessage: options.onMessage,
      closed: false,
    };
    this.subscriptions.push(subscription);
    return { close: () => (subscription.closed = true) };
  }

  getSession(): Session {
    return { bearer: null };
  }

  capabilities(): TransportCapabilities {
    return CAPABILITIES;
  }

  async getArtifactContent(request: {
    readonly artifactId: string;
    readonly revision: number;
  }): Promise<{
    readonly body: ReadableStream<Uint8Array>;
    readonly contentType: string;
    readonly contentLength: number | null;
    readonly etag: string | null;
    readonly filename: string | null;
  }> {
    this.artifactRequests.push(request);
    return {
      body: new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode("amount\n100\n"));
          controller.close();
        },
      }),
      contentType: "text/csv",
      contentLength: 11,
      etag: null,
      filename: "report.csv",
    };
  }

  async createArtifactRevision(): Promise<unknown> {
    return {};
  }

  get sessionSubscription(): CapturedSubscription | undefined {
    return [...this.subscriptions]
      .reverse()
      .find((item) => !item.closed && item.eventName === "runtime_event");
  }
}

function store(): KeyValueStore {
  const values = new Map<string, string>();
  return {
    get: (key) => values.get(key) ?? null,
    set: (key, value) => {
      if (value === null) values.delete(key);
      else values.set(key, value);
    },
    keys: (prefix) =>
      [...values.keys()].filter(
        (key) => prefix === undefined || key.startsWith(prefix),
      ),
  };
}

function runtimeEvent(
  sequenceNo: number,
  eventType: string,
  payload: Record<string, unknown>,
): RuntimeEventEnvelope {
  return {
    event_id: `evt_workspace_${sequenceNo}`,
    run_id: RUN_ID,
    conversation_id: CONVERSATION_ID,
    sequence_no: sequenceNo,
    event_type: eventType,
    activity_kind: "event",
    payload,
    created_at: new Date(1_700_000_000_000 + sequenceNo * 1_000).toISOString(),
  } as unknown as RuntimeEventEnvelope;
}

function workspaceStageEvents(includeArtifact = false): RuntimeEventEnvelope[] {
  const events: RuntimeEventEnvelope[] = includeArtifact
    ? [
        runtimeEvent(1, "artifact.created", {
          v: 1,
          artifact_id: ARTIFACT_ID,
          revision: 1,
          kind: "dataset",
          content_ref: "content_workspace_1",
        }),
        runtimeEvent(2, "artifact.revised", {
          v: 1,
          artifact_id: ARTIFACT_ID,
          revision: 2,
          content_ref: "content_workspace_2",
        }),
      ]
    : [];
  events.push(
    runtimeEvent(includeArtifact ? 3 : 1, "effect.staged", {
      v: 1,
      stage_id: STAGE_ID,
      operation_id: "operation_workspace_1",
      executor: "workspace",
      target_ref: "target://private-workspace-root",
      target_digest: TARGET_DIGEST,
      proposal_ref: "proposal://private-workspace-change-set",
      proposal_digest: PROPOSAL_DIGEST,
      proposal_content_ref: includeArtifact ? "content_workspace_2" : undefined,
      policy: "ask",
      op: "create_file",
      display_target: "/workspace/Finance/report.csv",
      author_actor: "user",
    }),
  );
  return events;
}

function renderRun(options: {
  readonly transport: WorkspaceTransport;
  readonly workspaceStageHost?: WorkspaceStageHost;
  readonly artifactDownloadPort?: ArtifactDownloadPort;
  readonly surfacesV2?: boolean;
}) {
  const ui: ReactElement = (
    <TransportProvider transport={options.transport}>
      <KeyValueStoreProvider store={store()}>
        <RunDestination
          conversationId={CONVERSATION_ID}
          surfacesV2={options.surfacesV2 ?? true}
          workspaceStageHost={options.workspaceStageHost}
          artifactDownloadPort={options.artifactDownloadPort}
        />
      </KeyValueStoreProvider>
    </TransportProvider>
  );
  return render(ui);
}

async function mountWorkspaceStage(
  transport: WorkspaceTransport,
  includeArtifact = false,
  expectWorkspaceSurface = true,
): Promise<void> {
  await screen.findByTestId("thread-canvas");
  await waitFor(() => expect(transport.sessionSubscription).toBeDefined());
  act(() => {
    for (const item of workspaceStageEvents(includeArtifact)) {
      transport.sessionSubscription?.onMessage?.(JSON.stringify(item));
    }
  });
  if (expectWorkspaceSurface) {
    await screen.findByTestId("tc-workspace-stage");
  }
}

describe("RunDestination workspace EffectStage integration", () => {
  it("mounts the canonical workspace stage in Studio and sends desktop only the narrow snapshot", async () => {
    const transport = new WorkspaceTransport();
    const decisions: unknown[] = [];
    const decide = vi.fn(async (input: unknown) => {
      decisions.push(input);
      return {
        stageId: STAGE_ID,
        revision: 1,
        decision: "approve" as const,
        status: "approved" as const,
      };
    });
    renderRun({
      transport,
      workspaceStageHost: {
        kind: "desktop",
        approvalPort: { decide },
      },
    });

    await mountWorkspaceStage(transport);
    expect(screen.getByTestId("tc-workspace-stage-path")).toHaveTextContent(
      "/workspace/Finance/report.csv",
    );
    expect(screen.queryByTestId("effect-stage-card")).toBeNull();

    fireEvent.click(screen.getByTestId("tc-workspace-stage-approve"));
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(1));
    expect(decide).toHaveBeenCalledWith({
      snapshot: {
        runId: RUN_ID,
        stageId: STAGE_ID,
        revision: 1,
        proposalDigest: PROPOSAL_DIGEST,
        targetDigest: TARGET_DIGEST,
      },
      decision: "approve",
    });
    const outbound = JSON.stringify(decisions[0]);
    expect(outbound).not.toMatch(
      /target_ref|proposal_ref|prepared|permit|path/i,
    );
    expect(
      transport.requests.some((request) =>
        request.path.includes("/effect-stages/"),
      ),
    ).toBe(false);
  });

  it("uses only the canonical facade decision in web and offers a download fallback", async () => {
    const transport = new WorkspaceTransport();
    const saveArtifact = vi.fn(async () => {});
    renderRun({
      transport,
      workspaceStageHost: { kind: "web" },
      artifactDownloadPort: { saveArtifact },
    });

    await mountWorkspaceStage(transport, true);
    expect(
      screen.getByTestId("tc-workspace-stage-action-unavailable"),
    ).toHaveTextContent("cannot write to your local workspace");
    expect(screen.getByTestId("tc-workspace-stage-edit")).toHaveTextContent(
      "Edit artifact",
    );

    fireEvent.click(screen.getByTestId("tc-workspace-stage-approve"));
    await waitFor(() =>
      expect(
        transport.requests.find((request) =>
          request.path.includes(`/effect-stages/${STAGE_ID}/decisions`),
        ),
      ).toEqual({
        method: "POST",
        path: `/v1/agent/effect-stages/${STAGE_ID}/decisions?run_id=${RUN_ID}`,
        body: {
          revision: 1,
          decision: "approve",
          proposal_digest: PROPOSAL_DIGEST,
          target_digest: TARGET_DIGEST,
        },
      }),
    );

    fireEvent.click(screen.getByTestId("tc-workspace-stage-download-artifact"));
    await waitFor(() => expect(saveArtifact).toHaveBeenCalledTimes(1));
    expect(transport.artifactRequests).toEqual([
      { artifactId: ARTIFACT_ID, revision: 2 },
    ]);
    expect(
      screen.getByTestId("tc-workspace-stage-action-unavailable"),
    ).toHaveTextContent("No local workspace change was made");
  });

  it("fails closed for a desktop host error or cancellation and never optimistically applies", async () => {
    const transport = new WorkspaceTransport();
    const decide = vi
      .fn()
      .mockRejectedValueOnce(new Error("main unavailable"))
      .mockResolvedValueOnce({
        stageId: STAGE_ID,
        revision: 1,
        decision: "reject",
        status: "rejected",
      });
    renderRun({
      transport,
      workspaceStageHost: { kind: "desktop", approvalPort: { decide } },
    });

    await mountWorkspaceStage(transport);
    fireEvent.click(screen.getByTestId("tc-workspace-stage-approve"));
    await waitFor(() =>
      expect(
        screen.getByTestId("tc-workspace-stage-action-unavailable"),
      ).toHaveTextContent("No workspace change was made"),
    );
    expect(screen.getByTestId("tc-workspace-stage-status")).toHaveTextContent(
      "Awaiting review",
    );

    fireEvent.click(screen.getByTestId("tc-workspace-stage-reject"));
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("tc-workspace-stage-status")).toHaveTextContent(
      "Awaiting review",
    );
    expect(
      transport.requests.some((request) => request.path.includes("/stages/")),
    ).toBe(false);
  });

  it("keeps Focus compact and leaves no-host/flag-off callers on their existing path", async () => {
    const transport = new WorkspaceTransport();
    const mounted = renderRun({ transport });
    await mountWorkspaceStage(transport, false, false);
    expect(screen.queryByTestId("tc-workspace-stage")).toBeNull();
    expect(screen.getByTestId("effect-stage-card")).not.toBeNull();

    // Host-equipped Studio mounts the rich review surface; Focus hides it and
    // shows only the lifecycle's compact card.
    mounted.unmount();
    const hostedTransport = new WorkspaceTransport();
    const hosted = renderRun({
      transport: hostedTransport,
      workspaceStageHost: {
        kind: "desktop",
        approvalPort: {
          decide: async () => ({
            stageId: STAGE_ID,
            revision: 1,
            decision: "approve",
            status: "approved",
          }),
        },
      },
    });
    await mountWorkspaceStage(hostedTransport);
    fireEvent.click(screen.getByTestId("run-mode-focus"));
    await waitFor(() =>
      expect(screen.getByTestId("run-canvas-slot")).toHaveAttribute(
        "data-visible",
        "false",
      ),
    );
    expect(screen.getByTestId("canvas-focus-cards")).toHaveTextContent(
      "PROPOSED CHANGE",
    );

    hosted.unmount();
    const flagOffTransport = new WorkspaceTransport();
    renderRun({ transport: flagOffTransport, surfacesV2: false });
    await screen.findByTestId("thread-canvas");
    await waitFor(() =>
      expect(flagOffTransport.sessionSubscription).toBeDefined(),
    );
    act(() => {
      for (const item of workspaceStageEvents()) {
        flagOffTransport.sessionSubscription?.onMessage?.(JSON.stringify(item));
      }
    });
    expect(screen.queryByTestId("tc-workspace-stage")).toBeNull();
  });
});
