"""User administration (administrators only)."""
from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from app.deps import DB, AdminUser
from app.models import Project, User
from app.schemas import UserAdminUpdate
from app.services import accounts, audit

router = APIRouter(prefix="/users", tags=["users"])


def _public(u: User, projects: int = 0) -> dict:
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role, "is_active": u.is_active,
            "created_at": u.created_at, "last_login_at": u.last_login_at, "projects": projects}


@router.get("")
def list_users(_: AdminUser, db: DB) -> list[dict]:
    counts = dict(db.execute(select(Project.owner_id, func.count(Project.id)).group_by(Project.owner_id)).all())
    return [_public(u, counts.get(u.id, 0)) for u in db.scalars(select(User).order_by(User.created_at))]


@router.patch("/{user_id}")
def update_user(user_id: int, body: UserAdminUpdate, admin: AdminUser, db: DB, request: Request) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    demoting = body.role is not None and body.role != "admin" and user.role == "admin"
    disabling = body.is_active is False and user.role == "admin"
    if (demoting or disabling) and _active_admins(db) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Keep at least one active administrator")
    if user.id == admin.id and body.is_active is False:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot disable your own account")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(user, k, v)
    db.commit()
    audit.record(db, "user_updated", admin.id, f"user:{user.id}", changes, request)
    return _public(user)


@router.post("/{user_id}/reset-link")
def reset_link(user_id: int, admin: AdminUser, db: DB, request: Request) -> dict:
    """Create a one-time password reset link for a user (emailed when SMTP is configured)."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    link = accounts.create_reset_link(db, user)
    sent = accounts.send_reset_email(user, link)
    audit.record(db, "password_reset_link_created", admin.id, f"user:{user.id}", {"emailed": sent}, request)
    return {"emailed": sent, "reset_url": None if sent else link}


def _active_admins(db) -> int:
    return db.scalar(select(func.count(User.id)).where(User.role == "admin", User.is_active.is_(True))) or 0
