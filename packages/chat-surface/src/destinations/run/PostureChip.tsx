// Global write-posture chip (Generative Surfaces v2, PRD-C2 / FR-B5). 🎨
//
// A single always-visible chip that reflects the run's write posture: normally
// "Writes wait for you"; warning-styled "Bypass on · writes auto" the moment any
// connector's override is `allow_always`. Pure presentational — the host binder
// derives `bypassOn` from `GET /v1/mcp/servers` (C1's `write_policy` per row:
// "Bypass on" ⇔ any ENABLED server is `allow_always`), optimistically ORed with
// the ledger's `gate.resolved{write_policy}` so the chip flips instantly on the
// gate before the connectors refetch lands. Kit-only styling.

import type { ReactElement } from "react";

import { Badge } from "@0x-copilot/design-system";

export interface PostureChipProps {
  /** True when any enabled connector's write policy is `allow_always`. */
  readonly bypassOn: boolean;
}

const NORMAL_LABEL = "Writes wait for you";
const BYPASS_LABEL = "Bypass on · writes auto";

export function PostureChip({ bypassOn }: PostureChipProps): ReactElement {
  return (
    <Badge
      tone={bypassOn ? "warning" : "neutral"}
      data-testid="posture-chip"
      data-bypass={bypassOn ? "on" : "off"}
    >
      {bypassOn ? BYPASS_LABEL : NORMAL_LABEL}
    </Badge>
  );
}
