# AC8 — Agentic browser

| Field             | Decision                                                                                               |
| ----------------- | ------------------------------------------------------------------------------------------------------ |
| Spec ID           | AC8                                                                                                    |
| Status            | Draft; decision-complete and awaiting architecture review                                              |
| Wave              | 3 — Execution capabilities                                                                             |
| Estimated effort  | XL — 20–25 engineer-days including process supervision, policy proxy, packaging, and adversarial tests |
| Dependencies      | AC2 event model, AC4 artifact store, AC5 scoped host filesystem access                                 |
| Required for      | AC10 hardening and staged desktop rollout                                                              |
| Primary owner     | `apps/desktop` browser runtime                                                                         |
| Supporting owners | AI-backend MCP integration, desktop packaging, security                                                |
| Web impact        | None                                                                                                   |

## Problem and why now

Some useful desktop workflows have no stable API or connector and require interaction with a web application. A browser can read rendered content, complete forms, download reports, and operate sites that are otherwise unavailable to the agent. It is also a high-risk capability: pages can inject instructions, browser sessions contain cookies, navigation can reach local services, clicks can create irreversible external effects, and downloaded content can attack the host.

The repository has Playwright as a root development dependency for tests and website capture, but it has no agent browser process, local browser MCP provider, profile isolation, domain policy, or browser action events. Reusing a test browser or calling Playwright from Electron main would turn a convenience dependency into an ambient privileged executor.

AC8 adds a desktop-only, supervised Playwright child and a product-owned MCP tool surface. The child owns browser automation. Electron main owns process lifecycle and the privileged policy boundary. The renderer remains unprivileged. The AI worker receives typed MCP tools and no Playwright, CDP, JavaScript-evaluation, browser-profile filesystem, or arbitrary URL escape hatch.

## Capability priority

The runtime must choose the least fragile and least privileged lane:

1. **Backend API/MCP connector** when one exists and has the required operation.
2. **AC8 browser** when a connector cannot perform the workflow and the site is in policy.
3. **Computer use** only under a future, separately reviewed PRD.

The browser is not a way to bypass connector scope, OAuth, tool approval, or quota controls. AC9 connector availability is surfaced before the browser card for the same product.

## Goals

- Run Playwright only in a separately supervised Node child with its own bundled Chromium.
- Expose a local, desktop-only MCP provider with a small typed action set.
- Use one isolated browser profile per workspace/profile id and ephemeral contexts for one-off work.
- Make the default context ephemeral; persistent profile creation requires explicit consent.
- Enforce exact-origin HTTPS policy on navigation, subresources, redirects, popups, frames, fetch/XHR, WebSockets, and downloads.
- Deny localhost, private/link-local/reserved addresses, cloud metadata, unsafe schemes, extension pages, local app ports, and DNS rebinding.
- Keep page content, accessibility snapshots, selectors, cookies, credentials, and typed secrets out of application logs and normal transcripts.
- Store screenshots, large snapshots, traces when explicitly enabled, and downloads in AC4 by reference.
- Require interactive consent for profile use and risk-appropriate approval for side-effecting actions.
- Let the user take over the isolated browser for login, password, passkey, CAPTCHA, and MFA entry without exposing those values to the model.
- Package and test the same pinned browser runtime on macOS arm64/x64 and Windows x64.
- Kill browser descendants and clean staging data on cancel, crash, profile deletion, app shutdown, and upgrade.

## Non-goals

- Running Playwright in the renderer, preload, Electron main thread, backend, or AI worker.
- Connecting to the user’s normal Chrome/Edge profile, installed extensions, open tabs, cookies, history, or password manager.
- Generic `evaluate`, `eval`, `run_code`, CDP, DevTools, extension install, filesystem, shell, or arbitrary Playwright methods.
- Invisible credential entry by the model.
- Circumventing CAPTCHA, bot protection, access controls, robots policy, terms of service, or site rate limits.
- Remote browser-as-a-service; AC8 is the local desktop browser lane.
- Treating Chromium’s process sandbox as equivalent to a VM or kernel-isolated execution environment.
- Full visual computer use or coordinate-only control.
- Persisting raw cookies or storage state in chat, AC4, audit, export, backup, or backend `TokenVault`.
- Automatically opening or executing downloaded content.

## User experience and failure behavior

### Session start

1. The agent proposes **Use browser** and explains why a connector is insufficient.
2. The consent sheet shows:
   - workspace and selected browser profile;
   - ephemeral or persistent mode;
   - exact origins requested;
   - expected read/write action class;
   - download/upload intent;
   - session time and action limits.
3. The user can remove origins, switch to ephemeral, choose a different profile, or deny.
4. Electron main binds the approved envelope to the run, workspace, profile, and local MCP token.
5. A visible Playwright-managed Chromium window opens with an automation indicator. It is not an Electron `BrowserWindow` and does not share Electron’s session.

### Interaction

- Read-only navigation and inspection proceed within the approved exact origins.
- The agent acts on opaque, generation-bound element references from a bounded accessibility snapshot. It cannot provide raw CSS/XPath or coordinates.
- The UI shows current origin, action, profile, automation state, and a **Stop** control.
- A domain boundary pauses before navigation. The user may add that exact origin for the remaining session or deny it.
- A form submit, send/post, delete, purchase, financial action, permission change, file upload, sensitive-data entry, or ambiguous button pauses with a summary of visible labels and destination origin.
- Login and secret fields always enter **Take over** mode. Browser tools are disabled while the user controls the page. The agent resumes only after the user explicitly returns control.

### Failure behavior

- If the desktop feature, worker, bundled browser, profile policy, or broker is unavailable, the local MCP card is absent. There is no renderer, Electron-main, system-browser, LangChain-toolkit, or host-shell fallback.
- Worker startup/health failure reports `browser_unavailable`; the rest of the app remains usable.
- A blocked scheme, origin, redirect, DNS result, private address, popup, or socket reports `browser_network_denied` with a safe origin-level reason.
- Stale element references return `browser_element_stale`; the model must take a fresh snapshot rather than guessing a selector.
- A worker or browser crash during a read action may restart a fresh ephemeral context after renewed consent. A crash during a side-effecting action returns `browser_action_outcome_unknown`; it is never automatically retried.
- Timeouts do not imply that a side effect failed. The UI asks the user to inspect the site before retrying.
- An oversized snapshot is truncated and offloaded. An oversized download is cancelled and its partial staging file is deleted.
- Downloads are never opened. Exporting one to a user root is a separate AC5 write with review.
- Cancellation stops new actions, closes contexts, terminates the process tree, and records whether cleanup completed.

## Alternatives considered

### Playwright in Electron main

Rejected. Electron main already owns windows, authentication, IPC, updates, and service supervision. Loading untrusted pages and automation code there would enlarge the most privileged failure domain and make a browser crash an app crash.

### Playwright in the renderer or a `<webview>`

Rejected. The renderer is untrusted and intentionally has no Node or host authority. A `<webview>` used by a presentation adapter is not an agent execution boundary and must not receive browser MCP credentials.

### Direct use of Microsoft Playwright MCP

Useful reference, not the production boundary. It demonstrates accessibility snapshots and MCP tools, but its own documentation states that Playwright MCP is not a security boundary. AC8 needs product-specific origin enforcement, run/workspace/profile binding, approval classes, artifact offload, local broker authentication, and audit.

### LangChain Playwright toolkit

Useful prototype/reference only. Its tools accept a URL, CSS selectors, extraction calls, and browser operations, but do not define 0xCopilot’s origin, profile, consent, artifact, or local-process contract. Wrapping `navigate_browser` with a prompt is insufficient.

### Connect to the user’s existing browser

Rejected. It would expose unrelated tabs, cookies, extensions, password managers, and personal browsing state, and would make workspace isolation unverifiable.

### Screenshot/coordinate computer use

Deferred. Accessibility references are more deterministic and permit semantic policy checks. Coordinate-only actions are a separate capability with a larger spoofing and approval problem.

### Remote browser provider

Deferred. Remote browsers require a separate data-residency, cookie-transfer, egress, and provider-deletion review. AC7’s execution sandbox does not implicitly gain browser profiles.

## Architecture and ownership

### Process topology

```text
runtime_worker
  -> DesktopBrowserMcpProvider (desktop-only)
  -> authenticated AC5 desktop capability broker route
  -> Electron-main browser policy/controller
  -> authenticated private loopback channel
  -> supervised browser-worker Node child
  -> pinned Playwright + bundled Chromium process tree
  -> local policy egress proxy
```

- **Runtime worker:** loads a compact local MCP card and calls typed tools through the normal MCP loader, permission, approval, budget, citation, payload, event, and audit middleware.
- **Electron main:** owns process spawn, short-lived audience tokens, profile catalog, workspace binding, consent state, origin policy, health, restart, and cleanup. It never imports Playwright and never executes a browser action.
- **Browser worker:** owns Playwright objects, accessibility snapshots, element-reference generation, action preflight, download staging, screenshot capture, and defense-in-depth network checks. It has no Electron APIs, facade/backend bearer, connector token, AC5 root, keychain API, or arbitrary host filesystem.
- **Policy proxy:** resolves and connects network destinations under the approved policy. Chromium has no proxy bypass and service workers are disabled.
- **Renderer:** renders activity and approval state through existing APIs. It receives no local broker URL/token, profile path, cookie, Playwright object, or page body.

The child runs via the packaged Electron executable with `ELECTRON_RUN_AS_NODE=1` and the compiled `out/browser-worker/index.js`. It has a minimal environment and app-owned working directory. Playwright Chromium is staged per OS/architecture in desktop resources; the app does not use the machine’s installed browser.

### Local MCP provider

The existing `DynamicMcpRegistry` already accepts multiple `McpServerProvider` instances. In the desktop profile, runtime dependencies append one `DesktopBrowserMcpProvider` to the backend provider:

- stable server name `desktop_browser`;
- no backend registry row and no OAuth state;
- one MCP client per run;
- broker URL and bootstrap credential from trusted desktop service environment;
- card absent outside `single_user_desktop` or when disabled/unhealthy;
- tool schemas discovered from the browser worker through the authenticated main broker.

This is the narrow exception to backend-owned **SaaS** MCP transport. AC9 SaaS connectors continue through backend internal cards/client-session/RPC proxy. The browser provider is device-local and cannot be registered as a remote connector.

### Browser contexts and profiles

- Default is a fresh non-persistent `browser.newContext()` whose storage is discarded at close.
- A persistent profile is created only by an explicit UI action. The worker uses `launchPersistentContext()` against an app-owned profile directory.
- Profile ids are random opaque ids. Paths derive from ids, not workspace names.
- Every profile manifest binds exactly one workspace id, browser version, creation time, last-used time, and policy version.
- Profiles never share `userDataDir`, cookies, cache, storage, permissions, service workers, downloads, or open pages.
- One profile may have at most one active automation lease. A second run queues or asks the user to use an ephemeral context.
- Persistent profiles use mode `0700` directories/`0600` files on macOS and current-user-only ACLs on Windows.
- Profiles are secret-bearing local state. They are excluded from chat artifacts, diagnostics, normal export/backup, remote sandbox snapshots, and file search.
- Deleting a profile closes it, kills descendants, and removes the directory. This is best-effort deletion, not cryptographic erase; OS backups/snapshots remain a deployment concern.

### Interactive login

The browser may navigate through a user-approved identity-provider origin in manual mode. During takeover:

- all model browser tools return `browser_takeover_active`;
- snapshots and screenshots stop;
- key and pointer input are not recorded by 0xCopilot;
- password/passkey/MFA fields are never serialized;
- browser storage remains only in the selected profile;
- the user explicitly selects **Return control to agent** on an approved origin.

Connector OAuth remains AC9/backend-owned and uses the system browser. Browser-profile login is only for web automation; cookies are never converted into connector tokens.

## Network and domain policy

### Origin model

- Policy entries are canonical exact origins: `https://<punycode-host>:443`.
- HTTP is denied except a test-only fixture profile that cannot ship.
- Wildcards, suffix matches, raw IP literals, user-info URLs, non-default ports, opaque origins, and inherited “same site” trust are denied in AC8.
- A newly encountered exact origin requires explicit user consent. Embedded subresources may use a separately admin-approved static asset origin set, but do not grant top-level navigation or tool execution there.
- Manual user navigation outside the approved set is allowed only during takeover. Agent tools remain disabled until the page returns to an approved origin.

### Schemes and destinations denied

- `file:`, `data:`, `blob:` as top-level targets, `javascript:`, `about:` except controlled blank pages, `chrome:`, `chrome-extension:`, `devtools:`, `view-source:`, `ftp:`, custom protocols, and external-app launches.
- IPv4/IPv6 loopback, private, link-local, multicast, unspecified, carrier-grade NAT, benchmarking/documentation/reserved ranges, and IPv4-mapped IPv6 forms.
- Cloud metadata and platform identity endpoints, including hostname aliases.
- `.local`, single-label names, local search domains, Electron broker/service ports, and desktop app protocol handlers.

### Enforcement

- The worker starts Chromium with the product-owned loopback policy proxy and no bypass list.
- The proxy parses canonical host/port, resolves DNS itself, rejects every resolved address against the denied-range table, pins a permitted address for that connection, and revalidates on reconnect.
- Every redirect and popup is independently authorized.
- Browser-context routing provides defense in depth and blocks unapproved request/resource types before dispatch.
- Service workers are set to `block` because Playwright documents that they can hide requests from context routing.
- QUIC is disabled; WebRTC local-address exposure and direct UDP are disabled; WebSockets traverse the proxy.
- Browser extensions, external protocols, background sync, notification permissions, geolocation, camera, microphone, clipboard read, USB, serial, Bluetooth, and filesystem APIs are denied by context policy.
- CSP or page code cannot expand policy. Prompt/page text can request an origin, but only the user/admin policy can grant it.

The proxy materially reduces SSRF and DNS-rebinding risk but is not a kernel network sandbox. A Chromium native-code compromise remains a residual risk. Security documentation must not describe AC8 as Cowork-style VM isolation.

## Typed tools and contracts

### MCP tool set

| Tool                 | Input                                 | Output                                      | Default risk                                |
| -------------------- | ------------------------------------- | ------------------------------------------- | ------------------------------------------- |
| `browser_navigate`   | Approved HTTPS URL                    | origin, title, status, snapshot ref         | Medium; origin expansion interrupts         |
| `browser_snapshot`   | depth and optional target ref         | bounded accessibility tree + artifact ref   | Read                                        |
| `browser_click`      | generation-bound element ref          | observed navigation/download/action summary | Medium; submit/ambiguous controls interrupt |
| `browser_type`       | element ref and non-secret text       | redacted completion metadata                | Medium; sensitive fields force takeover     |
| `browser_select`     | element ref and declared option       | completion metadata                         | Medium                                      |
| `browser_submit`     | form/button ref and reviewed fields   | outcome/unknown marker                      | High; always interrupts                     |
| `browser_wait`       | bounded condition and timeout         | condition status                            | Read                                        |
| `browser_screenshot` | viewport/full-page and redaction mode | AC4 image ref                               | Medium                                      |
| `browser_download`   | initiating element ref                | AC4 artifact metadata                       | High; executable-like content denied        |
| `browser_upload`     | element ref and AC5 object ref        | filename/size/destination summary           | High; always interrupts                     |
| `browser_close`      | none                                  | cleanup state                               | Read                                        |

There is no generic Playwright, JavaScript, selector, coordinate, console, network-body, cookie, storage-state, or CDP tool.

### Shared desktop protocol

`apps/desktop/browser/protocol.ts` is the TypeScript source of truth. Zod schemas derive runtime validation and MCP JSON Schemas; the AI backend consumes `tools/list` and does not hand-copy these types.

```ts
type BrowserProfileMode = "ephemeral" | "persistent";
type BrowserActionClass =
  | "read"
  | "navigate"
  | "input"
  | "submit"
  | "upload"
  | "download"
  | "external_effect";

interface BrowserOriginPolicy {
  version: 1;
  topLevelOrigins: readonly string[];
  subresourceOrigins: readonly string[];
  denyPrivateNetworks: true;
  serviceWorkers: "block";
}

interface BrowserRunBinding {
  version: 1;
  runId: string;
  workspaceId: string;
  profileId: string;
  profileMode: BrowserProfileMode;
  approvalId: string;
  originPolicy: BrowserOriginPolicy;
  expiresAt: string;
  nonce: string;
}

interface BrowserElementRef {
  sessionId: string;
  pageId: string;
  generation: number;
  ref: string;
  role: string;
  redactedName: string;
}

interface BrowserActionRequest {
  version: 1;
  requestId: string;
  binding: BrowserRunBinding;
  actionClass: BrowserActionClass;
  toolName: string;
  arguments: unknown;
  deadlineMs: number;
}

interface BrowserActionResult {
  version: 1;
  requestId: string;
  sessionId: string;
  actionId: string;
  status: "succeeded" | "denied" | "failed" | "cancelled" | "outcome_unknown";
  currentOrigin?: string;
  safeSummary: string;
  artifactRefs: readonly string[];
  nextGeneration?: number;
  errorCode?: BrowserErrorCode;
}
```

### Stable errors

- `browser_disabled`
- `browser_unavailable`
- `browser_profile_busy`
- `browser_profile_version_mismatch`
- `browser_consent_required`
- `browser_takeover_active`
- `browser_origin_approval_required`
- `browser_network_denied`
- `browser_element_stale`
- `browser_sensitive_input_required`
- `browser_action_approval_required`
- `browser_action_timeout`
- `browser_action_outcome_unknown`
- `browser_download_denied`
- `browser_artifact_quota_exceeded`
- `browser_cancelled`
- `browser_cleanup_pending`

### Broker authentication

- Electron main creates a random browser-worker credential and a separate AI-worker browser-broker bootstrap credential at boot.
- Requests bind `aud=desktop-browser-broker`, run id, workspace id, profile id, policy hash, nonce, expiry, and request id.
- Action credentials expire after five minutes and are rotated; the binding expires with the session.
- The worker credential is never passed to the AI worker. The AI credential is never passed to the renderer or browser worker.
- Both loopback servers reject non-loopback peers, missing/duplicate auth headers, stale timestamp, replayed nonce/request id, wrong audience, wrong run/profile, CORS requests, and non-JSON content types.
- Local bearer values are redacted from environment dumps, logs, crashes, diagnostics, events, and artifacts.

## Action policy and approvals

| Action                                                             | Policy                                                                                                                 |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| Navigate/read within approved origin set                           | Session consent; bounded automatic actions                                                                             |
| Add exact origin                                                   | User approval before any request                                                                                       |
| Open popup/new tab                                                 | Same-origin allowed; new origin pauses                                                                                 |
| Type ordinary non-secret text                                      | Allowed only in reviewed non-sensitive field; value remains a normal sensitive tool argument and is redacted from logs |
| Password, passkey, MFA, CAPTCHA, payment card, recovery code       | Mandatory user takeover; model cannot provide value                                                                    |
| Submit/send/post/delete/purchase/financial/admin/permission action | Per-action approval with visible target and origin                                                                     |
| Ambiguous button or form                                           | Treat as side effect and interrupt                                                                                     |
| Upload local file                                                  | AC5 object grant plus per-file/per-origin approval                                                                     |
| Download                                                           | Per-action approval; stage, scan/type-check, never open                                                                |
| Clipboard/camera/microphone/geolocation/notifications              | Denied in AC8                                                                                                          |

Approval occurs before the worker dispatches the action. A post-action event records observed outcome but is not a substitute for approval.

## Snapshot, screenshot, download, and upload handling

- Accessibility snapshots are the primary perception format.
- Input values, password fields, hidden fields, cookies, authorization headers, page scripts, and browser storage are omitted.
- Snapshot output is depth/node/byte bounded. Default inline preview is 32 KiB and hard maximum 128 KiB; overflow is AC4-only.
- Element refs are random session-local handles tied to page generation. Any navigation or material DOM change invalidates the prior generation.
- Screenshots default to masking detected input fields and configured sensitive regions. The approval preview warns that page images may still contain sensitive data.
- Default screenshot ceiling is 16 megapixels and 10 MiB.
- Downloads land in a per-run staging directory outside the profile, with generated filenames. The site-suggested name is metadata only and is sanitized.
- Default download ceiling is 100 MiB; hard ceiling is 512 MiB. MIME, magic bytes, extension, size, and SHA-256 are recorded.
- Executables, installers, scripts, shortcuts, disk images, archives containing links/devices/path traversal, and macro-enabled office documents are denied by default.
- Accepted bytes move to AC4, then the staging file is removed. Nothing auto-opens or executes.
- Export to the host uses AC5 and applies macOS quarantine metadata or Windows Mark-of-the-Web where supported.
- Upload takes an AC5 object ref, not a model-supplied host path. The broker revalidates read grant and hash before streaming only the approved file to the worker.

## Critical current and proposed files

### Current evidence and integration points

- `package.json` and `package-lock.json` — Playwright is pinned at the workspace root for development use.
- `apps/website/scripts/capture.mjs` and `film.mjs` — current non-agent Playwright usage.
- `apps/desktop/main/services/supervisor.ts` — current three-service lifecycle.
- `apps/desktop/main/services/desktop-supervisor.ts` — sole existing OS-facing service composition and child spawn.
- `apps/desktop/main/services/python-service.ts` — crash-loop, backoff, log, and kill-escalation precedent.
- `apps/desktop/main/services/service-env.ts` — trusted desktop-profile environment.
- `apps/desktop/main/ipc/schemas.ts`, `handlers.ts`, and `preload/bridge.ts` — allowlisted renderer IPC boundary; browser control must not be added there.
- `services/ai-backend/src/agent_runtime/capabilities/mcp/registry.py` — multi-provider MCP registry.
- `services/ai-backend/src/agent_runtime/capabilities/mcp/backend_provider.py` — backend SaaS MCP provider; not reused for local browser calls.
- `services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py` — existing tool policy/citation/invocation path.
- `services/ai-backend/src/runtime_worker/dependencies.py` — desktop-only provider wiring point.
- `docs/architecture/desktop-app.md` — accepted supervised browser trust boundary.

A repository search on 2026-07-18 found no agent browser worker, browser MCP provider, profile store, origin policy, or browser event implementation. Test/website Playwright and model-pricing references do not satisfy AC8.

### Proposed implementation files

- `apps/desktop/browser/protocol.ts`
- `apps/desktop/browser-worker/index.ts`
- `apps/desktop/browser-worker/mcp-server.ts`
- `apps/desktop/browser-worker/browser-session.ts`
- `apps/desktop/browser-worker/element-refs.ts`
- `apps/desktop/browser-worker/network-policy-proxy.ts`
- `apps/desktop/browser-worker/downloads.ts`
- `apps/desktop/browser-worker/redaction.ts`
- `apps/desktop/main/browser/browser-supervisor.ts`
- `apps/desktop/main/browser/browser-broker.ts`
- `apps/desktop/main/browser/profile-store.ts`
- `apps/desktop/main/browser/consent-policy.ts`
- `apps/desktop/main/browser/orphan-cleanup.ts`
- `services/ai-backend/src/agent_runtime/capabilities/mcp/desktop_browser_provider.py`
- `services/ai-backend/tests/integration/capabilities/mcp/test_desktop_browser_provider.py`
- `apps/desktop/tests/browser/policy-proxy.test.ts`
- `apps/desktop/tests/browser/browser-worker.e2e.test.ts`
- `apps/desktop/docs/agent-browser.md`
- `tools/desktop-runtime/stage.mjs` — stage the pinned Playwright browser per target.

## Security and threat model

| Threat                        | Control                                                                                                   | Required evidence                   |
| ----------------------------- | --------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| Page prompt injection         | Treat page text as data; immutable environmental policy; no tool/page can grant origin, files, or secrets | Adversarial page suite              |
| SSRF/local service access     | Exact origins, policy proxy DNS validation/pinning, denied address ranges and local ports                 | IPv4/IPv6/redirect/rebinding tests  |
| Cookie/token leakage          | Dedicated profiles; no cookie/storage tools; redaction; profile excluded from artifacts/export            | Secret-shaped corpus tests          |
| Credential phishing           | Visible origin; mandatory takeover for sensitive fields; model tools disabled during takeover             | Login fixture tests                 |
| Cross-workspace session theft | Profile/workspace binding, one lease, random ids, separate `userDataDir`                                  | Concurrency and cross-binding tests |
| Destructive external click    | Semantic preflight, high-risk classes, approval, no automatic unknown-outcome retry                       | Side-effect fixture tests           |
| Malicious download            | Staging, size/type rules, no execute/open, AC4 ref, quarantine on export                                  | Polyglot/archive/path tests         |
| Local file exfiltration       | Upload only from AC5 object ref after approval; no host paths or general filesystem                       | Unauthorized upload tests           |
| Broker spoof/replay           | Loopback, audience-bound short-lived tokens, nonces, run/profile/policy binding                           | Auth/replay tests                   |
| Worker compromise             | Minimal environment/filesystem, no backend/connector credentials, separate process, kill tree             | Environment and access assertions   |
| Chromium escape               | Prompt patching, pinned builds, child isolation, no ambient secrets; not claimed as VM                    | Dependency SLA and incident drill   |
| Hidden network path           | Proxy with no bypass, service workers blocked, QUIC/WebRTC restrictions                                   | Packet-level integration tests      |
| Accessibility ref confusion   | Generation-bound opaque refs and action-time role/origin recheck                                          | Stale/DOM-swap tests                |
| Screenshot privacy            | Masking, approval warning, short artifact retention                                                       | Redaction fixtures                  |

Browser content is untrusted even on an approved origin. An allowlist grants reachability; it does not certify the page or authorize an external effect.

## Persistence, retention, deletion, and recovery

- AC2 records typed action/lifecycle metadata and approval links.
- AC4 stores only explicitly retained bounded-overflow snapshots, screenshots, downloads, and optional diagnostic traces. It never stores cookies or storage state.
- Profile bytes remain in the app-owned browser profile directory and are not referenced as artifacts.
- Raw accessibility snapshots and screenshots expire after 7 days by default.
- Downloads and explicit user-kept browser artifacts expire after 30 days unless exported or pinned.
- Browser action metadata and safe summaries remain with the main chat until explicit delete.
- Persistent profiles remain until the user deletes the profile or workspace, or an admin retention rule expires an inactive profile after 90 days with warning. Ephemeral contexts and their storage are removed at session close.
- Legal hold pins action evidence/artifacts but does not preserve live browser processes. Whether it may pin a persistent profile is a deployment-policy decision; AC8 defaults to **no** because profiles contain credentials. Legal-hold documentation must state that limitation.
- Chat deletion releases action artifacts by reachability but does not delete a profile that may be shared by other chats in the same workspace. Profile deletion is a separate explicit workflow.
- Profile deletion and artifact deletion write evidence records; secure erase is not claimed.
- On restart, main removes abandoned ephemeral directories and download staging files, checks profile lock ownership, kills owned orphan process trees, and marks interrupted side-effect actions `outcome_unknown`.
- A persistent profile may reopen only with the same workspace binding and compatible pinned Chromium profile version. Incompatible profiles are backed up locally, disabled, and require explicit migration/re-login; they are never opened with a random browser binary.

AC10 owns final configurable retention, quota, dry-run purge, and cleanup UX.

## Observability and audit

### Events

- `browser.session_consent_requested`
- `browser.session_started`
- `browser.takeover_started`
- `browser.takeover_ended`
- `browser.origin_approval_requested`
- `browser.action_started`
- `browser.action_completed`
- `browser.action_outcome_unknown`
- `browser.network_denied`
- `browser.artifact_created`
- `browser.profile_created`
- `browser.profile_deleted`
- `browser.worker_restarted`
- `browser.cleanup_confirmed`
- `browser.cleanup_pending`
- `browser.session_closed`

Events include run/workspace/profile opaque ids, profile mode, current canonical origin, action class, tool name, approval/grant ids, policy hash, start/end time, result/error, byte/node counts, and artifact refs. They exclude full URL query/fragment, page body, snapshot content, selector/ref labels beyond safe redacted names, typed text, form values, cookies, storage, headers, screenshots, file bytes, broker tokens, and profile paths.

### Metrics

- `desktop_browser_worker_restarts_total{reason}`
- `desktop_browser_sessions_active{mode}`
- `desktop_browser_actions_total{class,outcome}`
- `desktop_browser_action_seconds{tool,outcome}`
- `desktop_browser_network_denied_total{reason}`
- `desktop_browser_artifact_bytes_total{kind}`
- `desktop_browser_downloads_total{outcome}`
- `desktop_browser_takeovers_total`
- `desktop_browser_cleanup_pending`
- `desktop_browser_profile_bytes`

Audit answers who approved the profile/session/origin/action, what external action was attempted, the origin and action class, which local file/artifact was transferred, observed result, profile/workspace scope, retention, and cleanup/deletion result. Page content and credentials are not audit fields.

## Acceptance criteria

- Browser tools are available only in `single_user_desktop` with AC8 enabled and a healthy supervised worker.
- Playwright and Chromium execute only in the browser-worker process tree.
- The renderer, preload, Electron main, backend, and AI worker do not import Playwright.
- Bundled Chromium never uses the user’s installed browser profile.
- Ephemeral is default; persistent profile creation is explicit and workspace isolated.
- All model actions use typed element refs and tools; generic selectors, coordinates, JS, CDP, shell, and filesystem are unreachable.
- Default network policy denies all origins; every granted origin is exact HTTPS and every connection passes private-address/DNS-rebinding controls.
- Sensitive input requires user takeover. Cookies/storage never appear in transcript, artifacts, audit, logs, or diagnostics.
- Every side-effecting or ambiguous action is approved before dispatch and unknown outcomes are never retried automatically.
- Downloads/screenshots use AC4 refs; uploads use AC5 refs; neither path accepts arbitrary host paths.
- Browser crash, cancel, update, logout, app shutdown, and profile deletion terminate descendants and clean staging or create durable cleanup evidence.
- Existing web behavior, backend SaaS MCP proxy, and test/website Playwright scripts remain unchanged.

## Detailed test plan

### Contract and MCP

- Validate every tool schema, valid/invalid input, stable error, bounded output, and absence of forbidden tools.
- Assert stale generation, wrong page/session/profile/workspace/run, duplicate request, expired token, and wrong audience fail closed.
- Verify local provider coexists with backend cards without duplicate names and is absent outside desktop.
- Run calls through existing MCP permission, approval, budget, citation, payload, and audit middleware.

### Network/SSRF

- Deny loopback/private/link-local/reserved/multicast/unspecified/CGNAT in IPv4, IPv6, integer/octal/hex, IPv4-mapped IPv6, mixed case, trailing-dot, and punycode forms.
- Deny cloud metadata names and aliases.
- Deny direct URL, 30x redirect, meta refresh, script navigation, iframe, popup, form action, fetch/XHR, image/font/media, WebSocket, DNS rebinding, and rebinding after keepalive expiry.
- Verify service workers cannot start and QUIC/WebRTC cannot create a bypass.
- Permit only an exact approved HTTPS origin and separately approved subresource origins.
- Verify manual navigation outside policy disables all model tools.

### Profiles and credentials

- Two workspaces and two profiles cannot read each other’s cookie, local/session storage, cache, permissions, pages, or downloads.
- Ephemeral storage disappears after close/crash/restart.
- Persistent profile survives expected restart but not cross-workspace binding.
- Password, passkey, MFA, CAPTCHA, payment, and recovery fixtures force takeover and produce no secret-shaped bytes in logs/events/artifacts/transcripts.
- Browser profile directories have macOS modes and Windows ACLs required by policy.

### Action and prompt-injection safety

- Malicious page text requests new origins, local files, cookies, shell, connector tokens, purchases, and secret entry; all environmental controls remain unchanged.
- DOM swaps between snapshot and click produce `browser_element_stale`.
- Hidden/overlaid controls, submit buttons, `onclick` navigation, and ambiguous role labels interrupt.
- Crash and timeout immediately before/after a simulated external side effect yield `outcome_unknown` and no automatic retry.
- User deny/cancel prevents dispatch.

### Artifact transfer

- Redact input values and configured regions from snapshots/screenshots.
- Enforce node/pixel/byte limits and deterministic AC4 offload.
- Reject traversal filenames, links, device entries, decompression bombs, polyglots, executable/script/shortcut/disk-image and macro-enabled files.
- Cancel partial/oversized downloads and remove staging.
- Upload only the approved AC5 object/hash; changing the host file creates a conflict.
- Export applies quarantine/Mark-of-the-Web where supported and never opens the file.

### Supervision and packaging

- Spawn, health timeout, graceful stop, SIGKILL escalation, crash-loop threshold, restart, app shutdown, update, and orphan cleanup.
- Kill Chromium, crashpad, proxy, and other descendant processes on macOS and Windows.
- No worker environment contains backend service token, facade bearer, connector token, provider key, or unrestricted host path.
- Packaged macOS arm64/x64 and Windows x64 smoke tests launch the exact pinned browser and complete a local fixture workflow.
- Upgrade/downgrade incompatible profile tests fail safely.

### Regression and load

- Existing website capture/test Playwright usage is unaffected.
- Web and non-desktop runtime do not load browser dependencies or cards.
- Enforce concurrent profile lease, action/session time, memory, log, snapshot, screenshot, and download quotas.
- Run 100 start/close cycles and assert no orphan processes, ports, profile locks, or staging bytes.

## Rollout, migration, and backout

1. Land protocol, fake worker, local provider, process supervision, and tests with AC8 disabled.
2. Stage a bundled Chromium and read-only ephemeral browser against local fixtures with deny-all network.
3. Enable internal users for exact-origin, read-only public sites with no login/download/upload.
4. Add persistent profiles and interactive takeover after isolation and secret-redaction review.
5. Add screenshots/downloads after AC4 and malware/type/quarantine tests.
6. Add file upload after AC5 object-grant tests.
7. Add side-effecting submits only after approval and unknown-outcome drills.
8. AC10 controls canary/default rollout.

Stop conditions are any private/local network reachability, cookie/secret leakage, cross-profile data, unapproved side effect, direct host path access, generic code/selector escape hatch, orphan process, unbounded artifact, or browser operation outside the worker.

Backout disables the local MCP card, rejects new sessions, closes contexts, kills all owned browser/proxy descendants, deletes ephemeral/staging data, and leaves persistent profiles disabled but intact for user export/delete. Action artifacts remain under retention. The user can continue with connectors or manual browsing; no automated fallback is selected.

There is no migration of the user’s normal browser profile.

## Definition of done

- AC2, AC4, and AC5 dependencies are implemented and AC8 is accepted.
- Product-owned protocol, local MCP provider, browser worker, policy proxy, profile store, takeover, approvals, artifacts, supervision, cleanup, events, metrics, and audit are implemented.
- Playwright and Chromium versions are pinned, staged, licensed, SBOM-recorded, and updateable independently under the desktop release process.
- Contract, SSRF/rebinding, prompt-injection, profile/secret, artifact, supervision, packaging, load, and web-regression suites pass.
- macOS and Windows orphan/cleanup and login-takeover drills are recorded.
- `apps/desktop/docs/agent-browser.md` documents controls, residual isolation risk, profile/delete UX, enterprise origin policy, incident disable, and support diagnostics.
- Security review confirms no claim that Playwright/Chromium is a VM security boundary.

## Why this is sane under SOLID, DRY, KISS, and single-source-of-truth

- **Single responsibility:** worker automates pages; main owns local authority/lifecycle; runtime owns agent orchestration; AC4/AC5 own bytes and host files.
- **Open/closed:** typed MCP tools can evolve by protocol version without exposing Playwright implementation APIs.
- **Liskov substitution:** fake and real workers satisfy the same action/result contract.
- **Interface segregation:** the model receives task-level browser actions, not a general browser programming interface.
- **Dependency inversion:** AI runtime depends on `McpServerProvider`; desktop controller depends on a worker protocol; neither depends across deployable source trees.
- **DRY:** one TypeScript/Zod protocol produces validation and MCP schemas, and existing middleware owns policy/approval/budget/audit.
- **KISS:** one bundled browser, one child, one exact-origin model, ephemeral by default, no user-browser attachment, and no computer use.
- **Single source of truth:** Electron main owns consent/profile bindings, the worker owns live browser state, AC4 owns retained artifacts, and AC2 owns durable action evidence.

## Residual risks

- Chromium and Playwright are large attack surfaces. Fast patching and minimal ambient authority reduce, but do not eliminate, native compromise risk.
- Exact-origin access can still expose sensitive account data and an approved site can contain malicious content.
- Some external effects cannot be proven from browser state. Unknown outcomes require human verification.
- Restrictive profiles can break sites that require service workers, third-party identity origins, WebRTC, or downloads. AC8 fails visibly instead of silently weakening policy.
- Local profile deletion cannot guarantee erasure from OS snapshots, backups, or SSD remanence.

## References

### Repository

- [`package.json`](../../../../package.json)
- [`apps/desktop/main/services/supervisor.ts`](../../../../apps/desktop/main/services/supervisor.ts)
- [`apps/desktop/main/services/desktop-supervisor.ts`](../../../../apps/desktop/main/services/desktop-supervisor.ts)
- [`apps/desktop/main/services/python-service.ts`](../../../../apps/desktop/main/services/python-service.ts)
- [`services/ai-backend/src/agent_runtime/capabilities/mcp/registry.py`](../../../../services/ai-backend/src/agent_runtime/capabilities/mcp/registry.py)
- [`services/ai-backend/src/agent_runtime/capabilities/mcp/backend_provider.py`](../../../../services/ai-backend/src/agent_runtime/capabilities/mcp/backend_provider.py)
- [`docs/architecture/desktop-app.md`](../../../architecture/desktop-app.md)

### Official prior art and platform documentation

- [Playwright browser contexts](https://playwright.dev/docs/api/class-browsercontext) — isolated persistent/non-persistent sessions and service-worker routing limitation.
- [Playwright browser type](https://playwright.dev/docs/api/class-browsertype) — persistent contexts, proxy, downloads, and browser launch.
- [Playwright network](https://playwright.dev/docs/network) and [downloads](https://playwright.dev/docs/downloads) — context routing/proxy behavior and download lifecycle.
- [Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp) — accessibility-snapshot/MCP prior art and its explicit statement that it is not a security boundary.
- [LangChain Playwright toolkit](https://docs.langchain.com/oss/python/integrations/tools/playwright) — prototype/reference tool surface; not adopted as the product policy boundary.
- [MCP authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization) — bearer audience and resource-server security principles; the local broker uses bootstrapped audience tokens rather than a user OAuth flow.
- [Cursor Browser](https://cursor.com/docs/agent/tools/browser) and [Cursor subagents](https://cursor.com/docs/subagents) — public prior art for MCP-controlled browser tools, origin controls, and isolating noisy browser context; no claim about unpublished internals.
- [Claude Cowork desktop architecture](https://support.claude.com/en/articles/14479288-claude-cowork-desktop-architecture-overview) — contrast: Cowork documents hypervisor-isolated execution; AC8 does not claim equivalent isolation.
