// Negative: bans `sessionStorage` global.
export function violation(): string | null {
  return sessionStorage.getItem("anything");
}
