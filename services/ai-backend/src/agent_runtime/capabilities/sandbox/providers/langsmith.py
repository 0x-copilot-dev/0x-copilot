"""LangSmith sandbox provider adapter — the one provider AC7 ships.

This is the ``SandboxProviderPort`` implementation that translates the LangSmith
sandbox SDK into the product's lifecycle. It wraps the pinned Deep Agents
``LangSmithSandbox`` backend (which implements ``SandboxBackendProtocol``) and
returns it inside a runtime-only :class:`SandboxHandle`.

Pinning note (PRD "Initial provider decision"): the ``langsmith[sandbox]`` extra
is intentionally NOT added to ``requirements.txt`` in this FOUNDATION change. The
adapter lazy-imports the SDK and raises a typed, redaction-safe error if it is
absent, so the capability fails closed until an implementation spike validates
regions, egress compilation, secret refs, native transfer, cancellation, and
session enumeration. Conformance runs against the in-repo fake provider; this
adapter is exercised only in a controlled staging account.

DEFERRED seams called out inline:

* egress compilation to the LangSmith Auth Proxy ``allow_list`` — the request
  carries a validated :class:`SandboxEgressPolicy`, but this adapter does not
  yet compile/verify it against the provider network control;
* secret-lease injection via the Auth Proxy — carried, not injected.
"""

from __future__ import annotations

from datetime import timedelta

from agent_runtime.capabilities.sandbox.contracts import (
    ManagedSandboxSession,
    SandboxCreateRequest,
    SandboxError,
    SandboxErrorCode,
    SandboxProviderId,
    _utcnow,
)
from agent_runtime.capabilities.sandbox.ports import SandboxHandle


class LangSmithSandboxProvider:
    """Provider adapter backed by the LangSmith sandbox SDK.

    Implements :class:`agent_runtime.capabilities.sandbox.ports.SandboxProviderPort`.
    """

    def __init__(
        self, *, region: str | None = None, session_ttl_seconds: int = 15 * 60
    ) -> None:
        self._region = region
        self._session_ttl_seconds = session_ttl_seconds

    async def create(self, request: SandboxCreateRequest) -> SandboxHandle:
        """Provision a LangSmith sandbox and wrap it as a DeepAgents backend."""

        sandbox_module, backend_cls = self._imports()
        # DEFERRED: compile ``request.egress`` to Auth Proxy ``allow_list`` and
        # verify the effective policy before returning. Until that lands, refuse
        # any request that asks for egress so we never silently run without the
        # network control we advertise.
        if request.egress.mode != "deny_all":
            raise SandboxError(
                SandboxErrorCode.SANDBOX_POLICY_UNSUPPORTED,
                "Egress allowlists are not yet enforced by this provider.",
            )
        if request.secret_refs:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_POLICY_UNSUPPORTED,
                "Secret-lease injection is not yet enabled for this provider.",
            )
        try:
            sandbox = sandbox_module.Sandbox.create()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - normalize SDK failure
            raise SandboxError(
                SandboxErrorCode.SANDBOX_PROVISION_FAILED,
                "The sandbox provider could not provision a session.",
            ) from exc
        backend = backend_cls(sandbox)
        now = _utcnow()
        session = ManagedSandboxSession(
            session_id=request.run_id,
            provider=SandboxProviderId.LANGSMITH,
            provider_session_ref=str(getattr(sandbox, "name", request.run_id)),
            owner_tag=request.owner_tag,
            created_at=now,
            expires_at=now + timedelta(seconds=self._session_ttl_seconds),
            cleanup_state="active",
        )
        return SandboxHandle(session=session, backend=backend)

    async def status(self, provider_session_ref: str) -> ManagedSandboxSession:
        """Return the provider's view of a session (best-effort projection)."""

        raise SandboxError(
            SandboxErrorCode.SANDBOX_PROVIDER_UNCONFIGURED,
            "LangSmith session status requires a validated SDK integration.",
        )

    async def terminate(self, provider_session_ref: str) -> None:
        """Idempotently delete a provider session."""

        sandbox_module, _ = self._imports()
        try:
            sandbox_module.Sandbox.delete(provider_session_ref)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - terminate is idempotent/best-effort
            return

    async def list_owned_sessions(
        self, owner_tag: str
    ) -> tuple[ManagedSandboxSession, ...]:
        """Enumerate live sessions for leak sweeps (requires SDK support)."""

        raise SandboxError(
            SandboxErrorCode.SANDBOX_PROVIDER_UNCONFIGURED,
            "LangSmith session enumeration requires a validated SDK integration.",
        )

    @staticmethod
    def _imports() -> tuple[object, type]:
        """Lazy-import the SDK + DeepAgents backend, or raise a typed error."""

        try:
            from langsmith import sandbox as sandbox_module  # noqa: PLC0415
        except ImportError as exc:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_PROVIDER_UNCONFIGURED,
                "The langsmith[sandbox] extra is not installed.",
            ) from exc
        from deepagents.backends.langsmith import LangSmithSandbox  # noqa: PLC0415

        return sandbox_module, LangSmithSandbox
