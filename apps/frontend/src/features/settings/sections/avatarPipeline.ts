/**
 * PR 8.3 — client-side avatar resize. Returns a 256×256 cover-cropped
 * JPEG `Blob` ready to multipart-upload to `/v1/me/avatar`. The server
 * caps the upload at 200 KB; with a JPEG quality of 0.9 we typically
 * land at 30–60 KB.
 *
 * Replaces the Phase-2 data-URL pipeline. The legacy `data:` URL
 * acceptor on the backend stays for rows already in the database, but
 * fresh uploads now go through the multipart route so the bytes never
 * round-trip via the profile JSON.
 */

const TARGET_SIZE = 256;
const JPEG_QUALITY = 0.9;
const ACCEPTED_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);
/** Hard ceiling on the original file size before we even attempt a resize. */
const MAX_INPUT_BYTES = 8 * 1024 * 1024;
/** Mirrors the server's ``_MAX_BYTES`` in ``me_avatar.py``. */
export const MAX_AVATAR_BLOB_BYTES = 200_000;

export class AvatarUploadError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AvatarUploadError";
  }
}

/**
 * Resize an image File to a 256×256 cover-cropped JPEG `Blob`.
 *
 * Returns the Blob + a dataURL preview. Callers can render the preview
 * immediately (so the user sees the new avatar before the network call)
 * and POST the Blob to ``/v1/me/avatar`` in the same handler.
 */
export interface AvatarPickResult {
  blob: Blob;
  /** `data:image/jpeg;base64,…` — for the immediate preview only. */
  previewDataUrl: string;
}

export async function fileToAvatarBlob(file: File): Promise<AvatarPickResult> {
  if (!ACCEPTED_TYPES.has(file.type)) {
    throw new AvatarUploadError(
      `Unsupported image type. Use PNG, JPEG, or WEBP.`,
    );
  }
  if (file.size > MAX_INPUT_BYTES) {
    throw new AvatarUploadError("Image is too large. Pick a file under 8 MB.");
  }

  const bitmap = await loadBitmap(file);
  try {
    const canvas = document.createElement("canvas");
    canvas.width = TARGET_SIZE;
    canvas.height = TARGET_SIZE;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      throw new AvatarUploadError("This browser cannot resize images.");
    }
    drawCover(ctx, bitmap, TARGET_SIZE);
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY),
    );
    if (!blob) {
      throw new AvatarUploadError("Couldn't encode the resized image.");
    }
    if (blob.size > MAX_AVATAR_BLOB_BYTES) {
      throw new AvatarUploadError(
        "Couldn't compress this image enough. Try a different photo.",
      );
    }
    const previewDataUrl = canvas.toDataURL("image/jpeg", JPEG_QUALITY);
    return { blob, previewDataUrl };
  } finally {
    bitmap.close?.();
  }
}

/**
 * `createImageBitmap` handles EXIF orientation in modern browsers and
 * works on the same File the input gave us — no Image() round-trip.
 */
async function loadBitmap(file: File): Promise<ImageBitmap> {
  if (typeof createImageBitmap === "function") {
    return createImageBitmap(file, { imageOrientation: "from-image" });
  }
  // Fallback: HTMLImageElement via blob URL. Used only on older browsers.
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      resolve(img as any);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new AvatarUploadError("Could not read this image."));
    };
    img.src = url;
  });
}

/**
 * Cover-crop draw — image fills the square, longer axis is clipped.
 */
function drawCover(
  ctx: CanvasRenderingContext2D,
  source: ImageBitmap,
  size: number,
): void {
  const sourceShorter = Math.min(source.width, source.height);
  const sx = (source.width - sourceShorter) / 2;
  const sy = (source.height - sourceShorter) / 2;
  ctx.drawImage(source, sx, sy, sourceShorter, sourceShorter, 0, 0, size, size);
}
