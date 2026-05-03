import type { ReactNode } from "react";
import {
  compactRecord,
  displayToolResult,
  formatToolValue,
} from "../../utils/jsonUtils";

export function mcpAuthDetails(
  args: Record<string, unknown>,
  result: unknown,
): ReactNode | null {
  const debug = compactRecord({
    server_id: args.server_id,
    server_name: args.server_name,
    approval_id: args.approval_id ?? args.action_id,
  });
  const renderedResult =
    result !== undefined ? (
      <>
        <small>Result</small>
        <pre>{formatToolValue(displayToolResult(result))}</pre>
      </>
    ) : null;
  if (!debug && !renderedResult) {
    return null;
  }
  return (
    <>
      {debug ? (
        <>
          <small>Debug</small>
          <pre>{formatToolValue(debug)}</pre>
        </>
      ) : null}
      {renderedResult}
    </>
  );
}
