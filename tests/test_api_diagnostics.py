"""诊断日志不能泄露模型错误正文，且必须能与 HTTP 错误关联。"""

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from casepath.api.errors import register_error_handlers
from casepath.application.errors import LanguageModelUnavailable


def test_failure_has_correlated_private_safe_log(caplog):
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/fail")
    def fail():
        try:
            raise ValueError("SECRET_USER_FACT_AND_KEY")
        except ValueError as exc:
            raise LanguageModelUnavailable("SECRET_PROMPT") from exc

    with caplog.at_level(logging.ERROR, logger="uvicorn.error"), TestClient(app) as client:
        first = client.get("/fail")
        second = client.get("/fail")
    assert first.status_code == 503
    rid = first.json()["request_id"]
    assert rid == first.headers["X-Request-ID"]
    assert rid != second.json()["request_id"]
    assert rid in caplog.text
    assert "LanguageModelUnavailable <- ValueError" in caplog.text
    assert "SECRET" not in caplog.text
    assert "SECRET" not in first.text
