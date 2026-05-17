// Negative: bans `fetch` global.
export async function violation(): Promise<unknown> {
  return fetch("/anything");
}
