"""End-to-end tests for the admin-managed user lifecycle.

Covers the full member-management surface: login, listing, creating (with an
auto-provisioned private project), deactivating, reactivating, resetting a
password, and deleting a member (a soft delete that deactivates the account and
keeps its private project data intact and auditable).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from agent_eval_api import auth
from agent_eval_api.auth import get_db
from agent_eval_api.db import Base, ProjectRecord, UserRecord
from agent_eval_api.main import create_app
from agent_eval_api.security import hash_password
from agent_eval_api.settings import Settings


@pytest.fixture
def users_client() -> Iterator[tuple[TestClient, Settings, Session]]:
    settings = Settings(
        database_url="sqlite:///:memory:",
        api_key_salt="test-salt",
        workspace_session_secret="test-session",
        jwt_secret="test-jwt-secret",
    )
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[auth.get_settings] = lambda: settings

    # Seed one admin user with a known private project.
    admin_project = ProjectRecord(id="admin-project", name="Admin workspace")
    session.add(admin_project)
    session.flush()
    session.add(
        UserRecord(
            id="admin-user",
            email="admin@example.com",
            password_hash=hash_password("admin-password"),
            display_name="Administrator",
            role="admin",
            active=True,
            project_id="admin-project",
        )
    )
    session.commit()

    with TestClient(app) as client:
        yield client, settings, session
    session.close()
    engine.dispose()


def _login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_admin_can_login_and_read_me(users_client: tuple[TestClient, Settings, Session]) -> None:
    client, _, _ = users_client
    token = _login(client, "admin@example.com", "admin-password")
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "admin@example.com"
    assert body["role"] == "admin"
    assert body["active"] is True
    assert body["project_id"] == "admin-project"


def test_admin_creates_lists_deactivates_reactivates_and_deletes_member(
    users_client: tuple[TestClient, Settings, Session],
) -> None:
    client, _, session = users_client
    admin_token = _login(client, "admin@example.com", "admin-password")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Create a member; the API must auto-provision a private project.
    created = client.post(
        "/auth/users",
        headers=headers,
        json={
            "email": "member@example.com",
            "password": "member-password",
            "display_name": "Member One",
        },
    )
    assert created.status_code == 201, created.text
    member = created.json()
    assert member["role"] == "member"
    assert member["active"] is True
    assert member["project_id"]  # auto-provisioned
    assert member["project_id"] != "admin-project"

    # The member owns a distinct project row.
    assert session.get(ProjectRecord, member["project_id"]) is not None

    # Listing returns both accounts.
    listed = client.get("/auth/users", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    emails = {item["email"] for item in listed.json()["items"]}
    assert emails == {"admin@example.com", "member@example.com"}

    # Deactivate the member.
    deactivated = client.patch(
        f"/auth/users/{member['id']}/active",
        headers=headers,
        json={"active": False},
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False

    # Deactivated member cannot log in.
    denied = client.post(
        "/auth/login", json={"email": "member@example.com", "password": "member-password"}
    )
    assert denied.status_code == 401

    # Reactivate.
    reactivated = client.patch(
        f"/auth/users/{member['id']}/active",
        headers=headers,
        json={"active": True},
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["active"] is True

    # Delete the member; soft-deletion keeps the row and project but blocks login.
    deleted = client.delete(f"/auth/users/{member['id']}", headers=headers)
    assert deleted.status_code == 204
    deleted_user = session.get(UserRecord, member["id"])
    assert deleted_user is not None
    assert deleted_user.deleted_at is not None
    assert deleted_user.active is False
    assert session.get(ProjectRecord, member["project_id"]) is not None

    # A soft-deleted account can no longer log in.
    relogin = client.post(
        "/auth/login", json={"email": "member@example.com", "password": "member-password"}
    )
    assert relogin.status_code == 401

    # Listing excludes the soft-deleted account.
    listed_after = client.get("/auth/users", headers=headers)
    assert listed_after.json()["total"] == 1
    emails_after = {item["email"] for item in listed_after.json()["items"]}
    assert "member@example.com" not in emails_after

    # Re-deleting a soft-deleted account returns 404 (idempotent guard).
    redeleted = client.delete(f"/auth/users/{member['id']}", headers=headers)
    assert redeleted.status_code == 404


def test_admin_cannot_deactivate_or_delete_self(
    users_client: tuple[TestClient, Settings, Session],
) -> None:
    client, _, _ = users_client
    token = _login(client, "admin@example.com", "admin-password")
    headers = {"Authorization": f"Bearer {token}"}

    deactivate = client.patch(
        "/auth/users/admin-user/active", headers=headers, json={"active": False}
    )
    assert deactivate.status_code == 400

    delete = client.delete("/auth/users/admin-user", headers=headers)
    assert delete.status_code == 400


def test_member_cannot_manage_users(
    users_client: tuple[TestClient, Settings, Session],
) -> None:
    client, _, _ = users_client
    admin_token = _login(client, "admin@example.com", "admin-password")
    headers = {"Authorization": f"Bearer {admin_token}"}
    created = client.post(
        "/auth/users",
        headers=headers,
        json={
            "email": "member2@example.com",
            "password": "member-password",
            "display_name": "Member Two",
        },
    )
    assert created.status_code == 201

    member_token = _login(client, "member2@example.com", "member-password")
    member_headers = {"Authorization": f"Bearer {member_token}"}

    assert client.get("/auth/users", headers=member_headers).status_code == 403
    assert (
        client.post(
            "/auth/users",
            headers=member_headers,
            json={
                "email": "another@example.com",
                "password": "member-password",
                "display_name": "Another",
            },
        ).status_code
        == 403
    )


def test_admin_can_reset_member_password(
    users_client: tuple[TestClient, Settings, Session],
) -> None:
    client, _, _ = users_client
    admin_token = _login(client, "admin@example.com", "admin-password")
    headers = {"Authorization": f"Bearer {admin_token}"}
    created = client.post(
        "/auth/users",
        headers=headers,
        json={
            "email": "reset@example.com",
            "password": "old-password",
            "display_name": "Reset Me",
        },
    )
    member_id = created.json()["id"]

    reset = client.post(
        f"/auth/users/{member_id}/reset-password",
        headers=headers,
        json={"password": "new-password"},
    )
    assert reset.status_code == 204

    # Old password no longer works, new one does.
    assert (
        client.post(
            "/auth/login", json={"email": "reset@example.com", "password": "old-password"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/auth/login", json={"email": "reset@example.com", "password": "new-password"}
        ).status_code
        == 200
    )
