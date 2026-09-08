"""test_content_capability_axis.py — MOD-07 (corte #4 da D6, 2026-09-08)

O QUE ESTE ARQUIVO PROVA
------------------------
`authorize_session_scope` decide DOIS eixos, e eles são independentes:

  · ESCOPO      — *quais sessões eu alcanço* (`accessible_pools`)
  · CAPACIDADE  — *que função eu exerço sobre ela* (`contacts.<campo>`)

Até a MOD-07 só existia o primeiro nesta casa, e a consequência foi medida ao vivo:
um token com `contacts.monitorar` e **sem** `contacts.visualizar` lia a transcrição
inteira de um contato dos seus pools (200). O campo existia, era oferecido na tela de
Acesso, e não era exigido em backend nenhum — gate decorativo é pior que gate nenhum,
porque quem concede acredita ter negado.

POR QUE UNIDADE ALÉM DO PROBE
-----------------------------
O `probe_session_content_scope.sh` mede isto ao vivo (ramo A2), e é ele quem prova que
a ROTA está guardada. O que ele não consegue afirmar barato são as duas decisões de
ORDEM e de ISENÇÃO abaixo — a primeira porque um 403 não diz quantas consultas houve
antes dele, a segunda porque exige o token de serviço do ambiente.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from plughub_analytics_api.pool_auth import PoolPrincipal, authorize_session_scope


def _user(pools, module_config):
    return PoolPrincipal(
        accessible_pools=pools, tenant_id="tenant_demo", sub="u1",
        module_config=module_config,
    )


class _RedisFalso:
    """Conta as consultas: é a testemunha de que a capacidade decide ANTES do escopo."""

    def __init__(self, pools):
        self.pools = pools
        self.consultas = 0


@pytest.fixture(autouse=True)
def _sem_fontes(monkeypatch):
    """Neutraliza as fontes de pool; cada teste declara o que a sua pergunta usa."""
    async def _vivo(redis, tenant_id, session_id):
        redis.consultas += 1
        return set(redis.pools)

    monkeypatch.setattr(
        "plughub_analytics_api.pool_auth.resolve_live_session_pools", _vivo)


@pytest.mark.asyncio
async def test_capacidade_faltando_recusa_mesmo_com_escopo_certo():
    """O par do caso feliz: MESMO escopo, campo faltando ⇒ 403 nomeando o campo."""
    p = _user(["sac_ia"], {"contacts": {"monitorar": {"access": "read_write"}}})
    with pytest.raises(HTTPException) as e:
        await authorize_session_scope(
            p, "tenant_demo", "s1", rota="t", campo="transcricao",
            redis=_RedisFalso(["sac_ia"]))
    assert e.value.status_code == 403
    assert "contacts.transcricao" in str(e.value.detail)


@pytest.mark.asyncio
async def test_com_o_campo_e_o_escopo_passa():
    """CONTROLE POSITIVO. Sem ele, um guard que negasse tudo passaria no de cima."""
    p = _user(["sac_ia"], {"contacts": {"transcricao": {"access": "read_only"}}})
    await authorize_session_scope(
        p, "tenant_demo", "s1", rota="t", campo="transcricao",
        redis=_RedisFalso(["sac_ia"]))


@pytest.mark.asyncio
async def test_a_capacidade_decide_ANTES_de_consultar_pools():
    """Ordem: a recusa por capacidade não pode custar Redis + ClickHouse.

    É a mesma correção que o guard de rank levou horas antes (o master pagava o custo
    do catálogo antes do curto-circuito). Aqui a testemunha é o contador de consultas.
    """
    redis = _RedisFalso(["sac_ia"])
    p = _user(["sac_ia"], {})
    with pytest.raises(HTTPException):
        await authorize_session_scope(
            p, "tenant_demo", "s1", rota="t", campo="transcricao", redis=redis)
    assert redis.consultas == 0, "consultou pools para recusar por capacidade"


@pytest.mark.asyncio
async def test_module_config_vazio_NEGA():
    """Grant-first: ausência de grants nunca é autorização — a mesma inversão de
    `accessible_pools`, pela mesma razão."""
    p = _user(["sac_ia"], {})
    with pytest.raises(HTTPException) as e:
        await authorize_session_scope(
            p, "tenant_demo", "s1", rota="t", campo="transcricao",
            redis=_RedisFalso(["sac_ia"]))
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_principal_de_SERVICO_nao_tem_capacidade_a_conferir():
    """`module_config is None` = serviço/open_access: irrestrito por IDENTIDADE.

    ⚠️ É diferente de `{}` (usuário sem grants), e confundir os dois seria transformar
    "não sei o que ele pode" em "pode tudo" — exatamente o que o resto deste arquivo
    existe para impedir.
    """
    svc = PoolPrincipal(accessible_pools=None, tenant_id=None, sub="service:evaluation-api")
    await authorize_session_scope(svc, "tenant_demo", "s1", rota="t", campo="transcricao")


@pytest.mark.asyncio
async def test_sem_campo_declarado_o_eixo_de_capacidade_NAO_roda():
    """Rota que não declara campo continua decidindo só escopo.

    Testemunha de que o eixo novo é OPT-IN por call site: sem ela, alguém poderia
    "endurecer" o default e quebrar em silêncio uma rota que ainda não foi classificada.
    """
    p = _user(["sac_ia"], {})
    await authorize_session_scope(
        p, "tenant_demo", "s1", rota="t", redis=_RedisFalso(["sac_ia"]))
