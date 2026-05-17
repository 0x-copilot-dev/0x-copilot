// Negative: bans `WebSocket` global.
export function violation(): WebSocket {
  return new WebSocket("ws://example");
}
