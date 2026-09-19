"""User authentication and admin-managed account provisioning.

Users are admin-provisioned (no open registration). Each user owns a private
project that is created on account creation. Login issues a signed JWT that the
browser uses to reach that project's data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Header, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_eval_api.auth import get_db
from agent_eval_api.contracts import (
    LoginRequest,
    LoginResponse,
    UserCreateRequest,
    UserCreatedResponse,
    UserListResponse,
    UserResetPasswordRequest,
    UserResponse,
    UserSetActiveRequest,
)
from agent_eval_api.db import ProjectRecord, UserRecord, new_id, utc_now
from agent_eval_api.security import create_access_token, hash_password, verify_password
from agent_eval_api.settings import Settings, get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


def user_response(user: UserRecord) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        active=user.active,
        project_id=user.project_id,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def get_current_user_from_bearer(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserRecord:
    from agent_eval_api.security import decode_access_token

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
    token = authorization.split(" ", 1)[1].strip()
    user_id = decode_access_token(token, settings)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired token")
    user = db.get(UserRecord, user_id)
    if user is None or not user.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or inactive user")
    return user


def require_admin(user: UserRecord = Depends(get_current_user_from_bearer)) -> UserRecord:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required")
    return user


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LoginResponse:
    user = db.scalar(select(UserRecord).where(UserRecord.email == payload.email))
    if user is None or not user.active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password")
    user.last_login_at = utc_now()
    db.commit()
    token = create_access_token(user.id, settings)
    return LoginResponse(access_token=token, user=user_response(user))


@router.get("/me", response_model=UserResponse)
def me(user: UserRecord = Depends(get_current_user_from_bearer)) -> UserResponse:
    return user_response(user)


@router.post("/users", response_model=UserCreatedResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreateRequest,
    db: Session = Depends(get_db),
    _: UserRecord = Depends(require_admin),
) -> UserCreatedResponse:
    """Admin-only account provisioning. Requires an authenticated admin JWT."""
    existing = db.scalar(select(UserRecord).where(UserRecord.email == payload.email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email already exists")

    # Each user gets their own private project namespace.
    project = ProjectRecord(id=new_id(), name=payload.display_name or payload.email)
    db.add(project)
    db.flush()

    user = UserRecord(
        id=new_id(),
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        role=payload.role,
        active=True,
        project_id=project.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserCreatedResponse(**user_response(user).model_dump())


@router.get("/users", response_model=UserListResponse)
def list_users(
    db: Session = Depends(get_db),
    _: UserRecord = Depends(require_admin),
) -> UserListResponse:
    """Admin-only listing of every provisioned account."""
    rows = db.scalars(select(UserRecord).order_by(UserRecord.created_at)).all()
    return UserListResponse(
        items=[user_response(user) for user in rows],
        total=len(rows),
    )


@router.patch("/users/{user_id}/active", response_model=UserResponse)
def set_user_active(
    user_id: str,
    payload: UserSetActiveRequest,
    db: Session = Depends(get_db),
    admin: UserRecord = Depends(require_admin),
) -> UserResponse:
    """Admin-only enable/disable of an account. An admin cannot disable itself."""
    if user_id == admin.id and not payload.active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot deactivate yourself")
    user = db.get(UserRecord, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    user.active = payload.active
    db.commit()
    db.refresh(user)
    return user_response(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    admin: UserRecord = Depends(require_admin),
) -> None:
    """Admin-only deletion. Cascade-removes the user's private project namespace."""
    if user_id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot delete yourself")
    user = db.get(UserRecord, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    project_id = user.project_id
    db.delete(user)
    project = db.get(ProjectRecord, project_id)
    if project is not None:
        db.delete(project)
    db.commit()


@router.post("/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    user_id: str,
    payload: UserResetPasswordRequest,
    db: Session = Depends(get_db),
    _: UserRecord = Depends(require_admin),
) -> None:
    """Admin-only password reset."""
    user = db.get(UserRecord, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    user.password_hash = hash_password(payload.password)
    db.commit()
