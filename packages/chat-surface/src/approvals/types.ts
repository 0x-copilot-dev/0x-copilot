// Approval-family value types (PR-1.6).
//
// `ActivityParam` is the inset key/value row shape rendered by
// `ActivityParams` inside the approval card. It moved down here from
// `apps/frontend/.../utils/activityDataBuilders.ts` so `chat-surface` owns
// the shape without importing the host (FR-1.21); `activityDataBuilders`
// now re-exports this type, keeping every host import site resolving
// unchanged.

import type { ReactNode } from "react";

export type ActivityParam = {
  label: string;
  value: ReactNode;
  /** Render the value in a full-width block row rather than inline. */
  block?: boolean;
};
