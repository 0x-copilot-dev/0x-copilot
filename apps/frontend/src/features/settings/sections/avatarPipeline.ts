/**
 * Phase 2 (PR 8.2) — avatar upload helper.
 *
 * The repo doesn't ship object storage today and adding S3-isms purely
 * for an avatar slot is premature. Instead we resize the user's pick to
 * 256×256 in a canvas and store the result as a `data:image/jpeg`
 * base64 URL inline in the existing `user_profiles.avatar_url` column.
 *
 * Server-side validators (`me_profile.py::_validate_avatar`) refuse
 * any blob over 200 KB and any content-type outside the PNG/JPEG/WEBP
 * allowlist, so the contract stays narrow even though the column type
 * is permissive. Swapping to S3 later means changing what gets stored
 * here — the contract and the FE rendering don't move.
 */

const TARGET_SIZE = 256;
const JPEG_QUALITY = 0.9;
const ACCEPTED_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);
/** Hard ceiling on the original file size before we even attempt a resize. */
const MAX_INPUT_BYTES = 8 * 1024 * 1024;
/** Mirrors `me_profile.py::_AVATAR_DATA_URL_MAX_LEN`. */
export const MAX_AVATAR_DATA_URL_LEN = 200_000;

export class AvatarUploadError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AvatarUploadError";
  }
}

/**
 * Resize an image File to a 256×256 cover-cropped JPEG and return the
 * `data:image/jpeg;base64,…` form ready to write to `avatar_url`.
 */
export async function fileToAvatarDataUrl(file: File): Promise<string> {
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
    const dataUrl = canvas.toDataURL("image/jpeg", JPEG_QUALITY);
    if (dataUrl.length > MAX_AVATAR_DATA_URL_LEN) {
      throw new AvatarUploadError(
        "Couldn't compress this image enough. Try a different photo.",
      );
    }
    return dataUrl;
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
