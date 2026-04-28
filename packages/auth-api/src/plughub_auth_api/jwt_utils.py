"""
jwt_utils.py
Emissão e decodificação de JWT HS256.

Claims do access token:
  sub              — user UUID
  tenant_id        — tenant do usuário
  email            — e-mail
  name             — nome de exibição
  roles            — lista de roles (operator | supervisor | admin | developer | business)
  accessible_pools — lista de pool_ids; [] = acesso a todos os pools
  exp / iat        — padrão JWT
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from .config import Settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(
    user_id: str,
    tenant_id: str,
    email: str,
    name: str,
    roles: list[str],
    accessible_pools: list[str],
    settings: Settings,
) -> str:
    expire = _now() + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub":              user_id,
        "tenant_id":        tenant_id,
        "email":            email,
        "name":             name,
        "roles":            roles,
        "accessible_pools": accessible_pools,
        "iat":              _now(),
        "exp":              expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """
    Decodifica e valida o access token.
    Levanta jose.JWTError em caso de token inválido ou expirado.
    """
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def generate_refresh_token() -> str:
    """Gera token opaco seguro de 43 chars URL-safe (~258 bits de entropia)."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    """SHA-256 do refresh token para armazenamento seguro."""
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()
