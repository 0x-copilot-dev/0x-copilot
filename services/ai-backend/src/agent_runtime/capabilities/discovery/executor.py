"""The production ``CapabilityExecutorPort``: re-resolve, revalidate, re-enter.

F3.2 shipped ``invoke_capability`` as a bounded fail-closed seam — with no
executor wired, the tool is simply not registered.  This module is the executor
that closes it, and its central design rule is *reuse, not a second route*.

The inner operation reaches the Operation Gateway through
:class:`~agent_runtime.capabilities.mcp.middleware.call_tool.CallMcpTool` — the
one dispatcher every MCP tool invocation already flows through.  That is a
deliberate structural choice rather than a convenience:

* ``CallMcpTool`` is the module that re-resolves the server card, re-checks
  ``McpPermissionPolicy``, builds the :class:`OperationRequest`, persists
  canonical arguments, composes :class:`McpOperationAdapter`, and calls
  ``gateway.invoke``.  Delegating to it means the inner operation is not merely
  *equivalent* to a directly registered MCP tool call — it **is** one, so
  classification, gate resolution, effect staging, approval, usage metrics,
  citation projection, and audit identity are the same code, not a parallel
  implementation that could drift.
* Consequently this module imports no gateway, stager, adapter, descriptor
  registry, or operation-request type.  Everything that could constitute a
  second dispatch path is absent by construction, and a test asserts the import
  set stays that way.

Three narrowing decisions before anything is dispatched:

1. **The descriptor is re-resolved live.**  The bridge has already asked the
   Step RB primitive whether the *reference* is current; this module then asks
   the existing :class:`McpLoader` — and therefore the F8 revision-aware
   discovery cache — what the capability's schema is *now*.  A capability the
   server no longer publishes refuses as stale.
2. **The schema identity is compared, not assumed.**  The digest of the live
   input schema must equal the digest recorded when the capability was
   disclosed.  A schema change between describe and invoke therefore fails
   deterministically, rather than depending on whether the stale arguments
   happen to still validate.
3. **Arguments are checked against the revalidated schema.**  The check is
   refuse-only: it can reject a call, never admit one the schema would not.

Every refusal is raised as :class:`CapabilityExecutionRefused`, which carries a
closed code and no text, so connector, loader, and store detail cannot reach
model-visible output through this seam.

The dispatch *vocabulary* — what a binding is, and what a run disclosed — lives
in :mod:`agent_runtime.capabilities.discovery.dispatch` and is re-exported here
so existing call sites keep resolving.  The split is what lets registration hand
the same run-scoped ledger to the search adapter and to this executor without
importing the concrete dispatcher this module is built around.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import logging
import re
from typing import Any, ClassVar

from agent_runtime.capabilities.discovery.contracts import (
    CapabilityDiscoveryErrorCode,
    CapabilityInvocationReceipt,
    CapabilityInvocationStatus,
    CapabilityInvocationTarget,
    CapabilitySource,
)
from agent_runtime.capabilities.discovery.dispatch import (
    CapabilityDispatchBinding,
    CapabilityDispatchBindingPort,
    RunScopedCapabilityDisclosure,
    RunScopedCapabilityDispatchBindings,
)
from agent_runtime.capabilities.discovery.tool_bridge import CapabilityExecutionRefused
from agent_runtime.capabilities.mcp.cards import (
    JsonSchema,
    McpLoadRequest,
    McpToolDescriptor,
)
from agent_runtime.capabilities.mcp.loader import McpLoader
from agent_runtime.capabilities.mcp.middleware.call_tool import CallMcpTool
from agent_runtime.execution.contracts import AgentRuntimeContext

_LOGGER = logging.getLogger(__name__)

_OPAQUE_TOKEN_PATTERN = re.compile(r"^[!-~]+$")
_INVOCATION_REF_MAX_CHARS = 256

_COMPLETED_SUMMARY = "The capability completed and its result is stored."
_STAGED_SUMMARY = (
    "The capability was proposed and is awaiting approval; "
    "no external change has been made."
)
_REFUSED_SUMMARY = "The capability did not run; no external change was made."


class CapabilityArgumentSchemaCheck:
    """Refuse-only check of canonical arguments against a revalidated schema.

    This is deliberately not a general JSON Schema implementation.  It asserts
    only the constraints it can read unambiguously — object shape, required
    properties, closed additional properties, and declared primitive types — and
    stays silent about everything else, leaving the connector's own validation
    authoritative downstream.  The asymmetry is the point: every rule here can
    only *reject* a call, so an unrecognized construct can never turn into
    permission the schema did not grant.
    """

    MAX_REQUIRED_NAMES: ClassVar[int] = 256

    _PRIMITIVES: ClassVar[dict[str, tuple[type, ...]]] = {
        "string": (str,),
        "array": (list, tuple),
        "object": (Mapping,),
    }

    @classmethod
    def enforce(
        cls,
        *,
        arguments: Mapping[str, Any],
        schema: JsonSchema,
    ) -> None:
        """Raise a typed refusal when ``arguments`` cannot satisfy ``schema``."""

        if not isinstance(schema, Mapping):
            # A descriptor whose schema is not even an object cannot be checked,
            # and dispatching an unvalidatable capability is exactly what this
            # step exists to prevent.
            raise CapabilityExecutionRefused(
                CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE
            )
        declared = schema.get("type")
        if isinstance(declared, str) and declared != "object":
            raise CapabilityExecutionRefused(
                CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE
            )
        properties = schema.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        cls._require_required(arguments=arguments, schema=schema)
        cls._reject_undeclared(
            arguments=arguments,
            schema=schema,
            properties=properties,
        )
        cls._reject_type_mismatch(arguments=arguments, properties=properties)

    @classmethod
    def _require_required(
        cls,
        *,
        arguments: Mapping[str, Any],
        schema: JsonSchema,
    ) -> None:
        required = schema.get("required")
        if not isinstance(required, (list, tuple)):
            return
        names = [name for name in required[: cls.MAX_REQUIRED_NAMES] if name]
        if any(not isinstance(name, str) or name not in arguments for name in names):
            raise CapabilityExecutionRefused(
                CapabilityDiscoveryErrorCode.INVALID_REQUEST
            )

    @classmethod
    def _reject_undeclared(
        cls,
        *,
        arguments: Mapping[str, Any],
        schema: JsonSchema,
        properties: Mapping[str, Any],
    ) -> None:
        # Only a schema that *closes* itself makes an undeclared argument an
        # error. Refusing extras against an open schema would reject calls the
        # connector accepts, which narrows availability without narrowing risk.
        if schema.get("additionalProperties") is not False:
            return
        if any(name not in properties for name in arguments):
            raise CapabilityExecutionRefused(
                CapabilityDiscoveryErrorCode.INVALID_REQUEST
            )

    @classmethod
    def _reject_type_mismatch(
        cls,
        *,
        arguments: Mapping[str, Any],
        properties: Mapping[str, Any],
    ) -> None:
        for name, value in arguments.items():
            spec = properties.get(name)
            if not isinstance(spec, Mapping):
                continue
            if not cls._matches(value, spec.get("type")):
                raise CapabilityExecutionRefused(
                    CapabilityDiscoveryErrorCode.INVALID_REQUEST
                )

    @classmethod
    def _matches(cls, value: object, declared: object) -> bool:
        """Return whether ``value`` satisfies a declared JSON Schema type."""

        if isinstance(declared, str):
            return cls._matches_one(value, declared)
        if isinstance(declared, (list, tuple)) and declared:
            return any(
                cls._matches_one(value, item)
                for item in declared
                if isinstance(item, str)
            )
        # No readable type constraint: this check stays silent rather than
        # inventing one.
        return True

    @classmethod
    def _matches_one(cls, value: object, declared: str) -> bool:
        if declared == "null":
            return value is None
        if declared == "boolean":
            return isinstance(value, bool)
        if declared == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if declared == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        expected = cls._PRIMITIVES.get(declared)
        if expected is None:
            # An unknown type token is not evidence of a mismatch.
            return True
        return isinstance(value, expected)


@dataclass(frozen=True)
class GatewayCapabilityExecutor:
    """Dispatch one authorized capability through the ordinary Operation Gateway.

    ``dispatcher`` is typed as the concrete :class:`CallMcpTool` rather than a
    protocol on purpose.  A protocol would let a future edit substitute some
    other callable and quietly reintroduce a parallel route; a concrete type
    makes "there is exactly one way an inner operation reaches the gateway" a
    property of the type system, in the same spirit as F3.2 gating dispatch on
    the invocation-target *type* rather than a string comparison.
    """

    bindings: CapabilityDispatchBindingPort
    loader: McpLoader
    dispatcher: CallMcpTool

    class Messages:
        """Safe public messages for executor composition."""

        DISPATCHER_TYPE: ClassVar[str] = (
            "the capability executor may dispatch only through the MCP operation "
            "dispatcher"
        )

    def __post_init__(self) -> None:
        # Dataclasses do not validate, and this field is the whole no-second-path
        # guarantee, so it is checked rather than merely annotated.
        if not isinstance(self.dispatcher, CallMcpTool):
            raise TypeError(self.Messages.DISPATCHER_TYPE)

    async def execute(
        self,
        *,
        target: CapabilityInvocationTarget,
        arguments: Mapping[str, Any],
        idempotency_key: str | None,
        runtime_context: AgentRuntimeContext,
    ) -> CapabilityInvocationReceipt:
        """Re-resolve, revalidate, and re-enter the gateway for one capability."""

        self._require_same_subject(runtime_context)
        self._require_no_unsupported_idempotency(idempotency_key)
        binding = self._require_binding(target)
        descriptor = await self._revalidated_descriptor(
            binding=binding,
            runtime_context=runtime_context,
        )
        CapabilityArgumentSchemaCheck.enforce(
            arguments=arguments,
            schema=descriptor.input_schema,
        )
        outcome = await self._dispatch(binding=binding, arguments=arguments)
        return self._receipt(target=target, outcome=outcome)

    def _require_same_subject(self, runtime_context: AgentRuntimeContext) -> None:
        """Refuse a dispatch whose subject is not the dispatcher's own subject.

        The bridge hands over the context its catalog was projected for and the
        dispatcher holds the context the run was admitted under.  They are the
        same object in production; a mismatch means two identities met on one
        call path, which is never a state worth dispatching from.
        """

        bound = self.dispatcher.runtime_context
        if (
            bound.run_id != runtime_context.run_id
            or bound.org_id != runtime_context.org_id
            or bound.user_id != runtime_context.user_id
        ):
            raise CapabilityExecutionRefused(
                CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE
            )

    @staticmethod
    def _require_no_unsupported_idempotency(idempotency_key: str | None) -> None:
        """Refuse rather than silently drop an at-most-once request.

        The MCP dispatcher carries no idempotency-key field into the gateway's
        per-operation coalescer, and building one here would mean a second
        idempotency mechanism beside
        :class:`~agent_runtime.capabilities.operations.context.OperationInvocationRegistry`.
        Accepting the key and ignoring it would promise at-most-once semantics
        for an external effect and not deliver them, so the honest behaviour is
        to refuse until the seam exists.
        """

        if idempotency_key is not None:
            raise CapabilityExecutionRefused(
                CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE
            )

    def _require_binding(
        self,
        target: CapabilityInvocationTarget,
    ) -> CapabilityDispatchBinding:
        """Resolve the only dispatch coordinates this capability may use."""

        if target.source is not CapabilitySource.MCP_SERVER:
            # Only the MCP route has a non-model dispatcher that enters the
            # gateway. A product tool card has no such seam, so it is
            # undispatchable here rather than dispatched some other way.
            raise CapabilityExecutionRefused(
                CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE
            )
        binding = self.bindings.binding_for(target.capability_ref)
        if binding is None or binding.capability_ref != target.capability_ref:
            raise CapabilityExecutionRefused(
                CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE
            )
        return binding

    async def _revalidated_descriptor(
        self,
        *,
        binding: CapabilityDispatchBinding,
        runtime_context: AgentRuntimeContext,
    ) -> McpToolDescriptor:
        """Return the capability's descriptor as the authority reports it now.

        The load goes through the existing :class:`McpLoader`, so it reuses the
        F8 revision-aware discovery cache and re-applies the loader's own
        authorization rather than trusting anything the catalog snapshot said.
        """

        try:
            result = await self.loader.load_server(
                McpLoadRequest(
                    server_name=binding.server_name,
                    runtime_context=runtime_context,
                )
            )
        except Exception as exc:  # noqa: BLE001 - loader detail never reaches output.
            _LOGGER.warning(
                "capability_invoke_descriptor_load_failed",
                extra={"metadata": {"server_name": binding.server_name}},
                exc_info=True,
            )
            raise CapabilityExecutionRefused(
                CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE
            ) from exc
        loaded = result.loaded_server
        if loaded is None:
            raise CapabilityExecutionRefused(
                CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE
            )
        descriptor = self._named(loaded.tools, binding.tool_name)
        if descriptor is None:
            # The authority no longer publishes this capability. That is the
            # same class of answer as a superseded ref: search again.
            raise CapabilityExecutionRefused(
                CapabilityDiscoveryErrorCode.CAPABILITY_STALE
            )
        live_digest = CapabilityDispatchBinding.schema_digest_for(
            descriptor.input_schema
        )
        if live_digest != binding.schema_digest:
            # The schema moved between describe and invoke. Refusing here is
            # deterministic: it does not depend on whether the arguments the
            # model built against the old schema happen to still validate.
            raise CapabilityExecutionRefused(
                CapabilityDiscoveryErrorCode.CAPABILITY_STALE
            )
        return descriptor

    @staticmethod
    def _named(
        descriptors: Sequence[McpToolDescriptor],
        tool_name: str,
    ) -> McpToolDescriptor | None:
        return next(
            (item for item in descriptors if item.name == tool_name),
            None,
        )

    async def _dispatch(
        self,
        *,
        binding: CapabilityDispatchBinding,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Enter the ordinary Operation Gateway through the one MCP dispatcher."""

        try:
            outcome = await self.dispatcher.ainvoke(
                {
                    "server_name": binding.server_name,
                    "tool_name": binding.tool_name,
                    "arguments": dict(arguments),
                }
            )
        except Exception as exc:  # noqa: BLE001 - connector detail stays internal.
            _LOGGER.warning(
                "capability_invoke_dispatch_failed",
                extra={"metadata": {"server_name": binding.server_name}},
                exc_info=True,
            )
            raise CapabilityExecutionRefused(
                CapabilityDiscoveryErrorCode.EXECUTION_FAILED
            ) from exc
        if not isinstance(outcome, Mapping):
            raise CapabilityExecutionRefused(
                CapabilityDiscoveryErrorCode.EXECUTION_FAILED
            )
        return outcome

    def _receipt(
        self,
        *,
        target: CapabilityInvocationTarget,
        outcome: Mapping[str, Any],
    ) -> CapabilityInvocationReceipt:
        """Project the gateway disposition into a body-free bridge receipt.

        Only the operation's own identity, a closed status, and one of this
        module's fixed summaries survive.  Connector payloads, arguments, and
        upstream error text stay behind the stored operation result — the model
        reads a receipt, never a connector body.
        """

        body = outcome.get("output")
        if not isinstance(body, Mapping):
            # No body at all: nothing auditable to hand back. This is also the
            # only remaining shape a typed error can take here — a dispatcher
            # failure that produced no operation identity.
            raise CapabilityExecutionRefused(
                CapabilityDiscoveryErrorCode.EXECUTION_FAILED
            )
        # A typed `error` beside a real body is a FAILED operation, not an
        # unusable one, and its receipt is the more useful answer: `refused`
        # plus a resolvable `invocation_ref` the run can audit, rather than an
        # exception carrying neither. Nothing from the error travels — the
        # summary below comes from this module's fixed map, so a connector
        # payload still cannot reach the model through this path.
        #
        # This branch used to raise, which was invisible while a failed
        # dispatch returned a success envelope with `error` absent. Making that
        # failure typed turned every connector failure on the bridge into an
        # exception, and the receipt it should have produced disappeared.
        status = self._status(body.get("status"))
        invocation_ref = self._invocation_ref(body, status=status)
        if invocation_ref is None:
            # Without an operation identity there is nothing auditable to hand
            # back, and a receipt the run cannot resolve is worse than a refusal.
            raise CapabilityExecutionRefused(
                CapabilityDiscoveryErrorCode.EXECUTION_FAILED
            )
        return CapabilityInvocationReceipt(
            capability_ref=target.capability_ref,
            invocation_ref=invocation_ref,
            status=status,
            safe_summary={
                CapabilityInvocationStatus.COMPLETED: _COMPLETED_SUMMARY,
                CapabilityInvocationStatus.STAGED: _STAGED_SUMMARY,
                CapabilityInvocationStatus.REFUSED: _REFUSED_SUMMARY,
            }[status],
        )

    @staticmethod
    def _status(raw: object) -> CapabilityInvocationStatus:
        """Map the dispatcher's disposition onto the closed bridge vocabulary.

        Unknown dispositions map to ``refused`` rather than to a success, so a
        future gateway outcome cannot be read by the model as a completed
        external effect.
        """

        if not isinstance(raw, str):
            return CapabilityInvocationStatus.REFUSED
        normalized = raw.strip().casefold()
        if normalized in {"completed", "succeeded"}:
            return CapabilityInvocationStatus.COMPLETED
        if normalized == "staged":
            return CapabilityInvocationStatus.STAGED
        return CapabilityInvocationStatus.REFUSED

    @staticmethod
    def _invocation_ref(
        body: Mapping[str, Any],
        *,
        status: CapabilityInvocationStatus,
    ) -> str | None:
        """Return the opaque handle the raw result stays behind."""

        candidate: object = None
        if status is CapabilityInvocationStatus.COMPLETED:
            candidate = body.get("result_ref")
        if not isinstance(candidate, str) or not candidate:
            operation_id = body.get("operation_id")
            candidate = (
                f"operation://{operation_id}"
                if isinstance(operation_id, str) and operation_id
                else None
            )
        if not isinstance(candidate, str):
            return None
        if len(candidate) > _INVOCATION_REF_MAX_CHARS:
            return None
        return candidate if _OPAQUE_TOKEN_PATTERN.match(candidate) else None


__all__ = (
    "CapabilityArgumentSchemaCheck",
    # Re-exported from ``dispatch`` so existing ``from ...executor import X``
    # call sites keep resolving after the dispatch vocabulary moved out.
    "CapabilityDispatchBinding",
    "CapabilityDispatchBindingPort",
    "GatewayCapabilityExecutor",
    "RunScopedCapabilityDisclosure",
    "RunScopedCapabilityDispatchBindings",
)
