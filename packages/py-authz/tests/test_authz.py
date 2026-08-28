"""
Tabela-verdade do verificador canônico.

POR QUE ESTA SUÍTE EXISTE
=========================
O pacote nasceu em 2026-08-27 com dois consumidores e **zero teste**. Isso não era
lacuna enquanto ele era um ponto de extração recente; passou a ser no momento em que
seis serviços foram postos para depender dele (migração de 2026-08-28). Um verificador
compartilhado sem tabela-verdade própria não é "um lugar só" — é *seis comportamentos
que agora mudam juntos, sem ninguém saber quais são*.

Cada decisão canônica do cabeçalho de `plughub_authz/__init__.py` corresponde a uma
divergência MEDIDA entre as seis implementações. Esta suíte fixa cada uma, para que a
próxima edição do pacote tenha de dizer, em vermelho, qual delas está revertendo.

O QUE FARIA CADA GRUPO FICAR VERMELHO
=====================================
  · rank            — alguém "consertar" `write_only` para ficar acima de `read_only`
                      (era a ordem da `analytics-api`, o outlier registrado)
  · min_access ruim — voltar a `.get(min_access, 0)`, que faz um typo virar rank 0 e,
                      com isso, QUALQUER grant não-`none` passar
  · config vazio    — reintroduzir a degradação graciosa (ausência de grants = liberado)
  · 401 × 403       — colapsar "não sei quem é" com "sei e não pode"
  · segredo ausente — tratar "não consigo verificar" como "verificado"
  · portão OFF      — o único cuja asserção é sobre LOG: um portão desabilitado que não
                      avisa é indistinguível de um portão. Sem esta asserção, a
                      degradação muda volta e nada fica vermelho.
"""
from __future__ import annotations

import logging
import time

import jwt as pyjwt
import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

from plughub_authz import (
    ACCESS_RANK,
    abac_can,
    bearer_from_header,
    enforce_write,
    verify_user_jwt,
)

SECRET = "s" * 32
OTHER_SECRET = "x" * 32


class _Req:
    """Stub de Request com os headers REAIS do Starlette.

    Não é um dict de propósito: `Headers` é case-insensitive, e o gate lê
    `authorization` em minúsculas enquanto todo cliente manda `Authorization`. Um dict
    passaria no teste e falharia no deploy — a família "o teste não pode reprovar".
    """

    def __init__(self, **headers: str) -> None:
        self.headers = Headers({k.replace("_", "-"): v for k, v in headers.items()})


def _token(module_config: dict | None = None, *, secret: str = SECRET, exp_delta: int = 3600) -> str:
    payload: dict = {"sub": "u_1", "tenant_id": "demo"}
    if module_config is not None:
        payload["module_config"] = module_config
    payload["exp"] = int(time.time()) + exp_delta
    return pyjwt.encode(payload, secret, algorithm="HS256")


def _mc(access: str, *, module: str = "config", field: str = "platform") -> dict:
    return {module: {field: {"access": access, "scope": []}}}


# ── rank: read_only e write_only COLAPSAM ────────────────────────────────────

def test_rank_write_only_iguala_read_only():
    """A divergência 2: a `analytics-api` usa lista indexada, onde `write_only` é
    ESTRITAMENTE maior que `read_only`. O canônico colapsa os dois — são graus
    laterais do mesmo nível, não uma escada. Quem precisa escrever pede `read_write`."""
    assert ACCESS_RANK["write_only"] == ACCESS_RANK["read_only"] == 1
    assert ACCESS_RANK["none"] == 0
    assert ACCESS_RANK["read_write"] == 2


@pytest.mark.parametrize(
    ("access", "min_access", "esperado"),
    [
        ("none",       "read_only",  False),
        ("read_only",  "read_only",  True),
        ("write_only", "read_only",  True),
        ("read_write", "read_only",  True),
        # A consequência do colapso, e a única linha em que ele é observável:
        # um grant `write_only` NÃO satisfaz um endpoint de escrita.
        ("write_only", "read_write", False),
        ("read_only",  "read_write", False),
        ("read_write", "read_write", True),
    ],
)
def test_abac_can_tabela_verdade(access, min_access, esperado):
    assert abac_can({"module_config": _mc(access)}, "config", "platform", min_access) is esperado


# ── min_access desconhecido LEVANTA (divergência 4) ──────────────────────────

@pytest.mark.parametrize("ruim", ["readwrite", "read-write", "write", "", "READ_ONLY"])
def test_abac_can_min_access_desconhecido_levanta(ruim):
    """Era `.get(min_access, 0)` em três serviços: o typo virava rank 0 e então
    qualquer grant não-`none` passava. Fail-open por erro de digitação, mudo."""
    with pytest.raises(ValueError, match="min_access desconhecido"):
        abac_can({"module_config": _mc("read_only")}, "config", "platform", ruim)


# ── ausência NEGA, em todas as formas (divergência 3) ────────────────────────

@pytest.mark.parametrize(
    "claims",
    [
        {},                                              # sem module_config
        {"module_config": {}},                           # vazio
        {"module_config": None},                         # nulo
        {"module_config": {"config": {}}},               # módulo sem o campo
        {"module_config": {"outro": {"platform": {"access": "read_write"}}}},  # outro módulo
        {"module_config": {"config": {"platform": None}}},          # campo nulo
        {"module_config": {"config": {"platform": "read_write"}}},  # campo não-dict
        {"module_config": {"config": "read_write"}},                # módulo não-dict
        {"module_config": {"config": {"platform": {}}}},            # sem `access`
        {"module_config": {"config": {"platform": {"access": "admin"}}}},  # fora do domínio
    ],
)
def test_abac_can_ausencia_nega(claims):
    """Grant-first: ausência de grants NUNCA é autorização. A `evaluation-api` liberava
    quando `min_access is None`; aqui não existe esse ramo."""
    assert abac_can(claims, "config", "platform") is False


# ── verify_user_jwt ──────────────────────────────────────────────────────────

def test_verify_token_valido_devolve_payload():
    claims = verify_user_jwt(_token(_mc("read_write")), SECRET)
    assert claims is not None
    assert claims["sub"] == "u_1"
    assert claims["module_config"]["config"]["platform"]["access"] == "read_write"


@pytest.mark.parametrize(
    ("token", "secret"),
    [
        (None,                        SECRET),   # ausente
        ("",                          SECRET),   # vazio
        ("nao.e.um.jwt",              SECRET),   # malformado
        (_token(secret=OTHER_SECRET), SECRET),   # assinado com outro segredo
        (_token(exp_delta=-60),       SECRET),   # expirado
    ],
)
def test_verify_recusa_devolve_none(token, secret):
    assert verify_user_jwt(token, secret) is None


def test_verify_segredo_vazio_devolve_none_e_nao_verifica():
    """'Não consigo verificar' não é 'verificado'. O chamador é obrigado a tratar o
    None — e `enforce_write` o converte em 503, nunca em 403."""
    assert verify_user_jwt(_token(_mc("read_write")), "") is None


def test_verify_nao_aceita_alg_none():
    """Um token `alg: none` é a forma clássica de transformar decode em bypass."""
    forjado = pyjwt.encode(
        {"sub": "u", "module_config": _mc("read_write")}, key="", algorithm="none"
    )
    assert verify_user_jwt(forjado, SECRET) is None


# ── bearer_from_header ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("header", "esperado"),
    [
        ("Bearer abc",     "abc"),
        ("bearer abc",     "abc"),   # cliente que manda minúsculo
        ("Bearer   abc  ", "abc"),
        (None,             None),
        ("",               None),
        ("abc",            None),    # sem esquema
        ("Basic abc",      None),    # esquema errado
        ("Bearer",         None),    # sem valor
        ("Bearer    ",     None),    # valor em branco
    ],
)
def test_bearer_from_header(header, esperado):
    assert bearer_from_header(header) == esperado


# ── enforce_write: os desfechos ──────────────────────────────────────────────

def test_enforce_admin_token_passa_e_devolve_none():
    """None = 'quem decidiu foi o admin-token'. O chamador distingue isso de claims."""
    req = _Req(x_admin_token="tok")
    assert enforce_write(request=req, admin_token="tok", jwt_secret=SECRET,
                         module="config", field="platform") is None


def test_enforce_bearer_suficiente_devolve_claims():
    req = _Req(authorization="Bearer " + _token(_mc("read_write")))
    claims = enforce_write(request=req, admin_token="tok", jwt_secret=SECRET,
                           module="config", field="platform")
    assert claims is not None and claims["sub"] == "u_1"


def test_enforce_credencial_ausente_401():
    """401 e 403 respondem perguntas diferentes: 'não sei quem é' × 'sei, e não pode'.
    A `pricing-api` devolvia 403 aqui (divergência 5)."""
    with pytest.raises(HTTPException) as exc:
        enforce_write(request=_Req(), admin_token="tok", jwt_secret=SECRET,
                      module="config", field="platform")
    assert exc.value.status_code == 401


def test_enforce_token_invalido_401():
    with pytest.raises(HTTPException) as exc:
        enforce_write(request=_Req(authorization="Bearer lixo"), admin_token="tok",
                      jwt_secret=SECRET, module="config", field="platform")
    assert exc.value.status_code == 401


def test_enforce_grant_insuficiente_403():
    req = _Req(authorization="Bearer " + _token(_mc("read_only")))
    with pytest.raises(HTTPException) as exc:
        enforce_write(request=req, admin_token="tok", jwt_secret=SECRET,
                      module="config", field="platform")
    assert exc.value.status_code == 403
    assert "config.platform" in exc.value.detail


def test_enforce_sem_segredo_503_nao_403():
    """Sem segredo não se sabe se ele PODE — não se sabe nem quem é. 403 afirmaria
    uma decisão que não foi tomada."""
    req = _Req(authorization="Bearer " + _token(_mc("read_write")))
    with pytest.raises(HTTPException) as exc:
        enforce_write(request=req, admin_token="tok", jwt_secret="",
                      module="config", field="platform")
    assert exc.value.status_code == 503


def test_enforce_admin_token_errado_cai_no_bearer_e_nao_passa():
    """Testemunha negativa: header de admin PRESENTE mas errado não é atalho — cai no
    caminho Bearer, que sem credencial recusa."""
    with pytest.raises(HTTPException) as exc:
        enforce_write(request=_Req(x_admin_token="errado"), admin_token="tok",
                      jwt_secret=SECRET, module="config", field="platform")
    assert exc.value.status_code == 401


# ── portão DESABILITADO: a asserção é sobre o LOG ────────────────────────────

def test_enforce_portao_desabilitado_passa_MAS_avisa(caplog):
    """A única asserção desta suíte que é sobre log, e é a mais importante.

    `admin_token` vazio DESABILITA o portão — postura preservada dos serviços que já
    faziam isso, para que um deploy interno sem token não fique de pé sem conseguir
    escrever. Mas um portão inerte que não avisa é indistinguível de um portão: foi
    exatamente assim que a `calendar-api` aceitou `POST /v1/calendars` anônimo.

    O aviso tem de NOMEAR o grant que deixa de valer. 'using default values' genérico
    foi a frase que ninguém leu por meses (§ Configuration — Single Source Invariants).
    """
    with caplog.at_level(logging.WARNING, logger="plughub.authz"):
        assert enforce_write(request=_Req(), admin_token="", jwt_secret=SECRET,
                             module="config", field="platform",
                             what="escrita de calendario") is None

    avisos = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert avisos, "portao desabilitado passou EM SILENCIO — a degradacao muda voltou"
    msg = avisos[0].getMessage()
    assert "config" in msg and "platform" in msg, "aviso nao nomeia o grant: " + repr(msg)
    assert "escrita de calendario" in msg, "aviso nao nomeia a operacao: " + repr(msg)


def test_enforce_portao_ligado_nao_avisa(caplog):
    """Testemunha do lado oposto: se o aviso saísse sempre, ele viraria ruído e o
    teste acima passaria sem medir nada."""
    req = _Req(authorization="Bearer " + _token(_mc("read_write")))
    with caplog.at_level(logging.WARNING, logger="plughub.authz"):
        enforce_write(request=req, admin_token="tok", jwt_secret=SECRET,
                      module="config", field="platform")
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


# ══════════════════════════════════════════════════════════════════════════════
# `scope_id` — o recorte de CAPACIDADE por pool (D2, passo 6 · 2026-08-28)
# ══════════════════════════════════════════════════════════════════════════════
#
# Eixo distinto de `resolve_scope`: lá a pergunta é *"quais LINHAS eu vejo"*, aqui é
# *"posso exercer esta FUNÇÃO neste pool"*. O grant carrega `scope` ao lado de `access`.
#
# Estes casos foram portados da `evaluation-api`, que era a única casa a implementá-los.
# O ramo 3 (`scope` não-vazio + `scope_id is None` → passa) é HERDADO, não decidido —
# ver a docstring de `abac_can` e o registro no `TODO.md`.

def _grant(access="read_write", scope=None):
    fld = {"access": access}
    if scope is not None:
        fld["scope"] = scope
    return {"module_config": {"evaluation": {"curar": fld}}}


def test_scope_vazio_e_grant_GLOBAL():
    """Lista vazia = sem recorte. `scope_id` nomeado não muda nada."""
    assert abac_can(_grant(scope=[]), "evaluation", "curar", "read_write") is True
    assert abac_can(_grant(scope=[]), "evaluation", "curar", "read_write", "qualquer") is True


def test_scope_ausente_tambem_e_global():
    """Grant sem a chave `scope` — forma que existe no repositório."""
    assert abac_can(_grant(), "evaluation", "curar", "read_write", "qualquer") is True


def test_scope_com_o_pool_passa_nas_DUAS_grafias():
    """A UI grava `pool:x`; parte dos grants tem só `x`. Uma casa aceita as duas."""
    assert abac_can(_grant(scope=["pool:retencao"]), "evaluation", "curar", "read_write", "retencao") is True
    assert abac_can(_grant(scope=["retencao"]), "evaluation", "curar", "read_write", "retencao") is True


def test_scope_sem_o_pool_RECUSA():
    assert abac_can(_grant(scope=["pool:outro"]), "evaluation", "curar", "read_write", "retencao") is False


@pytest.mark.parametrize(
    "escopo,pedido",
    [
        # PREFIXO: `pool:retencao_humano` não satisfaz `retencao`.
        (["pool:retencao_humano"], "retencao"),
        # SUFIXO: `pool:sub_retencao` termina em `retencao` e MESMO ASSIM não satisfaz.
        # ⚠️ Este caso nasceu de uma mutação SOBREVIVENTE: trocar o casamento exato por
        # `endswith` passou pela suíte inteira, porque o caso de prefixo acima não podia
        # pegá-lo. Um alias de duas grafias convida exatamente a esse tipo de "quase
        # igual", e o modo de falha é ALARGAR escopo em silêncio.
        (["pool:sub_retencao"], "retencao"),
        (["retencao_humano"], "retencao"),
        (["sub_retencao"], "retencao"),
        # E o `pool:` só vale como PREFIXO exato, nunca no meio.
        (["x_pool:retencao"], "retencao"),
    ],
)
def test_alias_casa_EXATO_nunca_por_pedaco(escopo, pedido):
    assert abac_can(_grant(scope=escopo), "evaluation", "curar", "read_write", pedido) is False


def test_scope_nao_vazio_SEM_scope_id_passa_ramo_HERDADO():
    """⚠️ Ramo 3. `scope_id=None` significa *o recurso não nomeia escopo* (na
    evaluation-api sai de `campaign.pool_id`), não *esqueci de passar*. Portado
    literalmente no passo 6; a leitura oposta é defensável e está registrada."""
    assert abac_can(_grant(scope=["pool:outro"]), "evaluation", "curar", "read_write") is True


def test_escopo_nao_salva_grant_INSUFICIENTE():
    """A ordem importa: rank primeiro, escopo depois. Um pool que casa não pode
    promover um `read_only` a `read_write`."""
    g = _grant(access="read_only", scope=["pool:retencao"])
    assert abac_can(g, "evaluation", "curar", "read_write", "retencao") is False


def test_escopo_malformado_nao_vira_recorte_nem_libera_demais():
    """`scope` que não é lista é tratado como AUSENTE (global), não como erro — a
    forma canônica de ausência neste repositório. O que decide é o `access`."""
    g = {"module_config": {"evaluation": {"curar": {"access": "read_write", "scope": "pool:x"}}}}
    assert abac_can(g, "evaluation", "curar", "read_write", "x") is True
    g_none = {"module_config": {"evaluation": {"curar": {"access": "none", "scope": ["pool:x"]}}}}
    assert abac_can(g_none, "evaluation", "curar", "read_write", "x") is False
