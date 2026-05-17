// Negative: bans `document` global.
export function violation(): string {
  return document.title;
}
