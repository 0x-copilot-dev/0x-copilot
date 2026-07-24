/** B1 user-driven promotion seam, owned by the shared chat substrate. */

import type {
  ArtifactKind,
  ArtifactMutationResponse,
} from "@0x-copilot/api-types";
import {
  promoteArtifact,
  type PromoteArtifactRequest,
} from "@0x-copilot/chat-transport";

import type { Transport } from "./Transport";

export interface PromoteArtifactInput {
  readonly runId: string;
  readonly sourceRef: string;
  readonly kind: ArtifactKind;
  readonly title?: string;
  readonly mediaType?: string;
  readonly suggestedFilename?: string;
  readonly idempotencyKey: string;
}

/** A host-independent promotion port; renderers remain deliberately out of scope. */
export interface ArtifactPromotionPort {
  promote(input: PromoteArtifactInput): Promise<ArtifactMutationResponse>;
}

export function createArtifactPromotionPort(
  transport: Transport,
): ArtifactPromotionPort {
  return {
    promote(input) {
      const request: PromoteArtifactRequest = {
        runId: input.runId,
        sourceRef: input.sourceRef,
        kind: input.kind,
        title: input.title,
        mediaType: input.mediaType,
        suggestedFilename: input.suggestedFilename,
        idempotencyKey: input.idempotencyKey,
      };
      return promoteArtifact<ArtifactMutationResponse>(transport, request);
    },
  };
}
