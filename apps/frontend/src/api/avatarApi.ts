import { assertOk, correlationHeaders } from "./http";

/**
 * Server-stored avatar pipeline (PR 8.3). Upload sends multipart;
 * delete clears the row + nulls ``user_profiles.avatar_url`` server-
 * side. The GET endpoint is rendered directly via ``<img src=…>``;
 * we don't fetch it from JS.
 */
export interface AvatarUploadResponse {
  avatar_url: string;
  etag: string;
  size_bytes: number;
}

export async function uploadMyAvatar(
  blob: Blob,
  filename = "avatar.jpg",
): Promise<AvatarUploadResponse> {
  const form = new FormData();
  form.append("file", blob, filename);
  const response = await fetch("/v1/me/avatar", {
    method: "POST",
    headers: correlationHeaders(),
    body: form,
  });
  await assertOk(response);
  return (await response.json()) as AvatarUploadResponse;
}

export async function deleteMyAvatar(): Promise<void> {
  const response = await fetch("/v1/me/avatar", {
    method: "DELETE",
    headers: correlationHeaders(),
  });
  await assertOk(response);
}
