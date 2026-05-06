/**
 * Base64-url <-> ArrayBuffer helpers for the WebAuthn enrollment
 * ceremony (PR 8.3).
 *
 * The WebAuthn spec uses base64url (URL-safe, no padding) for binary
 * fields on the wire. The browser API takes / returns `ArrayBuffer`. We
 * keep the helpers in their own module so the round-trip is testable
 * without React + DOM mocks.
 */

export function base64UrlToBytes(input: string): Uint8Array {
  const padded = input.replace(/-/g, "+").replace(/_/g, "/");
  const padding = padded.length % 4 === 0 ? 0 : 4 - (padded.length % 4);
  const b64 = padded + "=".repeat(padding);
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) {
    out[i] = bin.charCodeAt(i);
  }
  return out;
}

export function bytesToBase64Url(buffer: ArrayBuffer | Uint8Array): string {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 1) {
    bin += String.fromCharCode(bytes[i]);
  }
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * Decode every base64url field on a `PublicKeyCredentialCreationOptionsJSON`
 * into the `BufferSource`s the navigator expects. Mirrors the WebAuthn
 * level-2 IDL (just the fields we use today).
 */
export function decodeCreationOptions(
  options: Record<string, unknown>,
): PublicKeyCredentialCreationOptions {
  const cloned: Record<string, unknown> = { ...options };
  cloned.challenge = base64UrlToBytes(options.challenge as string);
  const user = options.user as Record<string, unknown> | undefined;
  if (user && typeof user.id === "string") {
    cloned.user = { ...user, id: base64UrlToBytes(user.id) };
  }
  const exclude = options.excludeCredentials as
    | Array<Record<string, unknown>>
    | undefined;
  if (Array.isArray(exclude)) {
    cloned.excludeCredentials = exclude.map((c) => ({
      ...c,
      id: base64UrlToBytes(c.id as string),
    }));
  }
  return cloned as unknown as PublicKeyCredentialCreationOptions;
}

/**
 * Pull the bits the backend's `webauthn_register_finish` expects out of
 * the navigator's `PublicKeyCredential`. Re-encodes binary fields to
 * base64url so the JSON round-trip is lossless.
 */
export function encodeAttestation(
  credential: PublicKeyCredential,
): Record<string, unknown> {
  const response = credential.response as AuthenticatorAttestationResponse;
  return {
    id: credential.id,
    rawId: bytesToBase64Url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bytesToBase64Url(response.clientDataJSON),
      attestationObject: bytesToBase64Url(response.attestationObject),
    },
    authenticatorAttachment:
      "authenticatorAttachment" in credential
        ? (credential as { authenticatorAttachment?: string })
            .authenticatorAttachment
        : undefined,
  };
}
