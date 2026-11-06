"""全图浏览不写库、不跨发布版本；分页不静默截断。"""
from types import SimpleNamespace

from fastapi.testclient import TestClient

from casepath.api.main import create_app


def test_graph_requires_live_gateway():
    with TestClient(create_app()) as client:
        assert client.get('/v1/graph').status_code == 503


def test_graph_pagination_and_version_guard():
    calls = []

    class Gateway:
        def execute_read(self, query, parameters):
            calls.append((query, parameters))
            return [{'id': 'a'}, {'id': 'b'}]

    app = create_app()
    projector = SimpleNamespace(catalog=SimpleNamespace(release_id='frozen'))
    app.state.live_runtime = SimpleNamespace(
        graph_gateway=Gateway(), service=SimpleNamespace(workflow=SimpleNamespace(
            dependencies=SimpleNamespace(condition_projector=projector))))
    with TestClient(app) as client:
        response = client.get('/v1/graph?limit=1').json()
        assert response['items'] == [{'id': 'a'}]
        assert response['next_offset'] == 1
        assert calls[0][1] == {'release': 'frozen', 'offset': 0, 'limit': 2}
        assert client.get('/v1/graph?release=old').status_code == 409
        assert client.get('/v1/graph?limit=10000').status_code == 422
        assert client.get('/v1/graph?kind=cypher').status_code == 422
