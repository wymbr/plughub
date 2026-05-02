"""
password.py
Utilitários de hash/verificação de senha com bcrypt.
"""
from __future__ import annotations

import bcrypt


def hash_password(plain: str) -> str:
    """Retorna hash bcrypt da senha em texto puro."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Retorna True se a senha em texto puro bate com o hash."""
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False
