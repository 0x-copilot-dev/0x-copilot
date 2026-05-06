import type { ToolCallMessagePartProps } from "../../runtime/types";
import type { ReactElement } from "react";
import { asRecord } from "../../utils/jsonUtils";
import { toolStatusLabel } from "../../utils/toolLabels";
import { ActivityCard } from "../activity/ActivityCard";
import { GeneratedPresentationCard } from "../activity/GeneratedPresentationCard";
import { presentationFromArgs } from "../activity/presentationHelpers";
import { toolDetailsContent } from "../details/toolDetailsContent";

export function ProgressTool(props: ToolCallMessagePartProps): ReactElement {
  const data = asRecord(props.args);
  const presentation = presentationFromArgs(data);
  if (presentation) {
    return (
      <GeneratedPresentationCard
        presentation={presentation}
        details={toolDetailsContent(props.argsText, props.result)}
      />
    );
  }
  const status =
    typeof data.status === "string"
      ? data.status
      : toolStatusLabel(props.status.type, props.isError);
  return (
    <ActivityCard
      title={String(data.title ?? "Progress")}
      status={status}
      variant="progress"
      description={typeof data.summary === "string" ? data.summary : undefined}
      details={toolDetailsContent(props.argsText, props.result)}
    />
  );
}
