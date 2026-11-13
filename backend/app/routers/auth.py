from fastapi import APIRouter, Depends, HTTPException, status, Request, Form
from fastapi.security import OAuth2PasswordRequestForm

from sqlalchemy.orm import Session
from datetime import timedelta
from typing import List, Optional
from pydantic import BaseModel
from app.database import get_db
from app.models.models import User, AuditLog
from app.schemas.schemas import UserCreate, UserOut, Token, LoginRequest
from app.services.auth_service import (
    hash_password, verify_password, create_access_token, get_current_user,
    issue_refresh_token, get_valid_refresh_token, revoke_refresh_token,
)
from app.services.limiter import limiter
from app.services import audit, twofa
from app.config import settings

router = APIRouter(prefix="/auth", tags=["Auth"])


class CodeIn(BaseModel):
    code: str


class RefreshIn(BaseModel):
    refresh_token: str


def _issue_tokens(user_id: int, db: Session) -> dict:
    access = create_access_token(
        {"sub": str(user_id)},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    refresh = issue_refresh_token(user_id, db)
    return {"access_token": access, "token_type": "bearer", "refresh_token": refresh}


@router.post("/register", response_model=UserOut, status_code=201)
@limiter.limit("5/minute")
def register(request: Request, payload: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Email already registered.")
    user = User(
        email=payload.email,
        name=payload.name,
        hashed_password=hash_password(payload.password),
        monthly_income=payload.monthly_income,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    audit.log(db, "register", user_id=user.id, email=user.email, detail="Account created", request=request)
    return user

@router.post("/login", response_model=Token)
@limiter.limit("5/minute")
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    totp_code: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == form_data.username).first()

    if not user or not verify_password(form_data.password, user.hashed_password):
        audit.log(db, "login_failed", user_id=user.id if user else None,
                  email=form_data.username, detail="Invalid credentials", request=request)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials."
        )

    # Second factor, when enabled.
    if user.totp_enabled:
        if not totp_code:
            # Distinct signal so the frontend knows to prompt for a code.
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="2FA code required")
        ok = twofa.verify_totp(user.totp_secret, totp_code)
        used_backup = False
        if not ok:
            ok, updated = twofa.consume_backup_code(user.backup_codes, totp_code)
            if ok:
                user.backup_codes = updated
                used_backup = True
                db.commit()
        if not ok:
            audit.log(db, "login_failed", user_id=user.id, email=user.email, detail="Invalid 2FA code", request=request)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid 2FA code")
        detail = "Signed in (backup code)" if used_backup else "Signed in (2FA)"
    else:
        detail = "Signed in"

    audit.log(db, "login_success", user_id=user.id, email=user.email, detail=detail, request=request)
    return _issue_tokens(user.id, db)


@router.post("/refresh", response_model=Token)
def refresh(payload: RefreshIn, request: Request, db: Session = Depends(get_db)):
    """Exchange a valid refresh token for a new access + refresh pair (rotation)."""
    row = get_valid_refresh_token(payload.refresh_token, db)
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token.")
    user = db.query(User).filter(User.id == row.user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token.")
    # Rotate: revoke the used token, then issue a fresh pair.
    row.revoked = True
    db.commit()
    audit.log(db, "token_refresh", user_id=user.id, email=user.email, detail="Session refreshed", request=request)
    return _issue_tokens(user.id, db)


@router.post("/logout")
def logout(payload: RefreshIn, request: Request, db: Session = Depends(get_db)):
    """Revoke the given refresh token server-side (idempotent)."""
    row = get_valid_refresh_token(payload.refresh_token, db)
    revoke_refresh_token(payload.refresh_token, db)
    if row:
        audit.log(db, "logout", user_id=row.user_id, detail="Signed out", request=request)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/activity")
def activity(
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Recent account-activity entries for the signed-in user (security log)."""
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.user_id == current_user.id)
        .order_by(AuditLog.created_at.desc())
        .limit(min(limit, 100))
        .all()
    )
    return [
        {
            "action": r.action,
            "detail": r.detail,
            "ip": r.ip,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


# ── Two-Factor Authentication (TOTP) ──────────────────────────────────────────

@router.get("/2fa/status")
def twofa_status(current_user: User = Depends(get_current_user)):
    return {
        "enabled": bool(current_user.totp_enabled),
        "backup_codes_remaining": twofa.backup_codes_remaining(current_user.backup_codes),
    }


@router.post("/2fa/setup")
def twofa_setup(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate (or regenerate) a secret and return a provisioning URI. Not active until verified."""
    if current_user.totp_enabled:
        raise HTTPException(400, "2FA is already enabled. Disable it first to re-enroll.")
    secret = twofa.new_secret()
    current_user.totp_secret = secret
    db.commit()
    return {
        "secret": secret,
        "otpauth_uri": twofa.provisioning_uri(secret, current_user.email),
    }


@router.post("/2fa/verify")
def twofa_verify(
    payload: CodeIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Confirm the first code to activate 2FA; returns one-time backup codes."""
    if not current_user.totp_secret:
        raise HTTPException(400, "Start 2FA setup first.")
    if not twofa.verify_totp(current_user.totp_secret, payload.code):
        raise HTTPException(400, "Invalid code. Try again.")
    plaintext, hashes_json = twofa.generate_backup_codes()
    current_user.totp_enabled = True
    current_user.backup_codes = hashes_json
    db.commit()
    audit.log(db, "2fa_enabled", user_id=current_user.id, email=current_user.email, detail="2FA enabled", request=request)
    return {"enabled": True, "backup_codes": plaintext}


@router.post("/2fa/disable")
def twofa_disable(
    payload: CodeIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Disable 2FA after verifying a current TOTP or backup code."""
    if not current_user.totp_enabled:
        raise HTTPException(400, "2FA is not enabled.")
    ok = twofa.verify_totp(current_user.totp_secret, payload.code)
    if not ok:
        ok, _ = twofa.consume_backup_code(current_user.backup_codes, payload.code)
    if not ok:
        raise HTTPException(400, "Invalid code.")
    current_user.totp_enabled = False
    current_user.totp_secret = None
    current_user.backup_codes = None
    db.commit()
    audit.log(db, "2fa_disabled", user_id=current_user.id, email=current_user.email, detail="2FA disabled", request=request)
    return {"enabled": False}
