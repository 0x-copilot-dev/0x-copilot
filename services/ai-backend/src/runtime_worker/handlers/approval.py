"""Queued approval-resolution command handling."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.capabilities.mcp.cards import (
    McpLoadError,
    McpLoadErrorCode,
    McpToolCallResult,
)
from agent_runtime.capabilities.mcp.client import (
    McpAuthError,
    McpClientError,
    McpConnectionError,
    McpTimeoutError,
)
from agent_runtime.capabilities.mcp.constants import Messages
from agent_runtime.capabilities.mcp.loader import McpLoader
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
    StreamEventSource,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.settings import RuntimeSettings
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecision,
    MessageRecord,
    MessageRole,
    RuntimeApiEventType,
    RuntimeApprovalResolvedCommand,
    RunRecord,
)
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory

RuntimeDependenciesFactory = Callable[[AgentRuntimeContext], RuntimeDependencies]


class RuntimeApprovalHandler:
    """Consume durable approval-resolution commands after the API records the decision."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        dependencies_factory: RuntimeDependenciesFactory | None = None,
        settings: RuntimeSettings | None = None,
    ) -> None:
        self.persistence = persistence
        self.event_store = event_store
        self.settings = settings or RuntimeSettings.load()
        self.dependencies_factory = (
            dependencies_factory or DefaultRuntimeDependenciesFactory(self.settings)
        )
        self.event_producer = RuntimeEventProducer(
            persistence=persistence,
            event_store=event_store,
        )

    async def handle(self, command: RuntimeApprovalResolvedCommand) -> None:
        run = self.persistence.get_run(org_id=command.org_id, run_id=command.run_id)
        if run is None:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Approval command references an unknown run.",
                retryable=False,
            )
        approval = self.persistence.get_approval_request(
            org_id=command.org_id,
            approval_id=command.approval_id,
        )
        if approval is None:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Approval command references an unknown approval.",
                retryable=False,
            )
        metadata = approval.metadata
        if metadata.get("approval_kind") != "mcp_tool":
            return

        if command.decision is ApprovalDecision.REJECTED:
            self._complete_rejected_mcp_call(command, run, metadata)
            return

        result = await self._execute_approved_mcp_call(run.runtime_context, metadata)
        payload = {
            "tool_name": "call_mcp_tool",
            "call_id": command.approval_id,
            "status": "completed" if result.get("ok") is not False else "failed",
            "output": result,
        }
        self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.TOOL,
            event_type=RuntimeApiEventType.TOOL_RESULT,
            payload=payload,
            summary=self._result_summary(result),
        )
        self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.TOOL,
            event_type=RuntimeApiEventType.TOOL_CALL_COMPLETED,
            payload={
                "tool_name": "call_mcp_tool",
                "call_id": command.approval_id,
                "status": payload["status"],
            },
        )
        final_text = self._assistant_summary(metadata, result)
        self.persistence.append_message(
            MessageRecord(
                conversation_id=run.conversation_id,
                org_id=run.org_id,
                run_id=run.run_id,
                role=MessageRole.ASSISTANT,
                content_text=final_text,
                parent_message_id=run.user_message_id,
                trace_id=run.trace_id,
            )
        )
        completed = self.persistence.update_run_status(
            run_id=run.run_id,
            status=AgentRunStatus.COMPLETED,
        )
        self.event_producer.append_api_event(
            run=completed,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.FINAL_RESPONSE,
            payload={"message": final_text},
            summary=final_text,
            status="completed",
        )
        self.event_producer.append_api_event(
            run=completed,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.RUN_COMPLETED,
            payload={"status": RuntimeApiEventType.RUN_COMPLETED.value},
            summary="Run completed",
        )
        self.event_producer.append_api_event(
            run=completed,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.RUN_COMPLETED,
            payload={"status": RuntimeApiEventType.RUN_COMPLETED.value},
            summary="Run completed",
        )

    async def _execute_approved_mcp_call(
        self,
        runtime_context: AgentRuntimeContext,
        metadata: Mapping[str, object],
    ) -> dict[str, object]:
        server_name = self._text(metadata.get("server_name"))
        tool_name = self._text(metadata.get("tool_name"))
        arguments = metadata.get("arguments")
        if server_name is None or tool_name is None:
            return McpToolCallResult.fail(
                McpLoadErrorCode.INVALID_SERVER_NAME,
                Messages.Loader.STABLE_SERVER_NAME_REQUIRED,
                correlation_id=runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)
        if not isinstance(arguments, Mapping):
            arguments = {}

        dependencies = self.dependencies_factory(runtime_context)
        registry = dependencies.mcp_registry
        loader = McpLoader(registry)  # type: ignore[arg-type]
        loaded = await loader.load_server_by_name(
            server_name=server_name,
            runtime_context=runtime_context,
        )
        if loaded.error is not None:
            return McpToolCallResult.fail_from_load_error(
                loaded.error,
                tool_name=tool_name,
            ).model_dump(mode="json", exclude_none=True)
        if loaded.loaded_server is None or tool_name not in {
            tool.name for tool in loaded.loaded_server.tools
        }:
            return McpToolCallResult.fail(
                McpLoadErrorCode.UNKNOWN_TOOL,
                Messages.Registry.REQUESTED_TOOL_UNKNOWN,
                server_name=server_name,
                tool_name=tool_name,
                correlation_id=runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)
        resolution = registry.resolve_server(server_name)
        if isinstance(resolution, McpLoadError):
            return McpToolCallResult.fail(
                resolution.code,
                resolution.safe_message,
                retryable=resolution.retryable,
                server_name=resolution.server_name or server_name,
                tool_name=tool_name,
                correlation_id=runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)
        try:
            client = resolution.provider.create_client(resolution.card)
            output = await client.call_tool(
                tool_name=tool_name,
                arguments=dict(arguments),
            )
        except (McpTimeoutError, TimeoutError):
            return McpToolCallResult.fail(
                McpLoadErrorCode.TIMEOUT,
                Messages.Loader.TIMEOUT,
                retryable=True,
                server_name=server_name,
                tool_name=tool_name,
                correlation_id=runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)
        except (McpAuthError, PermissionError):
            return McpToolCallResult.fail(
                McpLoadErrorCode.AUTH_FAILURE,
                Messages.Loader.AUTH_FAILED,
                server_name=server_name,
                tool_name=tool_name,
                correlation_id=runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)
        except (McpConnectionError, ConnectionError):
            return McpToolCallResult.fail(
                McpLoadErrorCode.CONNECTION_FAILED,
                Messages.Loader.CONNECTION_FAILED,
                retryable=True,
                server_name=server_name,
                tool_name=tool_name,
                correlation_id=runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)
        except (McpClientError, Exception):
            return McpToolCallResult.fail(
                McpLoadErrorCode.CONNECTION_FAILED,
                Messages.Loader.LOAD_FAILED,
                retryable=True,
                server_name=server_name,
                tool_name=tool_name,
                correlation_id=runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)

        return McpToolCallResult.ok(
            server_name=server_name,
            tool_name=tool_name,
            output=output,
        ).model_dump(mode="json", exclude_none=True)

    def _complete_rejected_mcp_call(
        self,
        command: RuntimeApprovalResolvedCommand,
        run: RunRecord,
        metadata: Mapping[str, object],
    ) -> None:
        server_name = self._text(metadata.get("server_name")) or "MCP server"
        tool_name = self._text(metadata.get("tool_name")) or "tool"
        output = {
            "ok": False,
            "server_name": server_name,
            "tool_name": tool_name,
            "error": {
                "code": "approval_rejected",
                "safe_message": "The user declined this MCP tool call.",
                "retryable": False,
            },
        }
        self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.TOOL,
            event_type=RuntimeApiEventType.TOOL_RESULT,
            payload={
                "tool_name": "call_mcp_tool",
                "call_id": command.approval_id,
                "status": "failed",
                "output": output,
            },
            summary="MCP tool call declined.",
        )
        final_text = f"Declined {server_name} / {tool_name}."
        self.persistence.append_message(
            MessageRecord(
                conversation_id=run.conversation_id,
                org_id=run.org_id,
                run_id=run.run_id,
                role=MessageRole.ASSISTANT,
                content_text=final_text,
                parent_message_id=run.user_message_id,
                trace_id=run.trace_id,
            )
        )
        completed = self.persistence.update_run_status(
            run_id=run.run_id,
            status=AgentRunStatus.COMPLETED,
        )
        self.event_producer.append_api_event(
            run=completed,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.FINAL_RESPONSE,
            payload={"message": final_text},
            summary=final_text,
            status="completed",
        )

    @classmethod
    def _assistant_summary(
        cls,
        metadata: Mapping[str, object],
        result: Mapping[str, object],
    ) -> str:
        server_name = cls._text(metadata.get("display_name")) or cls._text(
            metadata.get("server_name")
        )
        tool_name = cls._text(metadata.get("tool_name")) or "MCP tool"
        if result.get("ok") is False or result.get("error") is not None:
            return f"{server_name or 'MCP'} / {tool_name} could not complete."
        return f"{server_name or 'MCP'} / {tool_name} completed."

    @classmethod
    def _result_summary(cls, result: Mapping[str, object]) -> str:
        if result.get("ok") is False or result.get("error") is not None:
            return "MCP tool call failed."
        return "MCP tool call completed."

    @staticmethod
    def _text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        return text or None
