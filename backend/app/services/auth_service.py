import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import User, RefreshToken
from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_refresh_token(user_id: int, db: Session) -> str:
    """Create an opaque refresh token, store only its hash, return the raw value."""
    raw = secrets.token_urlsafe(48)
    row = RefreshToken(
        user_id=user_id,
        token_hash=_hash_token(raw),
        expires_at=datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        revoked=False,
    )
    db.add(row)
    db.commit()
    return raw


def get_valid_refresh_token(raw: str, db: Session) -> Optional[RefreshToken]:
    """Return the matching, non-revoked, non-expired RefreshToken row, or None."""
    if not raw:
        return None
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == _hash_token(raw)).first()
    if not row or row.revoked:
        return None
    if row.expires_at and row.expires_at < datetime.utcnow():
        return None
    return row


def revoke_refresh_token(raw: str, db: Session) -> bool:
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == _hash_token(raw)).first()
    if not row:
        return False
    row.revoked = True
    db.commit()
    return True


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=30))
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        raise credentials_exception
    return user
