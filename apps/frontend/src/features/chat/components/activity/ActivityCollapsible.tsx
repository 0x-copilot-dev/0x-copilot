import { classNames } from "@0x-copilot/design-system";
import type { ReactElement, ReactNode } from "react";

export function ActivityCollapsible({
  label,
  children,
  className,
  contentClassName,
}: {
  label: string;
  children: ReactNode;
  className?: string;
  contentClassName?: string;
}): ReactElement {
  return (
    <details className={classNames("aui-collapsible", className)}>
      <summary className="aui-collapsible__trigger">{label}</summary>
      <div className={classNames("aui-collapsible__content", contentClassName)}>
        {children}
      </div>
    </details>
  );
}
