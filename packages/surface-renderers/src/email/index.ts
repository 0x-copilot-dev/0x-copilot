import {
  registerAdapter,
  type SaaSRendererAdapter,
} from "@enterprise-search/chat-surface";

import { emailAdapter } from "./EmailRenderer";

export {
  emailAdapter,
  type EmailDiff,
  type EmailDiffPending,
  type EmailState,
} from "./EmailRenderer";

export function registerEmailAdapter(): void {
  registerAdapter(emailAdapter as SaaSRendererAdapter);
}
