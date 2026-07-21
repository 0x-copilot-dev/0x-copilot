// Curated local-model presets (FTUE P2 · SPEC "Download the local model").
//
// The single source of truth for the ONE curated on-device model the product
// ships as the first-run default: a real, pullable Hugging Face GGUF that runs
// through Ollama (`hf.co/{repo}:{quant}`). Both the FTUE gate card
// (`FirstRunLocalCard`) and the Settings download modal (`LocalModelsPage`
// `availableModels`) read this list, so the curated catalog never drifts
// between the two surfaces.
//
// Pure data — no substrate touchpoints. `sizeBytes` is the verified Hugging
// Face byte count for the chosen quant, used as the progress-bar denominator
// before the first live `bytes_total` frame arrives.

import type { AvailableLocalModel } from "./DownloadLocalModelModal";

/**
 * Qwen 3 4B — the curated first-run local model.
 *
 * `repo`/`quant` resolve to the Ollama pull tag `hf.co/Qwen/Qwen3-4B-GGUF:Q8_0`.
 *
 * Quant choice: `Q8_0` (near-lossless 8-bit) is the LARGEST real quant the
 * official `Qwen/Qwen3-4B-GGUF` repo publishes — the closest honest match to
 * the mock's "~5.6 GB" headline. Verified Hugging Face sizes for this repo:
 * Q4_K_M 2,497,280,256 · Q5_K_M 2,889,513,184 · Q6_K 3,306,260,704 ·
 * Q8_0 4,280,404,704. No standard Qwen3-4B GGUF quant is 5.6 GB, so the gate
 * card's verbatim "Qwen 3 4B · 5.6 GB · free forever" copy (owned by P1's
 * frozen `FIRST_RUN_COPY.local.meta`) overstates the real ~4.3 GB — a product
 * copy decision to reconcile (PRD-P2 §9 open question #1), NOT a code bug: the
 * live progress bar always uses the real `bytes_total` from the pull stream.
 */
export const QWEN3_4B_PRESET: AvailableLocalModel = {
  repo: "Qwen/Qwen3-4B-GGUF",
  quant: "Q8_0",
  name: "Qwen 3 4B",
  parameterSize: "4B",
  sizeBytes: 4_280_404_704, // verified HF Q8_0 byte count (~4.3 GB)
  note: "runs on this machine · free forever",
};

/** Curated download catalog — one real entry today (the FTUE default). */
export const LOCAL_MODEL_PRESETS: readonly AvailableLocalModel[] = [
  QWEN3_4B_PRESET,
];
