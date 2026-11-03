import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from app.config import get_settings
from app.deps import DB, CurrentUser
from app.models import User
from app.schemas import (
    ChangePasswordRequest, ForgotPasswordRequest, LoginRequest, PreferencesPatch, ProfileUpdate, RegisterRequest,
    ResetPasswordRequest, TokenOut, UserOut,
)
from app.security import create_access_token, hash_password, verify_password
from app.services import accounts, audit

router = APIRouter(prefix="/auth", tags=["auth"])

_MAX_FAILURES, _WINDOW_SECONDS = 5, 15 * 60
_failures: dict[str, list[float]] = defaultdict(list)
_failures_lock = threading.Lock()


def _locked(email: str) -> bool:
    now = time.time()
    with _failures_lock:
        _failures[email] = [t for t in _failures[email] if now - t < _WINDOW_SECONDS]
        return len(_failures[email]) >= _MAX_FAILURES


@router.post("/login", response_model=TokenOut)
def login(body: LoginRequest, request: Request, db: DB) -> TokenOut:
    email = body.email.strip().lower()
    if _locked(email):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts. Try again in 15 minutes.")
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        with _failures_lock:
            _failures[email].append(time.time())
        audit.record(db, "login_failed", target=email, request=request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect")
    with _failures_lock:
        _failures.pop(email, None)
    user.last_login_at = datetime.now(timezone.utc)
    audit.record(db, "login", user_id=user.id, target=email, request=request)
    return _token_for(user)


def _token_for(user: User) -> TokenOut:
    token = create_access_token(str(user.id), {"role": user.role})
    return TokenOut(access_token=token, expires_in=get_settings().access_token_expire_minutes * 60,
                    user=UserOut.model_validate(user))


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/options")
def options() -> dict:
    """What the sign-in pages should offer (public)."""
    s = get_settings()
    return {"allow_signup": s.allow_signup, "email_enabled": bool(s.smtp_host),
            "password_rule": "At least 8 characters, including a letter and a number."}


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request, db: DB) -> TokenOut:
    if _locked(f"register:{_client(request)}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many sign-up attempts. Try again later.")
    try:
        user = accounts.register(db, body.full_name, body.email, body.password)
    except accounts.AccountError as exc:
        with _failures_lock:
            _failures[f"register:{_client(request)}"].append(time.time())
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    audit.record(db, "user_registered", user_id=user.id, target=user.email, request=request)
    return _token_for(user)


@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordRequest, request: Request, db: DB) -> dict:
    """Always answers the same way, so the page cannot be used to find out which emails have accounts."""
    key = f"forgot:{body.email.strip().lower()}"
    reply = {"message": "If an account exists for that email, a reset link has been sent. It expires in "
                        f"{get_settings().password_reset_minutes} minutes."}
    if _locked(key):
        return reply
    with _failures_lock:
        _failures[key].append(time.time())  # at most 5 links per address per 15 minutes
    user = accounts.find_user(db, body.email)
    if user is None or not user.is_active:
        audit.record(db, "password_reset_unknown", target=body.email.strip().lower(), request=request)
        return reply
    link = accounts.create_reset_link(db, user)
    sent = accounts.send_reset_email(user, link)
    audit.record(db, "password_reset_requested", user_id=user.id, target=user.email, detail={"emailed": sent},
                 request=request)
    if not sent and get_settings().environment.lower() in {"development", "dev", "test"}:
        reply["dev_reset_url"] = link  # no mail server in development: show the link instead
    return reply


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest, request: Request, db: DB) -> dict:
    try:
        user = accounts.reset_password(db, body.token, body.password)
    except accounts.AccountError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    with _failures_lock:
        _failures.pop(user.email.lower(), None)  # a reset also clears a sign-in lockout
    audit.record(db, "password_reset", user_id=user.id, target=user.email, request=request)
    return {"message": "Your password has been changed. Sign in with the new password."}


@router.post("/change-password")
def change_password(body: ChangePasswordRequest, user: CurrentUser, request: Request, db: DB) -> dict:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The current password is incorrect")
    try:
        accounts.check_password(body.new_password)
    except accounts.AccountError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    user.password_hash = hash_password(body.new_password)
    db.commit()
    audit.record(db, "password_changed", user_id=user.id, target=user.email, request=request)
    return {"message": "Password changed"}


@router.patch("/me", response_model=UserOut)
def update_profile(body: ProfileUpdate, user: CurrentUser, db: DB) -> User:
    user.full_name = body.full_name.strip()
    db.commit()
    return user


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user


@router.patch("/me/preferences", response_model=UserOut)
def update_preferences(body: PreferencesPatch, user: CurrentUser, db: DB) -> User:
    user.preferences = {**(user.preferences or {}), **body.preferences}
    db.commit()
    return user
