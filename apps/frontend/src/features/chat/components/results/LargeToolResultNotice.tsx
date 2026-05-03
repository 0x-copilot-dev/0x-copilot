import type { ReactElement } from "react";
import type { LargeToolResult } from "../../utils/jsonUtils";

export function LargeToolResultNotice({
  compact = false,
}: {
  result: LargeToolResult;
  compact?: boolean;
}): ReactElement {
  return (
    <div className="aui-tool-card__notice">
      <span className="aui-tool-card__notice-title">Large result saved</span>
      {compact ? null : (
        <p>
          The connector returned more data than fits in chat. The agent can
          inspect the saved response when it needs details.
        </p>
      )}
    </div>
  );
}
