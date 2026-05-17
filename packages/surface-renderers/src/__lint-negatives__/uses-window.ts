// Negative: bans `window` global.
export function violation(): number {
  return window.innerWidth;
}
