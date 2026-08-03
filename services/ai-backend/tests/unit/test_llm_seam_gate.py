"""PRD-A2 D7 canaries for the shared model-construction seam guard."""

from __future__ import annotations

from pathlib import Path

from agent_runtime.observability.llm_seam_conformance import (
    CANONICAL_MODEL_FUNNEL,
    canonical_agent_topology_present,
    canonical_chat_model_call_sites,
    canonical_model_funnel_present,
    llm_seam_violations,
)


_SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def test_init_chat_model_only_in_funnel_and_no_direct_provider_imports() -> None:
    assert canonical_model_funnel_present(_SRC_ROOT)
    assert canonical_agent_topology_present(_SRC_ROOT)
    assert llm_seam_violations(_SRC_ROOT) == ()


def test_canonical_chat_model_consumer_inventory_is_reviewed() -> None:
    assert canonical_chat_model_call_sites(_SRC_ROOT) == (
        "agent_runtime/api/shape_request_coordinator.py:"
        "ShapeRequestCoordinator._completion_for -> build_chat_model_from_id",
        "agent_runtime/api/surface_view_coordinator.py:"
        "SurfaceViewCoordinator._completion_for -> build_chat_model_from_id",
        "agent_runtime/capabilities/surfaces/generator.py:"
        "build_surface_generation_scheduler -> build_chat_model_from_id",
        "agent_runtime/execution/deep_agent_builder.py:"
        "build_chat_model_from_id -> build_chat_model",
        "agent_runtime/execution/deep_agent_builder.py:"
        "build_deep_agent -> build_chat_model",
        "runtime_worker/jobs/proposal_extractor.py:"
        "ProposalExtractor._invoke_model -> build_chat_model",
        "runtime_worker/jobs/todo_extractor.py:"
        "TodoExtractor._invoke_model -> build_chat_model",
        "runtime_worker/model_invocation_composition.py:"
        "_RouteModelResolver.resolve -> build_chat_model",
    )


def test_gate_fails_on_planted_init_reference(tmp_path: Path) -> None:
    rogue = tmp_path / "some" / "rogue_file.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text(
        "from langchain.chat_models import init_chat_model\n"
        "model = init_chat_model('gpt-5-mini')\n",
        encoding="utf-8",
    )

    assert llm_seam_violations(tmp_path) == (
        "some/rogue_file.py: imports init_chat_model",
        "some/rogue_file.py: references init_chat_model()",
    )


def test_gate_ignores_docstring_and_comment_mentions(tmp_path: Path) -> None:
    source = tmp_path / "doc_only.py"
    source.write_text(
        '"""This module explains init_chat_model and init_embeddings."""\n'
        "# init_chat_model is called only in deep_agent_builder\n"
        "x = 1\n",
        encoding="utf-8",
    )

    assert llm_seam_violations(tmp_path) == ()


def test_gate_fails_on_planted_provider_import(tmp_path: Path) -> None:
    source = tmp_path / "rogue.py"
    source.write_text("import langchain_openai\n", encoding="utf-8")

    assert llm_seam_violations(tmp_path) == ("rogue.py: import langchain_openai",)


def test_gate_allows_internal_openai_compat_module(tmp_path: Path) -> None:
    source = tmp_path / "ok.py"
    source.write_text(
        "from agent_runtime.execution.openai_compat import "
        "CUSTOM_OPENAI_COMPATIBLE_PROVIDER\n",
        encoding="utf-8",
    )

    assert llm_seam_violations(tmp_path) == ()


def test_missing_funnel_is_detected(tmp_path: Path) -> None:
    assert canonical_model_funnel_present(tmp_path) is False
    assert canonical_agent_topology_present(tmp_path) is False
    assert CANONICAL_MODEL_FUNNEL.as_posix().endswith("deep_agent_builder.py")


def test_gate_fails_on_planted_agent_graph_bypasses(tmp_path: Path) -> None:
    rogue_graph = tmp_path / "rogue_graph.py"
    rogue_graph.write_text(
        "from deepagents import create_deep_agent\n"
        "agent = create_deep_agent(model='openai:test')\n",
        encoding="utf-8",
    )
    rogue_runtime = tmp_path / "rogue_runtime.py"
    rogue_runtime.write_text(
        "from agent_runtime.execution.deep_agent_builder import build_deep_agent\n"
        "agent = build_deep_agent(request)\n",
        encoding="utf-8",
    )

    assert llm_seam_violations(tmp_path) == (
        "rogue_graph.py: calls create_deep_agent() outside "
        "canonical Deep Agents builder",
        "rogue_runtime.py: calls build_deep_agent() outside canonical runtime factory",
    )


def test_gate_fails_when_graph_and_runtime_builders_are_aliased(
    tmp_path: Path,
) -> None:
    rogue = tmp_path / "aliased_graph.py"
    rogue.write_text(
        "from deepagents import create_deep_agent as compile_graph\n"
        "from agent_runtime.execution.deep_agent_builder import (\n"
        "    build_deep_agent as compile_runtime,\n"
        ")\n"
        "first = compile_graph(model='openai:test')\n"
        "second = compile_runtime(request)\n",
        encoding="utf-8",
    )

    assert llm_seam_violations(tmp_path) == (
        "aliased_graph.py: calls build_deep_agent() outside canonical runtime factory",
        "aliased_graph.py: calls create_deep_agent() outside "
        "canonical Deep Agents builder",
    )


class _TopologyFixtureMixin:
    """Writes the two files ``canonical_agent_topology_present`` reads."""

    @staticmethod
    def write_topology(root_dir: Path, *, root: str, child: str) -> None:
        builder = root_dir / "agent_runtime" / "execution" / "deep_agent_builder.py"
        builder.parent.mkdir(parents=True, exist_ok=True)
        builder.write_text(
            "def build_deep_agent(request):\n"
            "    model = build_chat_model(request.model_config)\n"
            "    return create_deep_agent(model=model)\n",
            encoding="utf-8",
        )
        factory = root_dir / "agent_runtime" / "execution" / "factory.py"
        factory.write_text(
            "request = DeepAgentBuildRequest(\n"
            f"    middleware=({root}),\n"
            f"    universal_middleware_factories=({child}),\n"
            ")\n",
            encoding="utf-8",
        )


def test_agent_topology_requires_exact_root_and_child_controller(
    tmp_path: Path,
) -> None:
    builder = tmp_path / "agent_runtime" / "execution" / "deep_agent_builder.py"
    builder.parent.mkdir(parents=True)
    builder.write_text(
        "def build_deep_agent(request):\n"
        "    model = build_chat_model(request.model_config)\n"
        "    return create_deep_agent(model=model)\n",
        encoding="utf-8",
    )
    factory = tmp_path / "agent_runtime" / "execution" / "factory.py"
    factory.write_text(
        "request = DeepAgentBuildRequest(\n"
        "    middleware=(RuntimeControlMiddleware(), ModelInvocationMiddleware()),\n"
        "    universal_middleware_factories=(OtherMiddleware,),\n"
        ")\n",
        encoding="utf-8",
    )

    assert canonical_agent_topology_present(tmp_path) is False

    factory.write_text(
        "request = DeepAgentBuildRequest(\n"
        "    middleware=(RuntimeControlMiddleware(), ModelInvocationMiddleware()),\n"
        "    universal_middleware_factories=(RuntimeControlMiddleware, "
        "ModelInvocationMiddleware),\n"
        ")\n",
        encoding="utf-8",
    )

    assert canonical_agent_topology_present(tmp_path) is True

    factory.write_text(
        "request = DeepAgentBuildRequest(\n"
        "    middleware=(OtherMiddleware(),),\n"
        "    universal_middleware_factories=(RuntimeControlMiddleware, "
        "ModelInvocationMiddleware),\n"
        ")\n",
        encoding="utf-8",
    )

    assert canonical_agent_topology_present(tmp_path) is False

    factory.write_text(
        "request = DeepAgentBuildRequest(\n"
        "    universal_middleware_factories=(RuntimeControlMiddleware, "
        "ModelInvocationMiddleware),\n"
        ")\n",
        encoding="utf-8",
    )

    assert canonical_agent_topology_present(tmp_path) is False

    factory.write_text(
        "request = DeepAgentBuildRequest(\n"
        "    middleware=(RuntimeControlMiddleware(), ModelInvocationMiddleware()),\n"
        "    universal_middleware_factories=(RuntimeControlMiddleware, "
        "ModelInvocationMiddleware),\n"
        ")\n",
        encoding="utf-8",
    )

    builder.write_text(
        builder.read_text(encoding="utf-8")
        + "\nrogue = create_deep_agent(model='openai:test')\n",
        encoding="utf-8",
    )

    assert canonical_agent_topology_present(tmp_path) is False


class TestTheConditionalDesktopMiddlewareIsNamedNotWaved(_TopologyFixtureMixin):
    """The host path translator is reviewed BY NAME, not admitted by counting.

    It is installed only when the desktop host filesystem rules are, so it
    appears as a starred call to the factory helper that returns it or an empty
    tuple. The gate pins that helper's name: adding an unreviewed middleware —
    or smuggling one in behind a different starred call — must still fail, or
    this gate would have stopped being a gate the moment the sequence became
    variable-length.
    """

    def test_the_reviewed_starred_helpers_are_accepted(self, tmp_path: Path) -> None:
        self.write_topology(
            tmp_path,
            root="RuntimeControlMiddleware(), ModelInvocationMiddleware(), "
            "*_host_path_tool_middleware(workspace_backend)",
            child="RuntimeControlMiddleware, ModelInvocationMiddleware, "
            "*_host_path_tool_middleware_factories(workspace_backend)",
        )

        assert canonical_agent_topology_present(tmp_path) is True

    def test_an_unreviewed_starred_helper_is_refused(self, tmp_path: Path) -> None:
        self.write_topology(
            tmp_path,
            root="RuntimeControlMiddleware(), ModelInvocationMiddleware(), "
            "*_whatever_middleware(workspace_backend)",
            child="RuntimeControlMiddleware, ModelInvocationMiddleware, "
            "*_host_path_tool_middleware_factories(workspace_backend)",
        )

        assert canonical_agent_topology_present(tmp_path) is False

    def test_an_extra_constructed_middleware_is_refused(self, tmp_path: Path) -> None:
        self.write_topology(
            tmp_path,
            root="RuntimeControlMiddleware(), ModelInvocationMiddleware(), "
            "SomethingElseMiddleware()",
            child="RuntimeControlMiddleware, ModelInvocationMiddleware",
        )

        assert canonical_agent_topology_present(tmp_path) is False

    def test_more_starred_helpers_than_reviewed_is_refused(
        self, tmp_path: Path
    ) -> None:
        self.write_topology(
            tmp_path,
            root="RuntimeControlMiddleware(), ModelInvocationMiddleware(), "
            "*_host_path_tool_middleware(workspace_backend), *_extra()",
            child="RuntimeControlMiddleware, ModelInvocationMiddleware",
        )

        assert canonical_agent_topology_present(tmp_path) is False

    def test_dropping_a_required_member_is_still_refused(self, tmp_path: Path) -> None:
        self.write_topology(
            tmp_path,
            root="RuntimeControlMiddleware(), "
            "*_host_path_tool_middleware(workspace_backend)",
            child="RuntimeControlMiddleware, ModelInvocationMiddleware",
        )

        assert canonical_agent_topology_present(tmp_path) is False

    def test_reordering_the_required_members_is_refused(self, tmp_path: Path) -> None:
        self.write_topology(
            tmp_path,
            root="ModelInvocationMiddleware(), RuntimeControlMiddleware()",
            child="RuntimeControlMiddleware, ModelInvocationMiddleware",
        )

        assert canonical_agent_topology_present(tmp_path) is False
