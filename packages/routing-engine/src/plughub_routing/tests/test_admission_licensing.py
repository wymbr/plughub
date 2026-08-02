"""
test_admission_licensing.py — fatia 3: a admissão parou de somar as moedas.

**O QUE ESTE ARQUIVO PROVA, e por que ele nasce VERMELHO contra o código anterior.**

Até 2026-08-02 a admissão gateava TODA sessão contra

    shared_limit = {t}:quota:max_concurrent_sessions − Σ session_reservation

e `max_concurrent_sessions` era a SOMA de duas licenças que não se substituem
(no demo: 360 IA + 10 humanos = 370). Consequência medida: contato para pool HUMANO
recusado na porta (`shared_full` → outage) com humano ocioso do outro lado — e a licença
humana já era cobrada no login, então era gate duplo E na unidade errada.

`test_human_is_not_gated_by_the_mixed_pot` monta exatamente esse cenário: pote
configurado e ESGOTADO, e um contato para pool humano. Código antigo: `admitted=False,
cause="shared_full"`. Código novo: admitido.

**A armadilha que este arquivo evita de propósito.** Um teste que só afirmasse "humano é
admitido" passaria também num ambiente SEM LIMITE NENHUM configurado — verde por
ausência, que não distingue "o gate errado saiu" de "nunca houve gate". Por isso cada
cenário de admissão traz o CONTROLE POSITIVO junto: no mesmo fixture, com as mesmas
chaves, uma sessão de IA além de `C_ai` **é recusada**. Se a maquinaria estivesse
inerte, o controle positivo cairia e o arquivo inteiro se declararia inútil.

Teste de INTEGRAÇÃO: precisa de um Redis real. Pulado se indisponível.
"""
from __future__ import annotations

import os
import uuid

import pytest
import redis.asyncio as aioredis

from plughub_routing.admission import AdmissionController
from plughub_routing.models import PoolConfig

# Dual-read deliberado: o serviço define `PLUGHUB_REDIS_URL` no compose, e um teste que
# leia só `REDIS_URL` PULA dentro do container — verde que nunca rodou (aconteceu com 9
# testes do claim pull, 2026-07-30).
REDIS_URL = (
    os.environ.get("REDIS_URL")
    or os.environ.get("PLUGHUB_REDIS_URL")
    or "redis://localhost:6379"
)


def _controller(client) -> AdmissionController:
    """Constrói o controller nas DUAS assinaturas — e isso é requisito, não gentileza.

    A fatia 3 tirou `pool_registry` do construtor (existia só para somar reservas).
    Sem esta tolerância, rodar este arquivo contra o código ANTERIOR estoura
    `TypeError` na fixture: o resultado seriam 10 **erros de coleta**, não 10 falhas —
    e erro de construção não diz nada sobre a REGRA. Um teste que nem consegue
    instanciar o código antigo é incapaz de demonstrar que reprova contra ele, que é
    justamente a única coisa que separa este arquivo de um verde decorativo.

    O fake devolve `[]` em `list_pools`, então `Σ session_reservation = 0` e o
    `shared_limit` do código antigo é o `C` inteiro — exatamente o cenário que os
    testes montam.
    """
    try:
        return AdmissionController(client)
    except TypeError:
        class _NoPools:
            async def list_pools(self, tenant_id: str) -> list:
                return []
        return AdmissionController(client, _NoPools())   # type: ignore[call-arg]


def _pool(pool_id: str, kind: str) -> PoolConfig:
    return PoolConfig(
        pool_id=pool_id, tenant_id="ignored",
        channel_types=["webchat"], sla_target_ms=300_000,
        agent_kind=kind,
    )


@pytest.fixture
async def env():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"Redis indisponível em {REDIS_URL} — teste de integração pulado")
    tenant = f"t_adm_{uuid.uuid4().hex[:8]}"
    adm    = _controller(client)
    try:
        yield adm, client, tenant
    finally:
        async for k in client.scan_iter(f"{tenant}:*"):
            await client.delete(k)
        async for k in client.scan_iter("session:t_adm_*"):
            await client.delete(k)
        await client.aclose()


# ── Cenário central da fatia 3 ────────────────────────────────────────────────

async def test_human_is_not_gated_by_the_mixed_pot(env):
    """Pote configurado E ESGOTADO; contato humano entra assim mesmo.

    O pote é montado do jeito que o código ANTIGO o lia — SET `{t}:admission:shared`
    cheio até `max_concurrent_sessions`. Contra aquele código este teste reprova com
    `cause="shared_full"`; contra o novo, o SET é irrelevante porque não existe mais
    balde de sessão para pool humano.
    """
    adm, client, tenant = env

    # C misto configurado e completamente consumido (2 de 2).
    await client.set(f"{tenant}:quota:max_concurrent_sessions", "2")
    await client.sadd(f"{tenant}:admission:shared", "sess-antiga-1", "sess-antiga-2")

    decision = await adm.admit(tenant, "sess-humana", _pool("retencao_humano", "human"), "retencao_humano")

    assert decision.admitted is True, (
        f"contato humano recusado com cause={decision.cause!r} — a licença humana é por "
        "LOGIN (cobrada no agent_login), nunca por sessão contra um pote que soma IA "
        "com humano"
    )
    # E não deixa rastro no balde de licença: humano não debita C_ai.
    assert await client.scard(f"{tenant}:admission:kind:ai") == 0
    assert await client.hgetall(f"{tenant}:admission:ai_pools") == {}


async def test_ai_is_still_gated_by_its_own_currency(env):
    """CONTROLE POSITIVO do teste acima — sem ele, aquele verde não distingue nada.

    Mesmo fixture, mesmas chaves: `C_ai` esgotado recusa na porta com `cause="quota"`.
    Se este cair, a maquinaria de admissão está inerte e o cenário humano passou por
    ausência de limite, não por decisão.
    """
    adm, client, tenant = env

    await client.set(f"{tenant}:quota:capacity:ai_agent", "1")
    first = await adm.admit(tenant, "sess-ia-1", _pool("sac_ia", "ai"), "sac_ia")
    assert first.admitted is True

    second = await adm.admit(tenant, "sess-ia-2", _pool("sac_ia", "ai"), "sac_ia")
    assert second.admitted is False
    assert second.cause == "quota"
    assert second.limit == 1 and second.current == 2


async def test_ai_ceiling_is_c_ai_not_the_mixed_pot(env):
    """O teto de IA é `C_ai`, e um pote misto GENEROSO não o afrouxa.

    Cenário: `max_concurrent_sessions = 370` (folgado) e `C_ai = 1`. Antes, as duas
    checagens rodavam em série e a resposta era a mesma; o valor deste teste é fixar
    que a que sobrou é a da MOEDA, não a da soma — se alguém reintroduzir o pote como
    "teto geral", este teste continua verde e o de cima é que denuncia. Por isso ele
    vem acompanhado do simétrico abaixo.
    """
    adm, client, tenant = env

    await client.set(f"{tenant}:quota:max_concurrent_sessions", "370")
    await client.set(f"{tenant}:quota:capacity:ai_agent", "1")

    await adm.admit(tenant, "sess-ia-1", _pool("sac_ia", "ai"), "sac_ia")
    blocked = await adm.admit(tenant, "sess-ia-2", _pool("sac_ia", "ai"), "sac_ia")
    assert blocked.admitted is False and blocked.cause == "quota"


async def test_mixed_pot_alone_gates_nobody(env):
    """Simétrico: com `max_concurrent_sessions` ESGOTADO e `C_ai` ausente, ninguém é
    recusado — nem IA. É a afirmação de que o pote deixou de ser portão, e não apenas
    de que humano escapou dele."""
    adm, client, tenant = env

    await client.set(f"{tenant}:quota:max_concurrent_sessions", "1")
    await client.sadd(f"{tenant}:admission:shared", "sess-antiga-1")

    ai    = await adm.admit(tenant, "sess-ia", _pool("sac_ia", "ai"), "sac_ia")
    human = await adm.admit(tenant, "sess-hum", _pool("retencao_humano", "human"), "retencao_humano")
    assert ai.admitted is True and human.admitted is True


# ── Instrumentação reapontada (decisão B) ─────────────────────────────────────

async def test_attribution_hash_follows_the_ai_bucket(env):
    """`{t}:admission:ai_pools` atribui por pool quem debita `C_ai`.

    Substitui `shared_pools`, que atribuía o pote misto. O nome importa: mantido, ele
    faria o Monitor continuar exibindo "capacidade compartilhada" para um balde que
    virou licença de IA — número plausível descrevendo outra coisa.
    """
    adm, client, tenant = env

    await adm.admit(tenant, "s1", _pool("sac_ia", "ai"), "sac_ia")
    await adm.admit(tenant, "s2", _pool("nps_ia", "ai"), "nps_ia")
    await adm.admit(tenant, "s3", _pool("retencao_humano", "human"), "retencao_humano")

    assert await client.hgetall(f"{tenant}:admission:ai_pools") == {
        "s1": "sac_ia", "s2": "nps_ia",
    }, "sessão humana não pode aparecer na atribuição de licença de IA"
    assert await client.scard(f"{tenant}:admission:kind:ai") == 2


async def test_escalation_ai_to_human_releases_the_licence(env):
    """Sessão que migra de pool de IA para pool humano DEVOLVE a licença.

    Sem isto, a licença ficaria presa até o fechamento e `C_ai` derivaria para cima —
    o mesmo modo de falha do `active_count` removido na fatia 2 (contador que só sobe
    é indistinguível de sistema ocupado).
    """
    adm, client, tenant = env

    await client.set(f"{tenant}:quota:capacity:ai_agent", "1")
    await adm.admit(tenant, "s1", _pool("sac_ia", "ai"), "sac_ia")
    assert await client.scard(f"{tenant}:admission:kind:ai") == 1

    await adm.admit(tenant, "s1", _pool("retencao_humano", "human"), "retencao_humano")
    assert await client.scard(f"{tenant}:admission:kind:ai") == 0
    assert await client.hgetall(f"{tenant}:admission:ai_pools") == {}

    # E a vaga liberada é reutilizável — a prova de que o SREM não foi cosmético.
    again = await adm.admit(tenant, "s2", _pool("sac_ia", "ai"), "sac_ia")
    assert again.admitted is True


async def test_active_session_migration_into_saturated_ai_fails_open(env):
    """Migração de sessão ATIVA para IA saturada nunca derruba o contato.

    Regra pré-existente, preservada de propósito: recusar na porta é degradação
    aceitável; recusar no meio do atendimento é perder o cliente. A sessão mantém a
    atribuição de ORIGEM (sem meio-estado no tracking).
    """
    adm, client, tenant = env

    await client.set(f"{tenant}:quota:capacity:ai_agent", "1")
    await adm.admit(tenant, "ocupante", _pool("sac_ia", "ai"), "sac_ia")
    # `s1` já é uma sessão viva, atribuída a um pool humano.
    await adm.admit(tenant, "s1", _pool("retencao_humano", "human"), "retencao_humano")

    migrated = await adm.admit(tenant, "s1", _pool("nps_ia", "ai"), "nps_ia")
    assert migrated.admitted is True, "sessão ativa não pode virar outage por teto"
    assert await client.scard(f"{tenant}:admission:kind:ai") == 1, (
        "fail-open não pode CONTAR a sessão no balde: seria estourar o teto pela porta "
        "dos fundos"
    )


async def test_release_frees_the_licence_for_the_mute_queue(env):
    """`release()` (fila muda) devolve a licença de IA e limpa a atribuição."""
    adm, client, tenant = env

    await adm.admit(tenant, "s1", _pool("sac_ia", "ai"), "sac_ia")
    await adm.release(tenant, "s1")

    assert await client.scard(f"{tenant}:admission:kind:ai") == 0
    assert await client.hgetall(f"{tenant}:admission:ai_pools") == {}
    assert await client.get(f"{tenant}:admission:kind_member:s1") is None


async def test_reconcile_releases_closed_sessions_and_heals_the_hash(env):
    """O reconciler libera sessão fechada e cura entrada órfã do HASH.

    A higiene do HASH existe porque SET e HASH são dois writes: um crash entre eles
    deixaria `Σ fatias ≠ SCARD`, e a tela mostraria uma atribuição sem lastro.
    """
    adm, client, tenant = env

    await adm.admit(tenant, "s1", _pool("sac_ia", "ai"), "sac_ia")
    await adm.admit(tenant, "s2", _pool("sac_ia", "ai"), "sac_ia")
    await client.setex("session:s1:closed", 60, "flow_complete")
    # Órfã: presente no HASH, ausente do SET (simula o crash entre os dois writes).
    await client.hset(f"{tenant}:admission:ai_pools", "s-fantasma", "sac_ia")

    released = await adm.reconcile()

    assert released >= 1
    assert await client.smembers(f"{tenant}:admission:kind:ai") == {"s2"}
    assert await client.hgetall(f"{tenant}:admission:ai_pools") == {"s2": "sac_ia"}


async def test_no_ceiling_configured_admits_but_still_tracks(env):
    """Sem `C_ai`, não há gate — mas o tracking continua, senão o Monitor cega.

    Fail-open é decisão consciente (tenant sem pricing não deve ter contato recusado);
    parar de contar junto seria transformar "sem teto" em "sem medição".
    """
    adm, client, tenant = env

    for i in range(3):
        d = await adm.admit(tenant, f"s{i}", _pool("sac_ia", "ai"), "sac_ia")
        assert d.admitted is True
    assert await client.scard(f"{tenant}:admission:kind:ai") == 3


async def test_has_headroom_mirrors_admit(env):
    """`has_headroom` é a versão READ-ONLY do `admit` — e tem de concordar com ele.

    Divergir aqui é o defeito clássico de duas implementações da mesma regra: o drain
    re-publicaria um contato que a admissão recusa em seguida, num laço de
    rejeita→re-enfileira a cada ciclo.
    """
    adm, client, tenant = env

    await client.set(f"{tenant}:quota:capacity:ai_agent", "1")
    assert await adm.has_headroom(tenant, "sac_ia", agent_kind="ai") is True
    await adm.admit(tenant, "s1", _pool("sac_ia", "ai"), "sac_ia")
    assert await adm.has_headroom(tenant, "sac_ia", agent_kind="ai") is False
    # Pool humano nunca tem teto de sessão a consultar.
    assert await adm.has_headroom(tenant, "retencao_humano", agent_kind="human") is True
