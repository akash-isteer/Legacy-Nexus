"""Account lifecycle: sign-up, password policy, password reset links and outgoing email."""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import PasswordResetToken, User
from app.security import hash_password

log = logging.getLogger("legacynexus.accounts")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountError(ValueError):
    pass


def check_password(password: str) -> None:
    """At least 8 characters with a letter and a digit."""
    if len(password) < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise AccountError("Use at least 8 characters, including a letter and a number.")


def find_user(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))


def register(db: Session, full_name: str, email: str, password: str) -> User:
    settings = get_settings()
    if not settings.allow_signup:
        raise AccountError("Self sign-up is turned off. Ask an administrator for an account.")
    email = email.strip().lower()
    if not EMAIL.match(email):
        raise AccountError("Enter a valid email address.")
    check_password(password)
    if find_user(db, email):
        raise AccountError("An account with this email already exists. Sign in or reset your password.")
    user = User(email=email, full_name=full_name.strip(), password_hash=hash_password(password),
                role=settings.signup_default_role if settings.signup_default_role in {"analyst", "viewer"} else "analyst",
                preferences={})
    db.add(user)
    db.commit()
    return user


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_reset_link(db: Session, user: User) -> str:
    """A one-time link; older unused links for the user stop working."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    db.execute(update(PasswordResetToken).where(PasswordResetToken.user_id == user.id,
                                                PasswordResetToken.used_at.is_(None)).values(used_at=now))
    token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(user_id=user.id, token_hash=_hash(token),
                              expires_at=now + timedelta(minutes=settings.password_reset_minutes)))
    db.commit()
    return f"{settings.app_base_url.rstrip('/')}/reset-password?token={token}"


def reset_password(db: Session, token: str, password: str) -> User:
    check_password(password)
    row = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == _hash(token)))
    now = datetime.now(timezone.utc)
    invalid = AccountError("This reset link is invalid or has expired. Request a new one.")
    if row is None or row.used_at is not None:
        raise invalid
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)  # SQLite
    if expires < now:
        raise invalid
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise AccountError("This account is disabled. Contact an administrator.")
    user.password_hash = hash_password(password)
    row.used_at = now
    db.commit()
    return user


def send_reset_email(user: User, link: str) -> bool:
    """Send the reset link. Returns False when email is not configured (the link is logged instead)."""
    settings = get_settings()
    minutes = settings.password_reset_minutes
    if not settings.smtp_host:
        log.warning("SMTP is not configured; password reset link for %s: %s", user.email, link)
        return False
    msg = EmailMessage()
    msg["Subject"] = "Reset your LegacyNexus password"
    msg["From"] = settings.smtp_from
    msg["To"] = user.email
    msg.set_content(
        f"Hello {user.full_name},\n\nUse this link to choose a new password. It works once and expires in "
        f"{minutes} minutes:\n\n{link}\n\nIf you did not ask for this, ignore this email; your password is unchanged.\n")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password or "")
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException):
        log.exception("Could not send the password reset email to %s", user.email)
        return False
