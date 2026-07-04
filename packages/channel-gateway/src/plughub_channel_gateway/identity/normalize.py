"""
identity/normalize.py — normalização e hashing de âncoras de identidade.

Regra de ouro (LGPD): o índice de resolução NUNCA guarda PII em claro. As âncoras
(telefone/e-mail/cpf/…) são normalizadas por tipo e hasheadas com salt por tenant
antes de virar chave Redis. O salt é SEGREDO → vem de env (PLUGHUB_IDENTITY_SALT),
nunca de config-api.
"""
from __future__ import annotations

import hashlib
import re

# Tipos de âncora suportados (Fase A). 'dev' fica para a fase D (device id).
ANCHOR_KINDS = ("phone", "email", "cpf", "princ", "dev")

# Peso de confiança por tipo — usado na desambiguação do Lookup 1.
# Ordem de autoridade (spec §9/§13.7): princ/ext > cpf/email > phone > dev.
KIND_CONFIDENCE: dict[str, float] = {
    "princ": 0.95,
    "cpf":   0.90,
    "email": 0.80,
    "phone": 0.70,
    "dev":   0.30,
}

_NON_DIGITS = re.compile(r"\D+")


def normalize_anchor(kind: str, value: str) -> str:
    """
    Normaliza o valor de uma âncora conforme o tipo, de forma determinística.

      phone → só dígitos com prefixo '+' (E.164 aproximado; assume DDI presente)
      email → trim + lowercase
      cpf   → só dígitos
      princ → trim (o `sub` do JWT do tenant, já opaco)
      dev   → trim

    Levanta ValueError para kind desconhecido ou valor vazio após normalizar.
    """
    if kind not in ANCHOR_KINDS:
        raise ValueError(f"unknown anchor kind: {kind}")
    v = (value or "").strip()
    if not v:
        raise ValueError("empty anchor value")

    if kind == "phone":
        digits = _NON_DIGITS.sub("", v)
        if not digits:
            raise ValueError("phone has no digits")
        return "+" + digits
    if kind == "email":
        return v.lower()
    if kind == "cpf":
        digits = _NON_DIGITS.sub("", v)
        if not digits:
            raise ValueError("cpf has no digits")
        return digits
    # princ / dev — opacos, só trim (já feito acima)
    return v


def hash_anchor(salt: str, kind: str, value: str) -> str:
    """
    value_hash = hex(sha256(salt + normalizado)). O salt (segredo, por tenant)
    garante que o índice não seja um dicionário reverso de PII.
    """
    normalized = normalize_anchor(kind, value)
    digest = hashlib.sha256((salt + normalized).encode("utf-8")).hexdigest()
    return digest


def kind_confidence(kind: str) -> float:
    """Peso de confiança do tipo de âncora (0..1). Desconhecido → 0.5."""
    return KIND_CONFIDENCE.get(kind, 0.5)


# ── Verification class — posse de canal (OTP) vs alegação (digitada) ────────────
# claimed  = cliente afirmou/digitou (não verificada) — grau de "origem/fraca".
# possessed = provada por OTP (posse do canal) — confiável para retomada sensível.
VERIFICATION_CLASSES = ("claimed", "possessed")

# Bônus de ranking para âncora verificada. Alto o suficiente para que QUALQUER
# âncora `possessed` supere QUALQUER âncora `claimed` na desambiguação do Lookup 1
# (possessed phone 0.70+1.0=1.70 > claimed cpf 0.90). Só afeta ordenação; a
# CustomerRef.confidence exposta continua sendo o kind_confidence (0..1).
POSSESSED_RANK_BONUS = 1.0


def anchor_rank_score(kind: str, verification_class: str) -> float:
    """Score interno de desambiguação = kind_confidence + bônus se possessed."""
    base = kind_confidence(kind)
    return base + (POSSESSED_RANK_BONUS if verification_class == "possessed" else 0.0)
