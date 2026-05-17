// Negative: bans `localStorage` global.
export function violation(): string | null {
  return localStorage.getItem("anything");
}
