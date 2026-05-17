import { registerSurface } from "@enterprise-search/chat-surface";

import { EmailRenderer } from "./EmailRenderer";

export { EmailRenderer } from "./EmailRenderer";
export {
  EmailDiffOverlay,
  type EmailDiffOverlayProps,
} from "./EmailDiffOverlay";

export function registerEmailSurface(): void {
  registerSurface("email", EmailRenderer);
}
