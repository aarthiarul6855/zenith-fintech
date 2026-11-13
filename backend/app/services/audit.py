"""Lightweight audit logging helper."""
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.models import AuditLog


def _client_ip(request: Optional[Request]) -> str:
    if request is None:
        return ""
    # Respect a proxy's forwarded header when present, else the direct client.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def log(
    db: Session,
    action: str,
    *,
    user_id: Optional[int] = None,
    email: Optional[str] = None,
    detail: str = "",
    request: Optional[Request] = None,
) -> None:
    """Record an audit entry. Never raises — auditing must not break the request."""
    try:
        entry = AuditLog(
            user_id=user_id,
            email=email,
            action=action,
            detail=detail,
            ip=_client_ip(request),
        )
        db.add(entry)
        db.commit()
    except Exception:
        db.rollback()
