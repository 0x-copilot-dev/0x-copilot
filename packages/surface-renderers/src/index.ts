import { registerEmailAdapter } from "./email";

export {
  emailAdapter,
  registerEmailAdapter,
  type EmailDiff,
  type EmailDiffPending,
  type EmailState,
} from "./email";

export function registerAll(): void {
  registerEmailAdapter();
}
