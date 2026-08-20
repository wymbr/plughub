"""
test_audit_gate.py — o portão ABAC de `/v1/audit/*`.

Por que estes casos existem em pytest e não no probe: o ambiente demo roda com
`PLUGHUB_ANALYTICS_OPEN_ACCESS=true`, e o bypass devolve no PRIMEIRO ramo de
`_check_audit_access`. Lá, um 200 é compatível com "gate correto" **e** com "gate
inexistente" — que é exatamente o estado em que o código estava até 2026-08-22,
quando o gate existia só no docstring. Um teste que não pode reprovar não vale
nada; este arquivo é a metade que o probe declara não julgar.

O caso que denuncia a regressão é o `test_token_valido_sem_modulo_audit_recusa`:
antes do conserto ele passaria por engano, porque `optional_pool_principal`
devolve 401 só para token MALFORMADO — um token bem-formado de um usuário sem o
módulo `audit` entrava.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

# PyJWT — a mesma biblioteca que `pool_auth.py` usa neste pacote. NÃO `python-jose`:
# ela é do auth-api e não está instalada aqui. Ver o comentário em `_audit_actor`.
import jwt

from plughub_analytics_api.audit import (
    AuditDenied,
    _check_audit_access,
    _has_abac,
)

SECRET = "segredo-de-teste"


class _Req:
    """Request mínimo — só o que `_audit_actor` lê."""

    def __init__(self, auth: str | None = None):
        self.headers = {"authorization": auth} if auth else {}
        self.query_params = {}


def _token(module_config: dict, sub: str = "u1") -> str:
    return jwt.encode(
        {
            "sub": sub,
            "module_config": module_config,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        SECRET,
        algorithm="HS256",
    )


@pytest.fixture
def settings(monkeypatch):
    """Aponta o módulo para um settings controlado, com cache limpo."""
    from plughub_analytics_api import config as cfg

    class S:
        analytics_open_access = False
        auth_jwt_secret = SECRET

    cfg.get_settings.cache_clear()
    monkeypatch.setattr(cfg, "get_settings", lambda: S())
    return S


# ── _has_abac — tabela-verdade da hierarquia ─────────────────────────────────

@pytest.mark.parametrize(
    "access,esperado",
    [
        ("read_write", True),
        ("read_only", True),
        # `write_only` está ACIMA de read_only na hierarquia declarada em
        # permissions.ts. Inventar uma segunda ordem aqui faria o gate da API
        # discordar do gate da tela, e gate que discorda de gate é gate nenhum.
        ("write_only", True),
        ("none", False),
        ("valor_inventado", False),
    ],
)
def test_has_abac_hierarquia(access, esperado):
    mc = {"audit": {"sessions": {"access": access, "scope": []}}}
    assert _has_abac(mc, "audit", "sessions") is esperado


@pytest.mark.parametrize(
    "mc",
    [
        {},                                        # sem module_config
        {"audit": {}},                             # módulo sem o campo
        {"audit": {"mcp_calls": {"access": "read_write"}}},  # OUTRO campo
        {"audit": {"sessions": "read_write"}},     # forma errada (string, não dict)
        {"contacts": {"sessions": {"access": "read_write"}}},  # outro MÓDULO
    ],
)
def test_has_abac_ausencias_recusam(mc):
    """Ausência nunca libera. Inclui o caso do campo VIZINHO concedido."""
    assert _has_abac(mc, "audit", "sessions") is False


# ── _check_audit_access — os quatro ramos, cada um nomeado ───────────────────

def test_open_access_libera_e_NOMEIA_o_ator(settings, monkeypatch):
    from plughub_analytics_api import config as cfg

    class S:
        analytics_open_access = True
        auth_jwt_secret = SECRET

    monkeypatch.setattr(cfg, "get_settings", lambda: S())
    sub, kind = _check_audit_access(_Req(), "sessions")
    # O ator TEM de sair como `open_access`, não como usuário anônimo: a trilha
    # precisa distinguir acesso de demo de acesso autenticado.
    assert kind == "open_access"
    assert sub == ""


def test_sem_credencial_recusa(settings):
    with pytest.raises(AuditDenied):
        _check_audit_access(_Req(), "sessions")


def test_sem_segredo_configurado_RECUSA(monkeypatch):
    """
    Postura oposta à do `pool_auth`, e de propósito: lá, não poder verificar o
    token degrada ABERTO (escopo de leitura operacional); aqui é dado pessoal sob
    LGPD. Identidade não tem fallback.
    """
    from plughub_analytics_api import config as cfg

    class S:
        analytics_open_access = False
        auth_jwt_secret = ""

    monkeypatch.setattr(cfg, "get_settings", lambda: S())
    with pytest.raises(AuditDenied):
        _check_audit_access(_Req(f"Bearer {_token({'audit': {'sessions': {'access': 'read_write'}}})}"), "sessions")


def test_token_valido_sem_modulo_audit_recusa(settings):
    """
    ⚠️ O caso que o defeito escondia. O token é VÁLIDO e bem-formado — só não tem
    o módulo `audit`. Antes do conserto isto retornava 200: `optional_pool_principal`
    só produz 401 para token malformado, o que é autenticação, nunca autorização.
    """
    tok = _token({"contacts": {"operacao": {"access": "read_write"}}})
    with pytest.raises(AuditDenied):
        _check_audit_access(_Req(f"Bearer {tok}"), "sessions")


def test_token_com_modulo_audit_libera(settings):
    tok = _token({"audit": {"sessions": {"access": "read_only", "scope": []}}})
    sub, kind = _check_audit_access(_Req(f"Bearer {tok}"), "sessions")
    assert (sub, kind) == ("u1", "user")


def test_campo_e_especifico(settings):
    """`audit.sessions` concedido NÃO libera `audit.mcp_calls`."""
    tok = _token({"audit": {"sessions": {"access": "read_write"}}})
    req = _Req(f"Bearer {tok}")
    assert _check_audit_access(req, "sessions")[1] == "user"
    with pytest.raises(AuditDenied):
        _check_audit_access(req, "mcp_calls")


def test_token_malformado_recusa(settings):
    with pytest.raises(AuditDenied):
        _check_audit_access(_Req("Bearer nao.e.um.jwt"), "sessions")
