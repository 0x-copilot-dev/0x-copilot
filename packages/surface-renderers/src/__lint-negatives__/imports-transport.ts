// Negative: bans `@0x-copilot/chat-transport` import (D28).
import type { Transport } from "@0x-copilot/chat-transport";

export function violation(): Transport | null {
  return null;
}
