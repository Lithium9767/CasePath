from fastapi.testclient import TestClient

from casepath.adapters.demo import (
    DemoCaseComparator,
    DemoCaseRetriever,
    DemoConditionProjector,
    DemoExplanationPlanner,
    DemoQuestionPolicy,
    DemoRuleRetriever,
)
from casepath.api.main import create_app, create_live_app
from casepath.application.errors import GraphUnavailable
from casepath.bootstrap import P4Components, build_live_runtime
from casepath.config import RuntimeMode, RuntimeSettings
from casepath.contracts import CapabilityMode, CapabilityStatus
from casepath.workflow import WorkflowDependencies


class FakeGraph:
    def __init__(self):
        self.available = True
        self.closed = False

    def execute_read(self, query, parameters, *, timeout_seconds=None):
        if not self.available:
            raise GraphUnavailable()
        return []

    def verify_connectivity(self):
        if not self.available:
            raise GraphUnavailable()

    def close(self):
        self.closed = True


class FakeLanguageModel:
    def __init__(self):
        self.closed = False

    def generate_structured(self, **kwargs):
        raise AssertionError("P1装配测试不应调用P4模型逻辑")

    def close(self):
        self.closed = True


class ExplicitP4Factory:
    def __init__(self):
        self.received = None

    def build(self, *, graph_gateway, language_model):
        self.received = (graph_gateway, language_model)
        return P4Components(
            workflow_dependencies=WorkflowDependencies(
                rule_retriever=DemoRuleRetriever(),
                condition_projector=DemoConditionProjector(),
                case_retriever=DemoCaseRetriever(),
                case_comparator=DemoCaseComparator(),
                question_policy=DemoQuestionPolicy(),
                explanation_planner=DemoExplanationPlanner(),
            ),
            answer_interpreter=None,
            capabilities=(
                CapabilityStatus(
                    capability="rule_retriever",
                    available=True,
                    mode=CapabilityMode.DEMO,
                    degraded=True,
                    reason="测试显式提供的算法组件，不是P1回退。",
                ),
            ),
        )


class FailingP4Factory:
    def build(self, *, graph_gateway, language_model):
        raise RuntimeError("P4装配失败")


def live_settings(**updates):
    return RuntimeSettings(
        mode=RuntimeMode.LIVE,
        neo4j_password="graph-secret",
        llm_api_key="llm-secret",
        **updates,
    )


def test_live_runtime_injects_clients_and_capabilities_change_with_graph_health():
    graph = FakeGraph()
    llm = FakeLanguageModel()
    factory = ExplicitP4Factory()
    runtime = build_live_runtime(
        live_settings(),
        factory,
        graph_factory=lambda settings: graph,
        language_model_factory=lambda settings: llm,
    )
    assert factory.received == (graph, llm)
    first = {item.capability: item for item in runtime.capabilities()}
    assert first["legal_graph"].available
    assert first["llm"].available
    assert first["session_repository"].degraded

    graph.available = False
    second = {item.capability: item for item in runtime.capabilities()}
    assert not second["legal_graph"].available
    assert second["legal_graph"].mode == CapabilityMode.LIVE

    with TestClient(
        create_app(
            runtime.service,
            capability_provider=runtime.capabilities,
            shutdown_callbacks=(runtime.close,),
        )
    ) as client:
        assert not {
            item["capability"]: item for item in client.get("/v1/capabilities").json()
        }["legal_graph"]["available"]
    assert graph.closed
    assert llm.closed


def test_live_app_starts_without_infrastructure_and_does_not_choose_p4_for_caller():
    settings = RuntimeSettings(
        mode=RuntimeMode.LIVE,
        neo4j_uri=None,
        neo4j_user=None,
        llm_api_key=None,
    )
    factory = ExplicitP4Factory()
    with TestClient(create_live_app(factory, settings=settings)) as client:
        capabilities = {
            item["capability"]: item for item in client.get("/v1/capabilities").json()
        }
        assert not capabilities["legal_graph"]["available"]
        assert not capabilities["llm"]["available"]
    assert factory.received == (None, None)


def test_build_live_runtime_requires_explicit_live_mode():
    factory = ExplicitP4Factory()
    try:
        build_live_runtime(RuntimeSettings(), factory)
    except ValueError as error:
        assert "CASEPATH_MODE=live" in str(error)
    else:
        raise AssertionError("Demo配置不得进入正式装配")


def test_live_runtime_closes_initialized_clients_when_p4_assembly_fails():
    graph = FakeGraph()
    llm = FakeLanguageModel()
    try:
        build_live_runtime(
            live_settings(),
            FailingP4Factory(),
            graph_factory=lambda settings: graph,
            language_model_factory=lambda settings: llm,
        )
    except RuntimeError as error:
        assert str(error) == "P4装配失败"
    else:
        raise AssertionError("P4工厂异常应继续向上传递")
    assert graph.closed
    assert llm.closed
