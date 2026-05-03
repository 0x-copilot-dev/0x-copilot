import type { ReactNode } from "react";
import {
  compactRecord,
  displayToolResult,
  formatToolValue,
  hasVisibleValue,
  stringValue,
} from "../../utils/jsonUtils";
import { formatDetailValue } from "./formatDetailValue";

export function approvalDetailsContent(
  args: Record<string, unknown>,
  result: unknown,
): ReactNode | null {
  const reason = stringValue(args.reason);
  const toolArgs = args.arguments;
  const debug = compactRecord({
    server_id: args.server_id,
    server_name: args.server_name,
    tool_name: args.tool_name,
    approval_id: args.approval_id,
  });
  const renderedResult =
    result !== undefined ? (
      <>
        <small>Decision</small>
        <pre>{formatToolValue(displayToolResult(result))}</pre>
      </>
    ) : null;
  if (!reason && toolArgs === undefined && !renderedResult && !debug) {
    return null;
  }
  return (
    <>
      {reason ? (
        <>
          <small>Reason</small>
          <p>{reason}</p>
        </>
      ) : null}
      {hasVisibleValue(toolArgs) ? (
        <>
          <small>Arguments</small>
          {formatDetailValue(toolArgs)}
        </>
      ) : null}
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
