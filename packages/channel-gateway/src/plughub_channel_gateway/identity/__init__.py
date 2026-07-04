"""
identity/ — Resolvedor de Identidade (Nível b), Fase A · Slice 1.

Módulo coeso (co-localizado no channel-gateway por reuso do prior art
`pending_workflow`; desenhado para virar serviço próprio depois). Mantém o índice
de resolução (Lookup 1) e o índice de pendências por cliente (Lookup 2) sobre
Redis, sem cadastro durável ainda (PG vem no Slice 2).

Ver docs/product/identity-resolver-fase-a-plano.md e identity-resolver-nivel-b-spec.md.
"""
from .index import CustomerRef, IdentityIndex, PendingEntry
from .normalize import ANCHOR_KINDS, hash_anchor, normalize_anchor
from .otp import OtpService

__all__ = [
    "IdentityIndex",
    "CustomerRef",
    "PendingEntry",
    "OtpService",
    "normalize_anchor",
    "hash_anchor",
    "ANCHOR_KINDS",
]
