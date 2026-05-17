// Route table for the tier-2 adapter review pipeline (Phase 7C).
//
// Two screens behind ``/admin/adapter-review``:
//   - queue (no candidate id) → AdapterReviewQueue
//   - detail (candidate id)   → AdapterReviewDetail

import type { ReactElement } from "react";

import type { RequestIdentity } from "../../api/config";

import { AdapterReviewDetail } from "./AdapterReviewDetail";
import { AdapterReviewQueue } from "./AdapterReviewQueue";

export type AdapterReviewRoute =
  | { readonly screen: "queue" }
  | { readonly screen: "detail"; readonly candidateId: string };

export interface AdapterReviewScreenProps {
  readonly identity: RequestIdentity;
  readonly route: AdapterReviewRoute;
  readonly onOpenCandidate: (candidateId: string) => void;
  readonly onBackToQueue: () => void;
}

export function AdapterReviewScreen({
  identity,
  route,
  onOpenCandidate,
  onBackToQueue,
}: AdapterReviewScreenProps): ReactElement {
  if (route.screen === "detail") {
    return (
      <AdapterReviewDetail
        candidateId={route.candidateId}
        onBack={onBackToQueue}
      />
    );
  }
  return <AdapterReviewQueue identity={identity} onOpen={onOpenCandidate} />;
}

export { AdapterReviewQueue } from "./AdapterReviewQueue";
export { AdapterReviewDetail } from "./AdapterReviewDetail";
export { AdapterPreview, PREVIEW_CSP } from "./AdapterPreview";
export { syntheticStateFor, allSyntheticStates } from "./SyntheticStateFactory";
export type { SyntheticState } from "./SyntheticStateFactory";
export * from "./types";
