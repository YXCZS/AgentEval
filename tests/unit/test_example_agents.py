from fastapi.testclient import TestClient
from tests.fixtures.example_agents import custom_app, order_app, rag_app


def test_custom_agent_returns_schema_oriented_output() -> None:
    response = TestClient(custom_app).post(
        "/run", json={"input": {"subject": "invoice", "priority": "HIGH"}}
    )

    assert response.status_code == 200
    assert response.json()["output"] == {
        "category": "custom",
        "subject": "invoice",
        "priority": "high",
        "accepted": True,
    }
    assert response.json()["tool_calls"] == []


def test_rag_agent_returns_retrieval_evidence() -> None:
    response = TestClient(rag_app).post("/run", json={"input": "What is the refund policy?"})

    assert response.status_code == 200
    assert response.json()["tool_calls"][0]["name"] == "retrieve_policy"
    assert response.json()["output"]["citations"] == ["refund"]


def test_order_agent_covers_policy_and_order_id_paths() -> None:
    client = TestClient(order_app)

    missing = client.post("/run", json={"input": "Cancel my order"}).json()
    prohibited = client.post(
        "/run", json={"input": {"action": "cancel", "order_id": "ORD-1002"}}
    ).json()
    refund = client.post(
        "/run", json={"input": {"action": "refund", "order_id": "ORD-1003"}}
    ).json()
    assert missing["output"]["status"] == "needs_order_id"
    assert prohibited["output"]["status"] == "blocked"
    assert [item["name"] for item in prohibited["tool_calls"]] == ["lookup_order"]
    assert refund["output"]["status"] == "refund_requested"
    assert [item["name"] for item in refund["tool_calls"]] == ["lookup_order", "request_refund"]
