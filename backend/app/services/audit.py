"""Audit trail for security-relevant and usage events."""
from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog


def record(db: Session, action: str, user_id: int | None = None, target: str = "", detail: dict | None = None,
           request: Request | None = None, commit: bool = True) -> None:
    ip = ""
    if request is not None:
        forwarded = request.headers.get("x-forwarded-for")
        ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "")
    db.add(AuditLog(user_id=user_id, action=action, target=target, detail=detail or {}, ip_address=ip))
    if commit:
        db.commit()
