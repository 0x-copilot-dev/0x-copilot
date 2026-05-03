import type { RuntimeEventPresentation } from "@enterprise-search/api-types";
import type { ReactElement, ReactNode } from "react";
import { ActivityCard } from "./ActivityCard";
import { ActivityItem } from "./ActivityItem";
import { PresentationResultRows } from "./PresentationResultRows";
import { activityVariantForPresentation } from "./presentationHelpers";
import type { ActivityVariant } from "./types";

export function GeneratedPresentationCard({
  presentation,
  details,
  forceCard = false,
  variant,
}: {
  presentation: RuntimeEventPresentation;
  details?: ReactNode;
  forceCard?: boolean;
  variant?: ActivityVariant;
}): ReactElement {
  const result =
    presentation.result_preview && presentation.result_preview.length > 0 ? (
      <PresentationResultRows rows={presentation.result_preview} />
    ) : undefined;
  const cardVariant = variant ?? activityVariantForPresentation(presentation);
  if (!forceCard && presentation.kind === "progress") {
    return (
      <ActivityItem
        title={presentation.title}
        status={presentation.status_label}
        variant={cardVariant}
        description={presentation.summary}
        result={result}
        details={details}
        detailsLabel={presentation.debug_label ?? "Tool details"}
      />
    );
  }
  return (
    <ActivityCard
      title={presentation.title}
      status={presentation.status_label}
      variant={cardVariant}
      description={presentation.summary}
      result={result}
      details={details}
      detailsLabel={presentation.debug_label ?? "Tool details"}
    />
  );
}
