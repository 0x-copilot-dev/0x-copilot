// Negative: bans dynamic import() of non-allowlisted modules.
export async function violation(): Promise<unknown> {
  return import("some-arbitrary-module");
}
