// Web `ProviderKeysPort` for the FTUE gate's KeyForm — backed by the typed
// `api/providerKeysApi` module (the sanctioned frontend seam; features never
// touch the Transport singleton directly — see apps/frontend/eslint.config.js).
//
// The desktop binder builds this port from the shared `createProviderKeysPort`
// over its IpcTransport; the web host already has a typed api layer for these
// same facade routes, so it wraps THAT rather than reaching for the substrate
// transport (which is eslint-banned inside `features/**`). Same endpoints,
// same wire contract: the plaintext key appears exactly once, in the PUT body
// of `save()`, and every read carries only the masked `key_hint`.

import type {
  ProviderKeyProvider,
  ProviderKeySummary,
  PutProviderKeyRequest,
} from "@0x-copilot/api-types";
import type { ProviderKeysPort } from "@0x-copilot/chat-surface";

import {
  deleteProviderKey,
  listProviderKeys,
  putProviderKey,
} from "../../api/providerKeysApi";

/**
 * Build the web `ProviderKeysPort` over `api/providerKeysApi`. The FTUE
 * KeyForm only calls `save()`, but `list()` / `remove()` complete the port
 * contract so any consumer typed against `ProviderKeysPort` drops in. The
 * optional `validate` / `saveDefaultModel` seams are omitted — the KeyForm
 * falls back to the shared `checkFirstRunKeyFormat` pre-flight, and the model
 * pick is resolved later in the P3 composer, exactly as on desktop.
 */
export function createFirstRunProviderKeysPort(): ProviderKeysPort {
  return {
    async list(): Promise<readonly ProviderKeySummary[]> {
      const res = await listProviderKeys();
      return res.keys;
    },
    save(
      provider: string,
      apiKey: string,
      defaultModel?: string | null,
    ): Promise<ProviderKeySummary> {
      // Plaintext travels exactly once, in this PUT body. `default_model` is a
      // display-safe slug (never key material) persisted per-provider so the
      // summary can project it back; omit it to preserve the stored default.
      const request: PutProviderKeyRequest =
        defaultModel !== undefined &&
        defaultModel !== null &&
        defaultModel !== ""
          ? { api_key: apiKey, default_model: defaultModel }
          : { api_key: apiKey };
      return putProviderKey(provider as ProviderKeyProvider, request);
    },
    async remove(provider: string): Promise<void> {
      await deleteProviderKey(provider as ProviderKeyProvider);
    },
  };
}
