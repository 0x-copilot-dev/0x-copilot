// Local models (Round 2 — download an HF GGUF + run it locally via Ollama).
//
//   GET    /v1/local-models/status              → LocalModelsStatus
//   GET    /v1/local-models                     → LocalModelsListResponse
//   GET    /v1/local-models/size?repo=&quant=   → LocalModelSize
//   GET    /v1/local-models/pull?repo=&quant=   → SSE stream of LocalModelPullEvent
//   DELETE /v1/local-models/{name}              → 204
//   POST   /v1/local-models/runtime/start       → LocalModelsStatus (PRD-P8 §4.3)
//
// Identity is the bearer header — the facade derives (org_id, user_id) from
// the verified session, so no identity query params (same convention as
// `/v1/me/*`). The pull stream rides the shared SSE transport lane (bearer
// attached via fetch headers, not EventSource).

import type {
  LocalModelPullEvent,
  LocalModelSize,
  LocalModelsListResponse,
  LocalModelsStatus,
} from "@0x-copilot/api-types";
import { getAppTransport } from "./transport";
import { httpJson } from "./http";

const PULL_EVENT_NAME = "local_model_pull";

export function getLocalModelsStatus(): Promise<LocalModelsStatus> {
  return httpJson<LocalModelsStatus>("GET", "/v1/local-models/status");
}

export function listLocalModels(): Promise<LocalModelsListResponse> {
  return httpJson<LocalModelsListResponse>("GET", "/v1/local-models");
}

export function getLocalModelSize(
  repo: string,
  quant: string,
): Promise<LocalModelSize> {
  return httpJson<LocalModelSize>("GET", "/v1/local-models/size", undefined, {
    repo,
    quant,
  });
}

/**
 * PRD-P8 §4.3 — start (or restart) the local runtime on the serving host.
 *
 * Server-authoritative: the route 404s unless the deployment permits this
 * server to manage the runtime process (the same gate reported as
 * `LocalModelsStatus.runtime_managed`), which on the web deployment it does
 * not. The web port still exposes it so ONE `FirstRunLocalModelsPort` shape
 * serves both hosts; the card never renders the button when `runtime_managed`
 * is false, so the 404 is a fail-closed backstop, not a normal path.
 */
export function startLocalModelRuntime(): Promise<LocalModelsStatus> {
  return httpJson<LocalModelsStatus>("POST", "/v1/local-models/runtime/start");
}

export async function deleteLocalModel(name: string): Promise<void> {
  await httpJson<void>(
    "DELETE",
    `/v1/local-models/${encodeURIComponent(name)}`,
  );
}

export interface LocalModelPullStream {
  close(): void;
}

/**
 * Open the pull-progress SSE stream for one HF GGUF (repo + quant). Malformed
 * frames are dropped without tearing down the stream (mirrors `agentsApi`).
 * The stream ends with a `done: true` frame or a terminal `status: "error"`
 * frame carrying `error`; the caller closes the handle either way.
 */
export function streamLocalModelPull({
  repo,
  quant,
  onEvent,
  onError,
  onOpen,
}: {
  readonly repo: string;
  readonly quant: string;
  readonly onEvent: (event: LocalModelPullEvent) => void;
  readonly onError?: (err: Error) => void;
  readonly onOpen?: () => void;
}): LocalModelPullStream {
  return getAppTransport().subscribeServerSentEvents({
    path: "/v1/local-models/pull",
    query: { repo, quant },
    eventName: PULL_EVENT_NAME,
    onOpen,
    onError: (err) => onError?.(err),
    onMessage: (data) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(data) as unknown;
      } catch {
        return;
      }
      if (isLocalModelPullEvent(parsed)) {
        onEvent(parsed);
      }
    },
  });
}

function isLocalModelPullEvent(value: unknown): value is LocalModelPullEvent {
  if (!value || typeof value !== "object") {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    typeof record.sequence_no === "number" &&
    typeof record.status === "string" &&
    typeof record.done === "boolean"
  );
}
