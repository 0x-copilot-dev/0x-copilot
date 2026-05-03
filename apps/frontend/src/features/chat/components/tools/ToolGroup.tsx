import type { ReactElement, ReactNode } from "react";

export function ToolGroup({
  children,
}: {
  startIndex: number;
  endIndex: number;
  children?: ReactNode;
}): ReactElement {
  return <>{children}</>;
}
