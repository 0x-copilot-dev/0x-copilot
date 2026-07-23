// Web file-save helper (Generative Surfaces v2, PRD-B2 D7).
//
// The raw-fallback Download button hands the host the full serialized payload +
// a filename; the substrate-agnostic package never touches the filesystem, so
// the web host does the Blob + object-URL + anchor-click dance here (browser
// APIs are legal in app code, banned in `packages/chat-surface`). Best-effort:
// resolves once the download is triggered; rejects only if the DOM is missing.

export async function downloadTextFile(
  text: string,
  filename: string,
): Promise<void> {
  if (typeof document === "undefined") {
    throw new Error("download unavailable: no document");
  }
  const blob = new Blob([text], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    // Revoke on the next tick so the click has committed to the download.
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}
