from fastapi.testclient import TestClient

from agent_eval_api.main import create_app


def test_health_reports_status_without_secrets(monkeypatch: object) -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "development"}
    assert "api_key" not in response.text.lower()


def test_cors_allows_local_web_origin() -> None:
    client = TestClient(create_app())

    response = client.options(
        "/projects/project-1/agents",
        headers={
            "Origin": "http://127.0.0.1:13000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-workspace-session",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:13000"


def test_cors_allows_real_playwright_web_origin() -> None:
    client = TestClient(create_app())

    response = client.options(
        "/projects/project-1/agents",
        headers={
            "Origin": "http://127.0.0.1:3002",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-workspace-session",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3002"
