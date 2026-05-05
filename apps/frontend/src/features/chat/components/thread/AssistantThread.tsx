import type { ReactElement, ReactNode } from "react";

/**
 * Layout shell for the chat panel. Header is supplied by the controller
 * (`<Topbar />` from `components/shell/Topbar.tsx`) so this file owns no
 * topbar-prop fan-out — it just slots the chrome above and the thread
 * body below.
 */
export function AssistantThread({
  topbar,
  children,
}: {
  topbar: ReactNode;
  children: ReactNode;
}): ReactElement {
  return (
    <section className="aui-chat-panel">
      {topbar}
      <div className="not-prose aui-demo-frame">{children}</div>
    </section>
  );
}
