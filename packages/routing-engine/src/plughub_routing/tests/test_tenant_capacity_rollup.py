"""
test_tenant_capacity_rollup.py — F4: agregação por recurso DISTINTO (defeito C).

**O defeito.** `Σ available(pool)` conta o mesmo recurso uma vez por pool. Um humano
`max_concurrent 3` logado em 3 pools contribui 3 em cada linha e a soma diz 9 para um
recurso de 3 vagas. Medido ao vivo em 2026-07-31 com 2 pools: soma 6, verdade 3.

**Por que não dá para consertar na linha do pool.** A linha está CERTA: aquele pool
realmente alcança 3 vagas. O que está errado é somar — e a informação de sobreposição
(quais pools compartilham qual recurso) simplesmente não está nas linhas. Daí a segunda
superfície: um rollup sobre instâncias DISTINTAS.

**Por tipo de licença, nunca um número só.** Humano e IA não se substituem. Um escalar
único repetiria a falácia de aditividade um nível acima: em vez de contar o mesmo
recurso duas vezes, somaria recursos que não são intercambiáveis — e "há 5 agentes"
viraria resposta para "posso atender este cliente por voz humana?".

**Como estes testes reprovam.** O primeiro é a linha de base medida: a soma das linhas
dá 6, o rollup tem de dizer 3. Contra qualquer implementação que agregue por pool em vez
de por instância distinta, ele reprova. Os demais prendem: separação por tipo (nenhum
campo escalar somando as moedas), `by_channel` deduplicado, `pools_available` sobrevivendo
como grandeza aditiva legítima, e o balde `unknown` para config contraditória — que é
publicado, nunca dobrado em human/ai.

Teste de INTEGRAÇÃO: precisa de um Redis real.
    REDIS_URL=redis://redis:6379 pytest test_tenant_capacity_rollup.py
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
import redis.asyncio as aioredis

from plughub_routing.models import AgentInstance
from plughub_routing.registry import (
    InstanceRegistry,
    _pool_config_key,
    _pool_instances_key,
    _pool_set_key,
    _tenant_capacity_key,
)


REDIS_URL = (
    os.environ.get("REDIS_URL")
    or os.environ.get("PLUGHUB_REDIS_URL")
    or "redis://localhost:6379"
)

# A linha de base medida em 2026-07-31: um humano de 3 vagas em dois pools irmãos.
HUMAN_POOLS = ["retencao_humano", "retencao_humano-int"]
CAPACITY    = 3


@pytest.fixture
async def env():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"Redis indisponível em {REDIS_URL} — teste de integração pulado")
    reg    = InstanceRegistry(client)
    tenant = f"t_roll_{uuid.uuid4().hex[:8]}"
    try:
        yield reg, client, tenant
    finally:
        async for k in client.scan_iter(f"{tenant}:*"):
            await client.delete(k)
        await client.aclose()


async def _pool(client, tenant, pool_id, kind, channels):
    """Pool no cache de config do routing-engine (escrito pelo kafka_listener na vida
    real). `agent_kind` é a autoridade canônica de tipo — sem ele o rollup não tem como
    separar as moedas, e é exatamente isso que o teste do balde `unknown` cobra."""
    await client.sadd(_pool_set_key(tenant), pool_id)
    await client.set(_pool_config_key(tenant, pool_id), json.dumps({
        "pool_id": pool_id, "agent_kind": kind, "channel_types": channels,
    }))


async def _login(reg, client, tenant, instance, pools, capacity, kind="human"):
    await reg.set_instance(AgentInstance(
        instance_id=instance, agent_type_id=kind, tenant_id=tenant,
        pools=list(pools), execution_model="stateful",
        max_concurrent=capacity, current_sessions=0, state="ready",
        source="human_login" if kind == "human" else "seed",
    ))
    for pool in pools:
        await client.sadd(_pool_instances_key(tenant, pool), instance)


async def _sum_of_pool_lines(reg, client, tenant, pools) -> int:
    """O número ERRADO, computado do jeito que a tela computava — para o teste medir a
    distância em vez de afirmá-la."""
    total = 0
    for pool in pools:
        await reg.write_pool_snapshot(
            tenant_id=tenant, pool_id=pool,
            sla_target_ms=480_000, channel_types=["whatsapp"],
        )
        snap = json.loads(await client.get(f"{tenant}:pool:{pool}:snapshot"))
        total += snap["available"]
    return total


# ── 1. O defeito medido ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_shared_resource_is_counted_once_not_once_per_pool(env):
    """A linha de base de 2026-07-31, reproduzida: soma das linhas 6, verdade 3."""
    reg, client, tenant = env
    inst = f"human-{uuid.uuid4().hex[:6]}"
    for p in HUMAN_POOLS:
        await _pool(client, tenant, p, "human", ["whatsapp"])
    await _login(reg, client, tenant, inst, HUMAN_POOLS, CAPACITY)

    somado = await _sum_of_pool_lines(reg, client, tenant, HUMAN_POOLS)
    assert somado == CAPACITY * len(HUMAN_POOLS), (
        f"setup: a soma das linhas deveria reproduzir o defeito ({somado})"
    )

    roll = await reg.refresh_tenant_capacity(tenant, force=True)
    assert roll, "rollup não foi computado — nada a julgar"
    human = roll["by_kind"]["human"]
    assert human["available"] == CAPACITY, (
        f"rollup diz available={human['available']}, mas o recurso tem {CAPACITY} vagas "
        f"(a soma das linhas dizia {somado} — contando o mesmo humano uma vez por pool)"
    )
    assert human["instances"] == 1, "o mesmo recurso apareceu mais de uma vez"
    assert human["total_capacity"] == CAPACITY


@pytest.mark.asyncio
async def test_consumed_slot_discounts_once_across_all_pools(env):
    """Vaga consumida num pool desconta do recurso — uma vez, não por pool."""
    reg, client, tenant = env
    inst = f"human-{uuid.uuid4().hex[:6]}"
    for p in HUMAN_POOLS:
        await _pool(client, tenant, p, "human", ["whatsapp"])
    await _login(reg, client, tenant, inst, HUMAN_POOLS, CAPACITY)

    assert await reg.claim_instance(
        tenant, inst, "ses-1", None, CAPACITY, pool_id=HUMAN_POOLS[0]
    ) == 1

    roll = await reg.refresh_tenant_capacity(tenant, force=True)
    human = roll["by_kind"]["human"]
    assert human["used"] == 1, f"used={human['used']}, esperava 1"
    assert human["available"] == CAPACITY - 1, (
        f"available={human['available']}: a vaga consumida descontou {CAPACITY - human['available']} "
        "vez(es) — deveria descontar exatamente uma, o recurso é um só"
    )


# ── 2. Moedas não-fungíveis ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_human_and_ai_are_never_summed(env):
    """Cada tipo tem seu próprio balde, e NÃO existe escalar somando os dois.

    Um `available` no topo responderia "há 5 agentes" para quem perguntou "posso
    atender por voz humana?". A ausência do campo é a garantia — se alguém conseguir
    ler um número único de disponibilidade, o rollup regrediu.
    """
    reg, client, tenant = env
    await _pool(client, tenant, "retencao_humano", "human", ["whatsapp"])
    await _pool(client, tenant, "sac_ia",          "ai",    ["webchat"])
    await _login(reg, client, tenant, "human-1", ["retencao_humano"], 3, kind="human")
    await _login(reg, client, tenant, "ia-1",    ["sac_ia"],          20, kind="ai")

    roll = await reg.refresh_tenant_capacity(tenant, force=True)
    assert roll["by_kind"]["human"]["available"] == 3
    assert roll["by_kind"]["ai"]["available"] == 20
    assert "available" not in roll, (
        "existe um `available` escalar no topo do rollup — humano e IA não se "
        "substituem, e somá-los é a falácia de aditividade um nível acima"
    )
    assert "total_capacity" not in roll, "idem para capacidade total"


@pytest.mark.asyncio
async def test_channel_availability_is_per_kind_and_deduplicated(env):
    """`by_channel`: instância serve o canal se ALGUM pool seu o declara — e conta uma
    vez, mesmo servindo o canal por dois pools diferentes."""
    reg, client, tenant = env
    inst = f"human-{uuid.uuid4().hex[:6]}"
    # Os dois pools declaram whatsapp: o recurso serve o canal por duas portas.
    for p in HUMAN_POOLS:
        await _pool(client, tenant, p, "human", ["whatsapp"])
    await _login(reg, client, tenant, inst, HUMAN_POOLS, CAPACITY)

    roll = await reg.refresh_tenant_capacity(tenant, force=True)
    wa = roll["by_kind"]["human"]["by_channel"]["whatsapp"]
    assert wa["available"] == CAPACITY, (
        f"whatsapp available={wa['available']}: o recurso foi contado uma vez por PORTA"
    )
    assert wa["instances"] == 1


@pytest.mark.asyncio
async def test_by_channel_is_a_projection_not_a_partition(env):
    """Somar `by_channel` entre canais EXCEDE o total do tipo — e isso é correto.

    Uma instância que serve dois canais conta nos dois. O teste fixa a propriedade para
    que ninguém "conserte" o excesso dividindo o recurso entre canais (inventaria
    frações de vaga) nem passe a somar os canais achando que particionam. Medido no
    tenant real: 275 + 286 + 67 = 628 para 353 instâncias.
    """
    reg, client, tenant = env
    inst = f"human-{uuid.uuid4().hex[:6]}"
    # UM pool, DOIS canais: o mesmo recurso atende pelos dois.
    await _pool(client, tenant, "multi", "human", ["whatsapp", "webchat"])
    await _login(reg, client, tenant, inst, ["multi"], CAPACITY)

    roll = await reg.refresh_tenant_capacity(tenant, force=True)
    human = roll["by_kind"]["human"]
    assert human["available"] == CAPACITY, "o total do tipo tem de ser o deduplicado"
    soma_canais = sum(c["available"] for c in human["by_channel"].values())
    assert soma_canais == 2 * CAPACITY, (
        f"Σ by_channel={soma_canais}: cada canal deve enxergar as {CAPACITY} vagas "
        "inteiras (projeção). Se virar partição, alguém dividiu o recurso entre canais."
    )
    assert soma_canais > human["available"], (
        "by_channel deixou de exceder o total — virou partição, e a soma entre canais "
        "passa a parecer válida"
    )


@pytest.mark.asyncio
async def test_pools_available_survives_as_an_additive_count_but_per_kind(env):
    """`pools_available` continua sendo soma — e deve continuar. Mas POR TIPO.

    Duas afirmações num teste só porque uma sem a outra passa por motivo errado:

    (a) a contagem é aditiva de propósito. Não é a mesma grandeza que `available`:
        responde "há por onde entrar?", e duas portas para o mesmo recurso são duas
        portas. Substituí-la pelo número deduplicado trocaria um erro por perda de
        informação.
    (b) dentro de `by_kind.human` ela só pode contar pools HUMANOS. Contar por canal
        e copiar o mesmo número em cada balde reintroduz a fungibilidade no campo
        vizinho ao que existe para separá-la — "há 19 portas humanas" quando há duas.

    A versão anterior deste teste tinha só (a), num tenant onde TODOS os pools eram
    humanos: passava sem discriminar. O defeito só apareceu no rollup do tenant real
    (2026-08-02: human/whatsapp e ai/whatsapp com 19 idênticos). Daí o pool de IA no
    mesmo canal — é ele que faz a asserção morder.
    """
    reg, client, tenant = env
    inst = f"human-{uuid.uuid4().hex[:6]}"
    for p in HUMAN_POOLS:
        await _pool(client, tenant, p, "human", ["whatsapp"])
    await _pool(client, tenant, "sac_ia", "ai", ["whatsapp"])   # MESMO canal, outro tipo
    await _login(reg, client, tenant, inst, HUMAN_POOLS, CAPACITY)
    await _login(reg, client, tenant, "ia-1", ["sac_ia"], 20, kind="ai")
    await _sum_of_pool_lines(reg, client, tenant, HUMAN_POOLS + ["sac_ia"])

    roll = await reg.refresh_tenant_capacity(tenant, force=True)
    wa_h = roll["by_kind"]["human"]["by_channel"]["whatsapp"]
    wa_a = roll["by_kind"]["ai"]["by_channel"]["whatsapp"]
    assert wa_h["pools_available"] == len(HUMAN_POOLS), (
        f"human/whatsapp pools_available={wa_h['pools_available']}: são "
        f"{len(HUMAN_POOLS)} portas HUMANAS — pool de IA no mesmo canal não é porta "
        "humana, e contá-lo aqui é a fungibilidade voltando pelo campo vizinho"
    )
    assert wa_a["pools_available"] == 1, (
        f"ai/whatsapp pools_available={wa_a['pools_available']}, esperava 1"
    )


# ── 3. Ambiguidade publicada, nunca dobrada ───────────────────────────────────

@pytest.mark.asyncio
async def test_instance_in_pools_of_different_kinds_falls_into_unknown(env):
    """Config contraditória vira balde `unknown` — não vira human nem ai.

    A instância consome UMA licença; qual moeda é indeterminado a partir do dado. Dobrar
    em qualquer um dos dois escolheria um lado em silêncio e produziria um número
    plausível. `unknown` publicado é a degradação honesta (e o rollup loga o conflito).
    """
    reg, client, tenant = env
    await _pool(client, tenant, "pool_h", "human", ["whatsapp"])
    await _pool(client, tenant, "pool_a", "ai",    ["whatsapp"])
    await _login(reg, client, tenant, "inst-mixed", ["pool_h", "pool_a"], 3)

    roll = await reg.refresh_tenant_capacity(tenant, force=True)
    assert "unknown" in roll["by_kind"], (
        f"instância em pools de tipos diferentes foi classificada como "
        f"{list(roll['by_kind'])} — a contradição sumiu da tela"
    )
    assert roll["by_kind"]["unknown"]["available"] == 3
    assert "human" not in roll["by_kind"] and "ai" not in roll["by_kind"], (
        "a instância ambígua foi dobrada numa das moedas"
    )


@pytest.mark.asyncio
async def test_pool_without_agent_kind_is_unknown_not_assumed_human(env):
    """Pool sem `agent_kind` no cache (config ainda não replicada) → `unknown`.

    Assumir `human` seria o default plausível: humano é a moeda cara, e superestimar
    disponibilidade humana leva a oferecer atendimento que não existe.
    """
    reg, client, tenant = env
    await _pool(client, tenant, "pool_sem_kind", None, ["whatsapp"])
    await _login(reg, client, tenant, "inst-1", ["pool_sem_kind"], 2)

    roll = await reg.refresh_tenant_capacity(tenant, force=True)
    assert list(roll["by_kind"]) == ["unknown"], (
        f"tipo inventado a partir de config ausente: {list(roll['by_kind'])}"
    )


# ── 4. Escopo: a conta restrita é REFEITA, não recortada ──────────────────────

@pytest.mark.asyncio
async def test_scoped_rollup_excludes_resources_outside_the_domain(env):
    """Supervisor que enxerga 2 pools não pode ver a capacidade de IA que não alcança.

    Caso real (2026-08-02): admin com `accessible_pools` = 2 pools humanos via o rollup
    do tenant diria "353 de IA disponíveis" — nenhum daqueles agentes está nos pools
    dele. O escopo tem de entrar no CÁLCULO.
    """
    reg, client, tenant = env
    for p in HUMAN_POOLS:
        await _pool(client, tenant, p, "human", ["whatsapp"])
    await _pool(client, tenant, "sac_ia", "ai", ["webchat"])
    await _login(reg, client, tenant, "human-1", HUMAN_POOLS, CAPACITY)
    await _login(reg, client, tenant, "ia-1", ["sac_ia"], 20, kind="ai")

    full = await reg.compute_tenant_capacity(tenant)
    assert set(full["by_kind"]) == {"human", "ai"}, "setup: o tenant tem os dois tipos"

    scoped = await reg.compute_tenant_capacity(tenant, only_pools=HUMAN_POOLS)
    assert set(scoped["by_kind"]) == {"human"}, (
        f"escopo humano trouxe {list(scoped['by_kind'])} — capacidade que o domínio "
        "não alcança vazou para dentro dele"
    )
    assert scoped["by_kind"]["human"]["available"] == CAPACITY
    assert scoped["scoped_to_pools"] == sorted(HUMAN_POOLS), (
        "o recorte não se anuncia: número escopado sem carimbo vira 'capacidade do "
        "tenant' na cabeça de quem lê a tela"
    )


@pytest.mark.asyncio
async def test_scoped_rollup_counts_a_shared_resource_whole(env):
    """Recurso logado DENTRO e FORA do domínio conta INTEIRO no escopo.

    Disponibilidade escopada é "quanto os MEUS pools alcançam", não "quanto é meu": o
    humano oferece as 3 vagas aos pools do domínio de fato. Que um pool de fora possa
    consumi-las antes é `busy_elsewhere` — não uma fatia a descontar aqui. Ratear as 3
    vagas entre os pools do recurso inventaria frações e subestimaria o que o domínio
    realmente alcança.
    """
    reg, client, tenant = env
    for p in HUMAN_POOLS + ["fora_do_escopo"]:
        await _pool(client, tenant, p, "human", ["whatsapp"])
    await _login(reg, client, tenant, "human-1", HUMAN_POOLS + ["fora_do_escopo"], CAPACITY)

    scoped = await reg.compute_tenant_capacity(tenant, only_pools=[HUMAN_POOLS[0]])
    assert scoped["by_kind"]["human"]["available"] == CAPACITY, (
        f"available={scoped['by_kind']['human']['available']}: o recurso foi rateado "
        f"entre seus pools. O domínio alcança as {CAPACITY} vagas inteiras."
    )
    assert scoped["by_kind"]["human"]["instances"] == 1


@pytest.mark.asyncio
async def test_empty_scope_is_not_unrestricted_scope(env):
    """Domínio vazio devolve vazio — não o tenant inteiro.

    O par perigoso: `only_pools=None` (irrestrito) e `only_pools=[]` (alcança nada) são
    opostos que um `if not only_pools` colapsaria num só, vazando o tenant para quem
    declarou não alcançar pool nenhum.
    """
    reg, client, tenant = env
    await _pool(client, tenant, "retencao_humano", "human", ["whatsapp"])
    await _login(reg, client, tenant, "human-1", ["retencao_humano"], 3)

    assert await reg.compute_tenant_capacity(tenant, only_pools=[]) == {}
    assert (await reg.compute_tenant_capacity(tenant))["by_kind"]["human"]["available"] == 3


# ── 5. O rollup é publicado, e o throttle não o congela ───────────────────────

@pytest.mark.asyncio
async def test_rollup_is_persisted_and_throttled(env):
    """Publicado em `{t}:capacity:snapshot`, e o throttle suprime o recompute seguinte.

    O throttle existe porque o rollup alimenta KPI e oferta de canal — escala humana,
    não gate de milissegundo. Mas ele não pode CONGELAR o valor: a segunda chamada é
    suprimida, e a chave publicada continua sendo a da primeira.
    """
    reg, client, tenant = env
    await _pool(client, tenant, "retencao_humano", "human", ["whatsapp"])
    await _login(reg, client, tenant, "human-1", ["retencao_humano"], 3)

    first = await reg.refresh_tenant_capacity(tenant)      # sem force: pega o cooldown
    assert first, "primeira chamada não publicou"
    stored = json.loads(await client.get(_tenant_capacity_key(tenant)))
    assert stored["by_kind"]["human"]["available"] == 3
    assert "computed_at" in stored

    assert await reg.refresh_tenant_capacity(tenant) is None, (
        "o throttle não suprimiu o recompute imediatamente seguinte"
    )
    assert await reg.refresh_tenant_capacity(tenant, force=True) is not None, (
        "`force` deveria ignorar o cooldown — sem isso não há como recomputar sob demanda"
    )


@pytest.mark.asyncio
async def test_tenant_without_pools_yields_nothing_rather_than_zero(env):
    """Tenant sem pool nenhum → rollup vazio, não um rollup de zeros.

    Zero afirmaria "não há capacidade"; vazio diz "não há o que medir". O consumidor
    trata os dois de formas opostas — o primeiro desvia o cliente de canal.
    """
    reg, _client, tenant = env
    assert await reg.compute_tenant_capacity(tenant) == {}
    assert await reg.refresh_tenant_capacity(tenant, force=True) is None
