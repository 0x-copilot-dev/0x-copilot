import {
  registerAdapter,
  type SaaSRendererAdapter,
} from "@0x-copilot/chat-surface";

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
