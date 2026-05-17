// Negative: bans `EventSource` global.
export function violation(): EventSource {
  return new EventSource("/events");
}
