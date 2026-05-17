// Negative: bans `XMLHttpRequest` global.
export function violation(): XMLHttpRequest {
  return new XMLHttpRequest();
}
