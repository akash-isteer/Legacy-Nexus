"""Shared FastAPI dependencies: authentication, authorisation and project access."""
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Project, User
from app.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)

DB = Annotated[Session, Depends(get_db)]


def get_current_user(db: DB, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue",
                                 headers={"WWW-Authenticate": "Bearer"})
    if creds is None:
        raise unauthorized
    try:
        payload = decode_access_token(creds.credentials)
        user = db.get(User, int(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    if user is None or not user.is_active:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator role required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def require_writer(user: CurrentUser) -> User:
    """Analysts and administrators change data; viewers have read-only access."""
    if user.role not in {"admin", "analyst"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role is read-only (viewer)")
    return user


WriterUser = Annotated[User, Depends(require_writer)]


def get_project(project_id: int, db: DB, user: CurrentUser) -> Project:
    project = db.get(Project, project_id)
    if project is None or (project.owner_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


def get_ready_project(project: Annotated[Project, Depends(get_project)]) -> Project:
    if project.status != "ready":
        raise HTTPException(status.HTTP_409_CONFLICT, "Run the analysis first; results are not available yet")
    return project


ProjectDep = Annotated[Project, Depends(get_project)]
ReadyProject = Annotated[Project, Depends(get_ready_project)]
