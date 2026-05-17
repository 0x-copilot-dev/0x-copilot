import { registerEmailSurface } from "./email";

export {
  EmailRenderer,
  EmailDiffOverlay,
  type EmailDiffOverlayProps,
} from "./email";

export function registerAll(): void {
  registerEmailSurface();
}
