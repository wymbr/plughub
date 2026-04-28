"""
webhooks.py
Token generation, hashing, and verification utilities for webhook authentication.

Design:
  - Tokens are generated with Python's `secrets` module (CSPRNG).
  - Format: "plughub_wh_<url-safe-43-chars>"  (~258 bits entropy)
  - Stored in DB as SHA-256 hex digest — plain token is NEVER persisted.
  - token_prefix (first 16 chars of plain token) is stored for display/identification.
  - Constant-time comparison via `hmac.compare_digest` prevents timing attacks.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

# Prefix makes tokens recognisable in logs and credential scanners.
_TOKEN_PREFIX = "plughub_wh_"

# How many bytes of display prefix to store (shown in admin UI, never enough to brute-force).
_PREFIX_DISPLAY_LEN = 16


def generate_token() -> tuple[str, str, str]:
    """
    Generate a new webhook token.

    Returns:
        (plain_token, token_hash, token_prefix)
        - plain_token  : full token shown once to the operator — never stored
        - token_hash   : SHA-256 hex digest — stored in DB
        - token_prefix : first `_PREFIX_DISPLAY_LEN` chars of plain — stored in DB
    """
    random_part = secrets.token_urlsafe(32)          # 256-bit random
    plain       = f"{_TOKEN_PREFIX}{random_part}"
    hashed      = _hash_token(plain)
    prefix      = plain[:_PREFIX_DISPLAY_LEN]
    return plain, hashed, prefix


def _hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def verify_token(plain: str, stored_hash: str) -> bool:
    """
    Verify a candidate plain token against the stored SHA-256 hash.
    Uses constant-time comparison to prevent timing attacks.
    """
    computed = _hash_token(plain)
    return hmac.compare_digest(computed, stored_hash)
