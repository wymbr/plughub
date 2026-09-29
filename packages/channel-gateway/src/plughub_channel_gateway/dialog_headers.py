"""
dialog_headers.py — AUT-62: os headers com que o gateway LÊ a dialog-api.

A dialog-api tinha leitura aberta, e a borda pública a publicava: com um `X-Tenant-ID`
qualquer um lia os formulários de todos os tenants. Desde 2026-09-29 o runtime entra
por `X-Service-Token` (só leitura). Os três leitores do gateway — survey web, pin de
versão e sonda de máscara do collect — montam o header AQUI, para não existirem três
respostas para "com que credencial eu leio formulário?".

Token vazio ⇒ o header sai sem ele, a dialog-api responde 401, e cada leitor loga o
motivo pelo caminho de degradação que já tinha. Nunca "funciona sem credencial".
"""
from __future__ import annotations

from .config import get_settings

__all__ = ["dialog_headers"]


def dialog_headers(tenant_id: str) -> dict[str, str]:
    h = {"X-Tenant-ID": tenant_id}
    token = get_settings().dialog_service_token
    if token:
        h["X-Service-Token"] = token
    return h
