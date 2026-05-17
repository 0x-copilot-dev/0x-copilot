// Negative: bans `@enterprise-search/chat-transport` import (D28).
import type { Transport } from "@enterprise-search/chat-transport";

export function violation(): Transport | null {
  return null;
}
