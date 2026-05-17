// Negative: bans dynamic import() with a non-literal specifier — the
// allowlist only matches string literals, so anything dynamic must fail.
export async function violation(spec: string): Promise<unknown> {
  return import(spec);
}
