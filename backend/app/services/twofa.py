"""TOTP two-factor helpers: secret generation, code verification, and backup codes."""
import hashlib
import json
import secrets
from typing import List, Optional, Tuple

import pyotp

ISSUER = "Zenith Fintech"
N_BACKUP_CODES = 8


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def verify_totp(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    try:
        # valid_window=1 tolerates slight clock drift (±30s).
        return pyotp.TOTP(secret).verify(str(code).strip().replace(" ", ""), valid_window=1)
    except Exception:
        return False


def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().lower().replace("-", "").encode()).hexdigest()


def generate_backup_codes() -> Tuple[List[str], str]:
    """Return (plaintext_codes, json_of_hashes). Plaintext is shown to the user once."""
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(N_BACKUP_CODES)]
    hashes = [_hash(c) for c in codes]
    return codes, json.dumps(hashes)


def consume_backup_code(stored_json: Optional[str], code: str) -> Tuple[bool, Optional[str]]:
    """If `code` matches a stored backup hash, return (True, updated_json_with_it_removed)."""
    if not stored_json or not code:
        return False, stored_json
    try:
        hashes = json.loads(stored_json)
    except Exception:
        return False, stored_json
    h = _hash(code)
    if h in hashes:
        hashes.remove(h)
        return True, json.dumps(hashes)
    return False, stored_json


def backup_codes_remaining(stored_json: Optional[str]) -> int:
    if not stored_json:
        return 0
    try:
        return len(json.loads(stored_json))
    except Exception:
        return 0
