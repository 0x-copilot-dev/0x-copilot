import type { ReactNode } from "react";
import {
  displayToolResult,
  formatToolValue,
  largeToolResultFromValue,
  parseJsonValue,
} from "../../utils/jsonUtils";
import { shouldShowToolDetails } from "../../utils/toolResultAnalysis";

export function toolDetailsContent(
  argsText: string | undefined,
  result: unknown,
): ReactNode | null {
  if (!shouldShowToolDetails(argsText, result)) {
    return null;
  }
  return (
    <>
      {argsText ? (
        <>
          <small>Input</small>
          <pre>{formatToolValue(parseJsonValue(argsText) ?? argsText)}</pre>
        </>
      ) : null}
      {result !== undefined && !largeToolResultFromValue(result) ? (
        <>
          <small>Result</small>
          <pre>{formatToolValue(displayToolResult(result))}</pre>
        </>
      ) : null}
    </>
  );
}
