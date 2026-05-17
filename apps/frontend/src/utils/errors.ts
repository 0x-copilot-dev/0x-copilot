/**
 * Single canonical helper for turning a caught `unknown` into a
 * user-visible string. See `docs/architecture/prds/01-error-message-utility.md`.
 *
 * Returns the error's message when the value is an `Error` and its
 * message is non-empty after trimming; otherwise returns the supplied
 * fallback. The fallback is required so call sites keep their domain
 * context ("Could not load policy" vs "Could not save policy") — there
 * is no project-wide "Something went wrong" default.
 *
 * Frontend code should not inline `err instanceof Error ? err.message : ...`
 * — import this instead. Lint rule pending.
 */
export function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error) {
    const trimmed = err.message?.trim();
    if (trimmed) return trimmed;
  }
  return fallback;
}
