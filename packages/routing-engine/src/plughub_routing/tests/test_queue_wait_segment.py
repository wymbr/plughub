"""
test_queue_wait_segment.py — janela de espera (D12) + tier da fila (defeito 2).

Duas propriedades do MESMO mecanismo, e as duas são sobre não afirmar fato que
não houve: *a espera só é registrada se houve espera* (D12, testes de integração,
precisam de Redis) e *a fila só é ATENDIDA se alguém atende* (defeito 2, predicado
puro, no fim do arquivo — nunca pulado por ambiente).

Sob teste está UMA propriedade, e ela é sobre AUSÊNCIA: **contato que não esperou
não produz segmento de espera.** O caminho que a viola não fica vermelho em lugar
nenhum — ele produz uma linha `role='queue' outcome='handoff' duration_ms=0`, que
é um valor perfeitamente plausível e some no meio do relatório de Fila/SLA.

História (2026-08-21): o portão era `if raw is None`, e `_decode` devolve `""`
para chave ausente, nunca `None`. O teste nunca era verdadeiro, o `return False`
nunca disparava, e **todo contato roteado direto emitia uma espera fantasma**.
Medido em 3 contatos reais: 2 sem fila → 2 fantasmas (100%); a query canônica do
Problema 36.2 não via nenhuma delas, porque lá o predicado é `outcome='abandoned'`
e a fantasma é `handoff`.

Cada teste diz o que o faria REPROVAR:
  · test_no_stamp_emits_nothing      → o portão volta a aceitar carimbo ausente
  · test_stamp_emits_wait_segment    → o produtor para de registrar espera real
  · test_publish_is_keyed_by_session → some o `key=` do publish (P1)
  · test_segment_id_is_deterministic → o id volta a ser sorteado (uuid4)
  · test_two_passages_get_distinct_ids       → o discriminador de passagem sai do
    namespace e duas esperas voltam a colapsar numa linha (2026-09-01)
  · test_same_passage_emits_the_same_id_twice → o id passa a depender de `now()`
    e duas saídas concorrentes da MESMA passagem viram duas linhas
  · test_timeout_{mute,attended}_tier_… + …_without_stamp_… → o `max_wait_exceeded`
    volta a ter emissor próprio, ou a emissão volta a depender do tier
    (fatia B, 2026-08-24 — o do tier ATENDIDO nasceu vermelho)

⚠️ Os dois últimos são um PAR e só julgam juntos: sozinho, o primeiro fica verde
com um `uuid4()` de volta (ids sempre distintos) e o segundo fica verde com o
defeito de 2026-08-24 (ids sempre iguais). Não separar.

⚠️ O `_FakeProducer` daqui aceita `key` **na forma exata** em que o código chama e
falha se ela não vier. Mock mais permissivo que o contrato foi o que já escondeu
bug neste repositório: fixture que aceita kwarg inexistente prova só que o teste é
tolerante.

Teste de INTEGRAÇÃO: precisa de um Redis real.
    PLUGHUB_REDIS_URL=redis://redis:6379 pytest test_queue_wait_segment.py

O skip por Redis ausente é EXPLÍCITO (nunca verde por ausência de ambiente) e lê
as DUAS variáveis — `REDIS_URL` não existe dentro do container.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest
import redis.asyncio as aioredis

from plughub_routing import mute_queue
from plughub_routing.mute_queue import (
    first_queued_key, queue_wait_segment_id, resolve_queue_exit,
)


REDIS_URL = (
    os.environ.get("REDIS_URL")
    or os.environ.get("PLUGHUB_REDIS_URL")
    or "redis://localhost:6379"
)


class _FakeProducer:
    """
    Espelha o contrato REAL do publish: `send(topic, key=bytes, value=dict)`.

    `key` é obrigatória de propósito. `conversations.participants` tem 3
    partições e ordem no Kafka é por partição; com `segment_id` determinístico
    a MESMA sessão emite várias vezes a MESMA linha, e é a chave que garante que
    a última emissão (a que traz o desfecho real) chegue por último. Sem ela o
    vencedor é sorteio — a linha pode congelar em `handoff wait_ms=0`.
    """
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes, dict]] = []

    async def send(self, topic: str, *, key: bytes, value: dict) -> None:
        assert isinstance(key, (bytes, bytearray)), (
            f"chave deve ir como bytes, veio {type(key).__name__}"
        )
        self.sent.append((topic, key, value))


@pytest.fixture
async def env():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"Redis indisponível em {REDIS_URL} — teste de integração pulado")
    tenant = f"t_qw_{uuid.uuid4().hex[:8]}"
    try:
        yield client, _FakeProducer(), tenant
    finally:
        async for k in client.scan_iter(f"{tenant}:*"):
            await client.delete(k)
        await client.aclose()


@pytest.mark.asyncio
async def test_no_stamp_emits_nothing(env):
    """
    O caso que o defeito de 2026-08-21 produzia: sessão SEM `first_queued_ms`.

    Reprova se o portão voltar a comparar com `None` (ou qualquer teste que
    trate `""` como presença). O sintoma no produto é uma espera fantasma de
    0 ms em todo contato atendido na hora.
    """
    client, producer, tenant = env
    sid = "ses-sem-fila"

    assert await client.get(first_queued_key(tenant, sid)) is None, (
        "pré-condição quebrada: a sessão do teste já tem carimbo de fila"
    )

    emitted = await resolve_queue_exit(
        client, producer, tenant, "sac_ia", sid, "handoff",
    )

    assert emitted is False, "declarou espera para contato que nunca esperou"
    assert producer.sent == [], (
        f"emitiu {len(producer.sent)} segmento(s) de espera fantasma: "
        f"{[v.get('duration_ms') for _, _, v in producer.sent]}"
    )


@pytest.mark.asyncio
async def test_stamp_emits_wait_segment(env):
    """
    TESTEMUNHA DE PRESENÇA — sem ela, o teste acima passaria com um produtor
    que nunca emite nada. Um contador de ausência precisa do par.

    Reprova se o produtor deixar de registrar espera real, ou se a duração
    deixar de derivar do carimbo (ex.: alguém trocar por `now - now`).
    """
    client, producer, tenant = env
    sid       = "ses-com-fila"
    waited_ms = 5_000
    first_ms  = int(time.time() * 1000) - waited_ms
    await client.set(first_queued_key(tenant, sid), str(first_ms))

    emitted = await resolve_queue_exit(
        client, producer, tenant, "retencao_humano", sid, "abandoned",
    )

    assert emitted is True
    assert len(producer.sent) == 1, f"esperado 1 segmento, veio {len(producer.sent)}"
    topic, _key, value = producer.sent[0]
    assert topic == "conversations.participants"
    assert value["role"] == "queue"
    assert value["outcome"] == "abandoned"
    assert value["pool_id"] == "retencao_humano", (
        "D10.1: o pool do segmento de espera é o DESTINO, não a fila que executou"
    )
    assert value["duration_ms"] >= waited_ms, (
        f"duração {value['duration_ms']}ms não reflete a espera de {waited_ms}ms — "
        "o carimbo deixou de ser a fonte"
    )

    assert await client.get(first_queued_key(tenant, sid)) is None, (
        "o carimbo sobreviveu à emissão — a segunda saída da mesma sessão "
        "emitiria de novo com duração recontada a partir do início original"
    )


@pytest.mark.asyncio
async def test_publish_is_keyed_by_session(env):
    """
    P1 — a chave do publish é `session_id`, e ela é LOAD-BEARING.

    A justificativa original ("este caminho emite um evento só") não se
    sustentou na medição: um contato que escala produziu TRÊS emissões para a
    mesma sessão, todas com o mesmo `segment_id`. Quem vence é quem chega por
    último, e isso só é determinístico dentro de uma partição.

    Reprova se alguém remover o `key=` (o `_FakeProducer` levanta TypeError) ou
    trocar a convenção para `segment_id` — duas convenções de chave no mesmo
    tópico reabrem o problema pelo lado da cardinalidade.
    """
    client, producer, tenant = env
    sid = "ses-chave"
    await client.set(first_queued_key(tenant, sid), str(int(time.time() * 1000)))

    await resolve_queue_exit(client, producer, tenant, "sac_ia", sid, "handoff")

    assert len(producer.sent) == 1
    _topic, key, _value = producer.sent[0]
    assert key == sid.encode("utf-8"), (
        f"chave={key!r}, esperado {sid.encode('utf-8')!r} — mesma convenção do "
        "bridge (main.py), de propósito"
    )


@pytest.mark.asyncio
async def test_segment_id_is_deterministic(env):
    """
    O id derivado é o que torna emissão repetida INÓCUA: em vez de garantir
    "emite uma vez só" em N saídas concorrentes (impossível), garante-se que as
    N emissões sejam a MESMA linha, e o ReplacingMergeTree deduplica.

    Reprova se alguém voltar a `uuid4()` — o sintoma seria uma linha de espera
    por saída, inflando o volume de fila do relatório.
    """
    client, producer, tenant = env
    sid      = "ses-id"
    first_ms = int(time.time() * 1000)
    await client.set(first_queued_key(tenant, sid), str(first_ms))

    await resolve_queue_exit(client, producer, tenant, "sac_ia", sid, "handoff")

    _topic, _key, value = producer.sent[0]
    assert value["segment_id"] == queue_wait_segment_id(tenant, sid, first_ms)
    assert value["segment_id"] == queue_wait_segment_id(tenant, sid, first_ms), (
        "não é estável"
    )
    assert value["segment_id"] != queue_wait_segment_id(tenant, "outra", first_ms), (
        "o id não discrimina sessão"
    )


@pytest.mark.asyncio
async def test_two_passages_get_distinct_ids(env):
    """
    REGRESSÃO do contato real `9403a14b-3020-4cf9-85f2-6a937dae41c4` (2026-08-24).

    Ele esperou DUAS vezes — 24 118 ms em `retencao_humano` (saída `handoff`) e
    85 009 ms em `especialista_onboarding` (saída `abandoned`) — e o ledger ficou
    com UMA linha: mesmo `segment_id`, `ReplacingMergeTree` guardou a segunda. A
    primeira espera não existe em lugar nenhum, e não volta.

    Reprova se o discriminador sair do namespace: aí os dois ids voltam a ser
    iguais e o assert de desigualdade cai. É o teste que autoriza a mudança de
    identidade — sem ele, "o id é determinístico" continuaria verde com o defeito
    de volta, porque determinismo nunca foi a propriedade que faltava.

    ⚠️ Só julga porque a população do teste TEM o caso em que os valores diferem:
    duas passagens com carimbos distintos. Com um carimbo só, `!=` passaria por
    acidente em qualquer implementação.
    """
    client, producer, tenant = env
    sid = "ses-duas-filas"

    # Passagem 1 — o humano assume; a saída apaga o carimbo (é o que permite a 2ª).
    first_a = int(time.time() * 1000) - 24_118
    await client.set(first_queued_key(tenant, sid), str(first_a))
    await resolve_queue_exit(
        client, producer, tenant, "retencao_humano", sid, "handoff",
    )
    assert await client.get(first_queued_key(tenant, sid)) is None, (
        "a passagem 1 não apagou o carimbo — sem isso a passagem 2 herdaria o "
        "carimbo antigo e nem chegaria a existir como passagem própria"
    )

    # Passagem 2 — transferido, espera de novo em OUTRO pool, cliente desiste.
    first_b = int(time.time() * 1000) - 85_009
    await client.set(first_queued_key(tenant, sid), str(first_b))
    await resolve_queue_exit(
        client, producer, tenant, "especialista_onboarding", sid, "abandoned",
    )

    assert len(producer.sent) == 2, (
        f"esperadas 2 emissões (uma por passagem), vieram {len(producer.sent)}"
    )
    id_a = producer.sent[0][2]["segment_id"]
    id_b = producer.sent[1][2]["segment_id"]
    assert id_a != id_b, (
        "as duas esperas têm o MESMO segment_id — no ClickHouse elas colapsam "
        "numa linha e a primeira espera é perdida (colisão de 2026-08-24)"
    )
    assert producer.sent[0][2]["outcome"] == "handoff"
    assert producer.sent[1][2]["outcome"] == "abandoned", (
        "o desfecho da 2ª passagem foi sobrescrito pelo da 1ª"
    )
    assert producer.sent[0][2]["pool_id"] == "retencao_humano"
    assert producer.sent[1][2]["pool_id"] == "especialista_onboarding"


@pytest.mark.asyncio
async def test_same_passage_emits_the_same_id_twice(env):
    """
    TESTEMUNHA DE PRESENÇA do teste acima — sem ela, um id que embutisse `now()`
    (ou `uuid4()`) passaria em "duas passagens, dois ids" e destruiria em silêncio
    a propriedade que a D12 comprou: emitir a MESMA passagem duas vezes tem de
    produzir a MESMA linha, porque as saídas concorrem.

    Duas saídas da mesma passagem ⇒ mesmo carimbo ⇒ mesmo id.
    """
    client, producer, tenant = env
    sid      = "ses-mesma-passagem"
    first_ms = int(time.time() * 1000) - 3_000

    await client.set(first_queued_key(tenant, sid), str(first_ms))
    await resolve_queue_exit(client, producer, tenant, "sac_ia", sid, "handoff")

    # Segunda saída da MESMA passagem (corrida real: drain × contact_closed). O
    # carimbo foi apagado, então o produtor recusa — por isso o id é conferido
    # pela função, que é o que o ClickHouse veria se a corrida tivesse empatado.
    await resolve_queue_exit(client, producer, tenant, "sac_ia", sid, "abandoned")

    assert len(producer.sent) == 1, (
        "a segunda saída da mesma passagem emitiu de novo — o carimbo deveria "
        "tê-la barrado"
    )
    assert producer.sent[0][2]["segment_id"] == queue_wait_segment_id(
        tenant, sid, first_ms
    ), "o id da passagem não é reconstruível a partir do carimbo dela"


# ══ max_wait_exceeded: UM produtor, os DOIS tiers (fatia B, 2026-08-24) ═════════
#
# Até aqui o `_emit_queue_timeout` tinha um emissor PRÓPRIO de segmento de espera,
# no ramo `else` do teste `queue:agent_active`. Consequência: na fila ATENDIDA o
# contato era fechado por teto de retenção **sem nenhum segmento `role='queue'`** —
# o relatório de Fila/SLA perdia exatamente a população de que trata, e a perda não
# tinha sintoma próprio (não é linha errada; é linha ausente).
#
# ⚠️ Estes três só julgam JUNTOS, e cada um cobre o buraco do outro:
#   · mute_tier      → reprova se o segundo emissor VOLTAR (2 linhas em vez de 1),
#                      ou se o `close_reason` se perder no caminho unificado
#   · attended_tier  → reprova se a emissão voltar a depender do tier. **É o único
#                      que estava VERMELHO antes desta fatia** (emitia 0)
#   · sem carimbo    → testemunha de AUSÊNCIA: sem ela, um produtor que emitisse
#                      incondicionalmente passaria nos dois de cima
#
# Previsão escrita antes de rodar: 1 · 1 · 0. Antes da mudança seria 1 · **0** · 0.
#
# ── Falseabilidade CONFERIDA em 2026-08-24 ───────────────────────────────────────
# A linha "estava VERMELHO antes desta fatia" era **dedução**, não observação:
# quando foi escrita o código já estava consertado, e o único vermelho que o autor
# tinha visto era o do harness (o `_FakeProducer` do fixture exigindo `key=` em
# todos os tópicos ⇒ `TypeError` engolido ⇒ `AttributeError` antes de qualquer
# asserção — ver `_run_timeout` abaixo). Comentário que afirma medição sem tê-la
# feito é a mesma família do DDL de `participation_intervals`, então foi medido:
#
#   mutação: a chamada a `resolve_queue_exit` em `_emit_queue_timeout` re-gatilhada
#   por `queue:agent_active` (o ramo `else` reencenado)
#   resultado: **1 failed, 3 passed** — `attended_tier` vermelho com
#   "fila ATENDIDA fechada por max_wait produziu 0 segmentos de espera"
#
# ⚠️ **Não use `git stash` para repetir isto.** Com a fatia B commitada o stash é
# no-op, o teste segue verde, e *verde por ausência de mudança* é indistinguível de
# *teste que não pode reprovar* — que é a proposição sob exame. Mute de propósito, e
# confirme QUAL código rodou por `inspect.getsource` da função carregada (não por
# `grep`: este bloco de comentário cita os nomes que a busca contaria).


class _TimeoutProducer:
    """
    Fake do `_emit_queue_timeout`, que publica em TRÊS tópicos com contratos de
    chave DIFERENTES — e o fake reflete isso em vez de nivelar por baixo.

    `conversations.participants` **exige** `key`: é o tópico de 3 partições cuja
    publicação sem chave é o defeito mais caro já registrado no repositório. Os
    outros dois (outbound, `conversations.events`) hoje publicam sem chave, e essa
    é dívida à parte — o fake a tolera **e a declara aqui**, para que ninguém leia
    a tolerância como aprovação. Mock mais permissivo que o contrato já escondeu
    bug aqui; mock mais estrito que o código só produziria vermelho falso.
    """
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes | None, dict]] = []

    async def send(self, topic: str, *, key: bytes | None = None, value: dict) -> None:
        if topic == "conversations.participants":
            assert isinstance(key, (bytes, bytearray)), (
                "publish em conversations.participants SEM key= — ordem no Kafka é "
                f"por partição (veio {type(key).__name__})"
            )
        self.sent.append((topic, key, value))

    def wait_segments(self) -> list[dict]:
        return [v for t, _, v in self.sent
                if t == "conversations.participants" and v.get("role") == "queue"]


class _Settings:
    kafka_topic_outbound       = "conversations.outbound"
    queue_timeout_close_grace_s = 0


async def _run_timeout(client, tenant: str, sid: str,
                       *, attended: bool, stamp_ms: int | None) -> _TimeoutProducer:
    """
    Exercita `_emit_queue_timeout` de verdade — não uma reimplementação dele.

    ⚠️ **O producer é criado AQUI, não recebido do fixture.** O `_FakeProducer` do
    fixture exige `key=` em TODOS os tópicos, o que é o contrato certo para o
    `resolve_queue_exit` (que só publica em `conversations.participants`) e errado
    para este caminho, que também publica em outbound e `conversations.events` —
    os dois ainda sem chave, dívida à parte. Usar o do fixture fazia os três
    publishes de fechamento levantarem `TypeError` dentro dos `try/except` do
    código, e o teste morria por `AttributeError` antes de qualquer asserção.

    ⚠️ As chaves de tier e de fechamento (`queue:agent_active:*`,
    `session:*`) NÃO são prefixadas por tenant, logo escapam da limpeza do fixture
    (`scan_iter(f"{tenant}:*")`). Removidas à mão aqui: teste que suja o Redis de
    um ambiente compartilhado volta como flakiness de outro teste.
    """
    from plughub_routing.main import _emit_queue_timeout

    producer = _TimeoutProducer()
    if stamp_ms is not None:
        await client.set(first_queued_key(tenant, sid), str(stamp_ms))
    if attended:
        await client.set(f"queue:agent_active:{sid}", "1")
    try:
        await _emit_queue_timeout(
            client, producer, _Settings(), tenant, "retencao_humano", sid,
            int(time.time() * 1000),
        )
        # O ramo atendido agenda o close num `create_task`; com grace 0 basta
        # ceder o loop uma vez para ele terminar antes do teardown.
        await asyncio.sleep(0)
    finally:
        await client.delete(
            f"queue:agent_active:{sid}",
            f"session:{sid}:closed",
            f"session:{sid}:contact_close_fired",
            f"session:{sid}:contact_id",
            f"menu:result:{sid}",
        )
    return producer


@pytest.mark.asyncio
async def test_timeout_mute_tier_emits_exactly_one_wait_segment(env):
    """
    Reprova se o emissor duplicado voltar (2 linhas), se o `close_reason` sumir no
    caminho unificado, ou se o id voltar a ser sorteado.
    """
    client, _unused, tenant = env
    sid      = "ses-timeout-mudo"
    first_ms = int(time.time() * 1000) - 12_000

    producer = await _run_timeout(client, tenant, sid,
                                  attended=False, stamp_ms=first_ms)

    segs = producer.wait_segments()
    assert len(segs) == 1, (
        f"esperado 1 segmento de espera, vieram {len(segs)} — "
        f"{[s.get('segment_id') for s in segs]} (dois emissores de volta?)"
    )
    assert segs[0]["close_reason"] == "max_wait_exceeded", (
        f"o motivo real virou {segs[0]['close_reason']!r}: o teto de retenção "
        f"ficou indistinguível de um abandono qualquer"
    )
    assert segs[0]["outcome"] == "abandoned"
    assert segs[0]["segment_id"] == queue_wait_segment_id(tenant, sid, first_ms), (
        "o id não é reconstruível a partir do carimbo — voltou a ser sorteado"
    )
    assert segs[0]["duration_ms"] >= 12_000, (
        f"duração {segs[0]['duration_ms']} ms não reflete a espera carimbada"
    )


@pytest.mark.asyncio
async def test_timeout_attended_tier_also_emits_wait_segment(env):
    """
    **O teste que estava vermelho.** Fila ATENDIDA (`queue:agent_active` presente)
    fechada por teto: até 2026-08-24 saíam ZERO segmentos de espera daqui, porque o
    emissor morava no ramo `else`.

    Reprova se a emissão voltar a depender do tier.
    """
    client, _unused, tenant = env
    sid      = "ses-timeout-atendido"
    first_ms = int(time.time() * 1000) - 9_000

    producer = await _run_timeout(client, tenant, sid,
                                  attended=True, stamp_ms=first_ms)

    segs = producer.wait_segments()
    assert len(segs) == 1, (
        f"fila ATENDIDA fechada por max_wait produziu {len(segs)} segmentos de "
        f"espera — era exatamente esta a lacuna que a fatia B fechou"
    )
    assert segs[0]["close_reason"] == "max_wait_exceeded"
    # A unificação não pode ter trocado um caminho pelo outro: o contato ainda
    # fecha.
    assert any(t == "conversations.events" and v.get("event_type") == "contact_closed"
               for t, _, v in producer.sent), (
        "o contato não foi fechado — a unificação não pode ter comido o passo 4"
    )


@pytest.mark.asyncio
async def test_timeout_without_stamp_emits_no_wait_segment(env):
    """
    TESTEMUNHA DE AUSÊNCIA dos dois acima: sessão sem `first_queued_ms` (carimbo
    expirado, ou fechada por um caminho que já o consumiu) **não** produz espera.

    Reprova se alguém "consertar" a ausência fabricando duração a partir de `now`
    — que é exatamente o que o emissor removido fazia quando o `queued_at_ms` do
    `queue_contact` também faltava (`joined_iso = now_iso`, `wait_ms = 0`).
    """
    client, _unused, tenant = env
    sid = "ses-timeout-sem-carimbo"

    assert await client.get(first_queued_key(tenant, sid)) is None

    producer = await _run_timeout(client, tenant, sid,
                                  attended=False, stamp_ms=None)

    segs = producer.wait_segments()
    assert segs == [], (
        f"declarou espera para sessão sem carimbo: "
        f"{[s.get('duration_ms') for s in segs]} ms"
    )
    # Testemunha de presença DO PRÓPRIO CAMINHO: o contato fecha mesmo assim.
    # Sem ela, uma função que abortasse no topo passaria neste teste.
    assert any(t == "conversations.events" and v.get("event_type") == "contact_closed"
               for t, _, v in producer.sent), (
        "nada foi publicado — o teste acima passaria por caminho morto"
    )


def test_timeout_has_no_participants_publish_of_its_own():
    """
    Guarda ESTRUTURAL do produtor único — e serve de preflight de símbolo: se este
    passar dentro do container, o código medido é o código novo.

    Pergunta feita à AST, não ao texto: *quantos `…send("conversations.participants",
    …)` existem dentro do `_emit_queue_timeout`?* Resposta exigida: **zero** — a
    espera é publicada pelo `resolve_queue_exit`, que é quem tem o id derivado e a
    chave.

    ⚠️ Por que AST e não `grep`/`in`: o docstring da própria função **cita** o nome
    do tópico ao explicar por que o emissor saiu. Uma busca textual contaria o
    comentário que documenta a remoção e reproduziria o número ANTIGO — armadilha
    já paga neste repositório.

    Sem Redis: nunca pulado por ambiente.
    """
    import ast
    import inspect

    from plughub_routing.main import _emit_queue_timeout

    tree = ast.parse(inspect.getsource(_emit_queue_timeout))
    topics = [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", "") == "send"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    ]

    assert "conversations.participants" not in topics, (
        "o `_emit_queue_timeout` voltou a publicar segmento por conta própria — "
        "é o emissor com uuid4() e sem key= que a fatia B removeu, e ele só cobre "
        "o tier MUDO"
    )
    # TESTEMUNHA DE PRESENÇA: sem ela, um `getsource` que devolvesse a função
    # errada (ou vazia) passaria na asserção acima por caminho morto.
    assert "conversations.events" in topics, (
        f"a AST não encontrou o publish de contact_closed — o instrumento está "
        f"medindo outra coisa (tópicos vistos: {topics})"
    )


# ══ Tier da fila: ENDEREÇO × política × endereço legado (defeito 2, 2026-08-24) ══
#
# Mesmo mecanismo, propriedade complementar: os testes acima cuidam de "a espera
# só é registrada se houve espera"; os de baixo, de "a fila só é ATENDIDA se
# alguém atende". Sem Redis — predicado puro, nunca pulado por ambiente.
#
# O defeito: `queue_config` carrega três fatos de escopos diferentes, e quatro
# call sites perguntavam "há quem atenda?" testando a PRESENÇA do objeto. Um pool
# que só declarava teto de espera era classificado como fila atendida, segurava
# licença de IA durante uma espera que ninguém atendia, e o bridge logava ERROR
# de deploy quebrado num pool deliberadamente sem tratamento.

def test_legacy_skill_and_policy_are_not_an_address():
    """
    TESTEMUNHA NEGATIVA — a config VIVA do `retencao_humano` em 2026-08-24.

    Reprova se o predicado voltar a considerar o objeto (ou o `skill_id` legado,
    ou o `max_wait_s`) como endereço. É o caso que produzia o defeito inteiro, e
    ele parece configurado: dois campos preenchidos e nenhum endereça nada.
    """
    pool_cfg = {"queue_config": {"agent_type_id": "", "max_wait_s": 1800,
                                 "skill_id": "skill_fila_v1"}}
    assert mute_queue.queue_address(pool_cfg) == "", (
        "objeto sem `pool_id` foi lido como fila atendida — é o defeito 2 de volta"
    )
    assert mute_queue.pool_max_wait_s(pool_cfg) == 1800, (
        "a política de espera sumiu junto com o endereço: são fatos separados, e "
        "o teto do pool vale também na fila muda"
    )


def test_address_is_the_pool_id():
    """
    TESTEMUNHA DE PRESENÇA — sem ela, um `queue_address` que devolvesse `""`
    sempre passaria no teste acima, e a fila atendida nunca mais ativaria.

    Cobre o cenário 2 do produto: pool humano COM pool de IA na fila.
    """
    pool_cfg = {"queue_config": {"pool_id": " fila_humano ", "max_wait_s": 300}}
    assert mute_queue.queue_address(pool_cfg) == "fila_humano"
    assert mute_queue.pool_max_wait_s(pool_cfg) == 300


def test_absent_config_is_mute_and_has_no_ceiling():
    """
    Cenário 1: pool sem tratamento nenhum (35 dos 36 pools do demo).

    `0` em `pool_max_wait_s` significa NÃO DECLARADO — é o que faz a varredura
    cair na tolerância do canal. Se alguém "consertar" para 1800, o pool passa a
    ter teto próprio sem ninguém ter configurado, e o teto por canal morre.
    """
    for pool_cfg in ({}, {"queue_config": None}, {"queue_config": {}}):
        assert mute_queue.queue_address(pool_cfg) == ""
        assert mute_queue.pool_max_wait_s(pool_cfg) == 0
    assert mute_queue.queue_address(None) == ""
    assert mute_queue.pool_max_wait_s(None) == 0


def test_malformed_config_degrades_to_mute_not_to_crash():
    """
    `queue_config` vem de JSON de terceiros (registry → Redis). Tipo inesperado
    não pode derrubar o roteamento — mas também não pode virar endereço.
    Reprova se alguém remover a checagem de tipo e o `.get` estourar.
    """
    for bad in ("fila_humano", ["fila_humano"], 42):
        assert mute_queue.queue_address({"queue_config": bad}) == ""
        assert mute_queue.pool_max_wait_s({"queue_config": bad}) == 0
    assert mute_queue.pool_max_wait_s({"queue_config": {"max_wait_s": "abc"}}) == 0
    assert mute_queue.pool_max_wait_s({"queue_config": {"max_wait_s": -5}}) == 0
