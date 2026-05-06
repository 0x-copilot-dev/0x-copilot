/**
 * Atlas-owned `accept`-string matcher. Mirrors the behavior of the
 * matcher we previously inherited from `@assistant-ui/core`. Pure;
 * no runtime side effects.
 *
 * `accept` is a comma-separated list of:
 *   - `*` (wildcard, matches everything)
 *   - file extensions like `.pdf`
 *   - exact MIME types like `text/plain`
 *   - generic MIME prefixes like `image/*`
 */
export type FileLike = { name: string; type: string };

export function fileMatchesAccept(file: FileLike, accept: string): boolean {
  if (accept === "*") {
    return true;
  }
  const allowedTypes = accept
    .split(",")
    .map((entry) => entry.trim().toLowerCase());
  const extensionPart = file.name.split(".").pop()?.toLowerCase() ?? "";
  const fileExtension = extensionPart ? `.${extensionPart}` : "";
  const fileMimeType = file.type.toLowerCase();
  for (const type of allowedTypes) {
    if (type.startsWith(".") && type === fileExtension) {
      return true;
    }
    if (type.includes("/") && type === fileMimeType) {
      return true;
    }
    if (type.endsWith("/*")) {
      const generalType = type.split("/")[0];
      if (fileMimeType.startsWith(`${generalType}/`)) {
        return true;
      }
    }
  }
  return false;
}
