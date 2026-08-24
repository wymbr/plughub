"""
mute_queue.py — Fila de sistema (tier gratuito) — system-queue.md, Fase A.

Helpers do ciclo de vida da fila MUDA (pool sem ENDEREÇO de fila — isto é, sem
`queue_config.pool_id`; ver `queue_address` abaixo — ou overflow de admissão): isenção de C, contador total, tetos por canal e o ledger analítico
via SEGMENTO SINTÉTICO `role=queue` — mesma fonte que a Fase D do
queue-attended-model já consome no /reports/pools/queue (sem tópicos novos).

Chaves:
  {t}:queue:unadmitted          SET  — sessões em fila muda (isentas de C).
                                       SCARD = ocupação do buffer grátis
                                       (teto: max_queue_total).
  {t}:queue:first_queued:{sid}  STR  — epoch ms do PRIMEIRO enqueue (NX + TTL).
                                       Preserva a espera real através de
                                       re-enfileiramentos (re-admissão negada
                                       no drain) e dá a duração do segmento.

⚠️ **O nome do módulo ficou ESTREITO (D12, 2026-08-28).** `resolve_queue_exit` (ex-
`resolve_mute_exit`) passou a registrar a espera nos **DOIS** tiers — atendido e
mudo —, porque a espera é fato de ROTEAMENTO e não pode depender de o agente de
fila rodar. O resto do módulo segue específico da fila muda (isenção de C, buffer,
tetos). Não renomeei o arquivo para não espalhar diff por N imports; a divergência
está declarada aqui em vez de descoberta depois.

Saídas da fila (emitem o segmento e limpam o estado) — o portão é o
`first_queued_ms`, que existe nos dois tiers (`registry.py` add_queued_contact):
  handoff   — há agente para o contato: admitido no inbound, OU dequeue no drain.
  abandoned — cliente desconectou esperando (contact_closed, ou marker no drain).
  max_wait_exceeded — teto de retenção estourado. **Desde 2026-08-24 (fatia B)
   também sai daqui**, via `close_reason=`. Era emitido por um segundo produtor
   dentro do `_emit_queue_timeout`, que sorteava o id, publicava sem `key=` e —
   por viver no ramo `else` do `queue:agent_active` — só cobria o tier MUDO.

Proteções (spec § Proteções operacionais): tetos vêm do namespace `routing` do
Config API via `routing_config` (cache com defaults hard-coded e degradação
graciosa — Config fora/ausente NUNCA significa ilimitado; reload automático no
config.changed).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from .routing_config import routing_config

logger = logging.getLogger("plughub.routing.mute_queue")

_TTL_S = 604_800  # 7d — mesmo horizonte dos markers de sessão


def unadmitted_key(tenant_id: str) -> str:
    return f"{tenant_id}:queue:unadmitted"


def first_queued_key(tenant_id: str, session_id: str) -> str:
    return f"{tenant_id}:queue:first_queued:{session_id}"


def _decode(v) -> str:
    if v is None:
        return ""
    return v.decode() if isinstance(v, bytes) else str(v)


# ── Tier da fila: ENDEREÇO, nunca presença de objeto (defeito 2, 2026-08-24) ──

def queue_address(pool_cfg: dict | None) -> str:
    """
    Pool que ATENDE a espera deste pool. `""` ⇒ fila MUDA.

    **É o predicado único do tier**, e existe porque havia quatro testes
    diferentes sobre o mesmo objeto, cada um respondendo a outra pergunta:

      · `main.py:808`  — `if queue_config:`            (presença do OBJETO)
      · `main.py:1272` — `if max_wait_s > 0:`          (teto, rotulado como tier)
      · bridge `:5735` — `if not queue_cfg:`           (presença do OBJETO)
      · bridge `:5841` — `queue_pool_id or pool_id`    (ADIVINHA o endereço)

    O que quebrou na prática: `queue_config` carrega TRÊS fatos de escopos
    diferentes — endereço (`pool_id`), política de espera (`max_wait_s`) e um
    endereço LEGADO que não endereça mais nada (`skill_id`/`agent_type_id`, mortos
    desde que produção passou a ser o slot `current` do POOL, 2026-07-13). Logo um
    pool que só queria configurar o teto de espera ficava classificado como fila
    ATENDIDA, pulava a isenção de licença de IA e o veto de canal, e o bridge
    logava ERROR de deploy quebrado num pool deliberadamente sem tratamento.

    Só `pool_id` é endereço. Ausente = ninguém atende = fila muda — os dois
    cenários do produto (com e sem pool de IA na fila de um pool humano) são o
    MESMO caminho com o campo preenchido ou vazio, nunca dois códigos.
    """
    if not pool_cfg:
        return ""
    qc = pool_cfg.get("queue_config") or {}
    if not isinstance(qc, dict):
        return ""
    return (qc.get("pool_id") or "").strip()


def pool_max_wait_s(pool_cfg: dict | None) -> int:
    """
    Teto de espera declarado NO POOL (segundos); 0 = não declarado.

    Ortogonal ao tier: vale na fila muda também. Quem vence é o pool — o teto por
    canal é DEFAULT de quem não declarou, mais o veto do `0` (ver
    `channel_max_wait_s`). Decidido 2026-08-24: espera é fato do pool.
    """
    if not pool_cfg:
        return 0
    qc = pool_cfg.get("queue_config") or {}
    if not isinstance(qc, dict):
        return 0
    try:
        return max(0, int(qc.get("max_wait_s") or 0))
    except (TypeError, ValueError):
        return 0


# ── Tetos (proteções operacionais — Config API namespace `routing`) ───────────

def max_queue_total() -> int:
    """Teto TOTAL do buffer grátis (hard limit; default 100, nunca ilimitado)."""
    try:
        v = int(routing_config.get("queue_max_total", 100))
        return v if v > 0 else 100
    except (TypeError, ValueError):
        return 100


def channel_max_wait_s(settings, channel: str) -> int:
    """
    Tolerância do CANAL a espera em silêncio (segundos).

    **`0` é VETO, não teto** — o canal não aceita fila muda nenhuma (voice: dead
    air segura tronco) e o contato é encerrado gracioso na porta (`main.py:816`).
    Qualquer outro valor é apenas o DEFAULT de quem não declarou `max_wait_s` no
    pool; declarado, o pool vence (decisão 2026-08-24 — espera é fato do pool, e
    esta tabela é tolerância física do canal, não promessa de atendimento).

    Config: `queue_max_wait_by_channel` {canal: segundos}; canais ausentes caem
    no `queue_max_wait_default_s` dos settings (1800).
    """
    default = int(getattr(settings, "queue_max_wait_default_s", 1800))
    by_channel = routing_config.get("queue_max_wait_by_channel") or {}
    try:
        v = by_channel.get(channel)
        return int(v) if v is not None else default
    except (TypeError, ValueError, AttributeError):
        return default


# ── Ciclo de vida ─────────────────────────────────────────────────────────────

async def mark_mute_queued(redis_client, tenant_id: str, session_id: str, now_ms: int) -> int:
    """
    Registra a sessão como em fila muda (isenta de C) e devolve o epoch ms do
    PRIMEIRO enqueue (NX — re-enfileiramentos preservam a espera original).
    """
    fq_key = first_queued_key(tenant_id, session_id)
    await redis_client.set(fq_key, str(now_ms), nx=True, ex=_TTL_S)
    await redis_client.sadd(unadmitted_key(tenant_id), session_id)
    raw = _decode(await redis_client.get(fq_key))
    try:
        return int(float(raw)) if raw else now_ms
    except ValueError:
        return now_ms


def queue_wait_segment_id(tenant_id: str, session_id: str,
                          first_queued_ms: int) -> str:
    """
    Id DETERMINÍSTICO do segmento de espera (D12 emenda 1; discriminado 2026-09-01).

    Uma passagem pela fila tem identidade derivável — não sorteável. Com `uuid4()`
    (o que havia aqui e ainda há no `_emit_queue_timeout` e no bridge) duas
    emissões viram duas LINHAS; com id derivado viram a mesma linha, e o
    `ReplacingMergeTree` deduplica sozinho. É o padrão que o `quality-ingest` já
    usa para idempotência de importação.

    Isso substitui um guard: em vez de garantir "emite uma vez só" no caminho
    (impossível em N saídas concorrentes), garante-se que emitir duas vezes seja
    INÓCUO.

    ── Por que o `first_queued_ms` entra no namespace (2026-09-01) ────────────────
    A premissa antiga estava escrita aqui em prosa — *"uma sessão tem UMA passagem
    pela fila"* — e é **falsa**. Medido num contato real
    (`9403a14b-3020-4cf9-85f2-6a937dae41c4`): espera de 24 118 ms em
    `retencao_humano` (saída `handoff`, humano assumiu), transferência, espera de
    85 009 ms em `especialista_onboarding` (saída `abandoned`). **Duas emissões, um
    id, uma linha** — o ClickHouse guardou a segunda e os 24 118 ms **deixaram de
    existir**. Não é defeito de exibição: é perda de dado, irrecuperável.

    O discriminador é o **carimbo de entrada**, não o pool, e a razão é de escopo:

      · o carimbo é fato da PASSAGEM — `add_queued_contact` o escreve com NX
        (`registry.py:2628`) e a saída o apaga (abaixo), então a chave já
        SIGNIFICA "esta passagem". Re-enfileiramento dentro da mesma passagem
        preserva o valor (é o que `test_release_preserves_first_queued` exige),
        logo o id continua estável onde tem de ser.
      · o `pool_id` é fato do CALL SITE. Em `main.py:286` o emissor passa
        `event.pool_id or ""` — duas saídas da mesma passagem chegando por sites
        com pools diferentes (ou uma com `""`) dariam DOIS ids para UMA passagem,
        destruindo justamente a idempotência acima. Discriminar por pool também
        não separaria duas esperas no MESMO pool.

    ⚠️ **DESCONTINUIDADE DE IDENTIDADE.** As linhas gravadas antes desta mudança
    têm o id sem carimbo; nenhuma delas é reescrita e nenhuma migração as alcança
    (o `first_queued_ms` original não é recuperável — a chave é apagada na saída).
    Consequência prática: nenhuma. Ninguém junta segmento por id calculado fora do
    produtor, e o `ReplacingMergeTree` só dedupe dentro da mesma chave de ORDER BY.

    ⚠️ **O que esta mudança NÃO conserta, de propósito:** o relatório Fila/SLA
    colapsa a sessão numa linha (`anyIf(pool_id, role='queue')` /
    `anyIf(outcome, ...)` / `maxIf(duration_ms, ...)` em `reports_query.py:5752`),
    então a segunda linha que passa a existir aqui continua descartada na leitura —
    e o `anyIf` passa a sortear onde as duas discordam. Medido: a exposição JÁ
    existe (2 de 5 sessões multi-linha discordam hoje), mas era resíduo histórico
    congelado e passa a crescer com o tráfego. O conserto do colapso é a **D14**
    (SLA é fato do segmento), e somar as esperas NÃO é o conserto — somar duas
    esperas contra um alvo é o que a D14 diz não ter uso.
    """
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"plughub:queue-wait:{tenant_id}:{session_id}:{int(first_queued_ms)}",
    ))


async def resolve_queue_exit(
    redis_client,
    producer,
    tenant_id:  str,
    pool_id:    str,
    session_id: str,
    outcome:    str,                 # "handoff" | "abandoned"
    emit_segment: bool = True,
    close_reason: str = "",
) -> bool:
    """
    Encerra a passagem pela fila — **nos DOIS tiers** (atendida e muda) — e publica
    o segmento `role='queue'` com a espera REAL.

    ── Por que esta função mudou de nome e de escopo (D12, 2026-08-28) ────────────
    Era `resolve_mute_exit`, e abria com `SREM(unadmitted)` + `return False`: quem
    não estava em fila **muda** saía sem nada. Como ela já é chamada em TODAS as
    saídas de fila, o efeito era uma cobertura pela metade sem sintoma próprio — a
    fila ATENDIDA nunca registrava espera, e a ausência sumia no mesmo silêncio do
    resto. Medido: 19 segmentos de espera no tenant, todos do tier mudo.

    O `SREM` continua, mas agora é **bookkeeping de admissão**, não portão: o seu
    resultado não decide mais se o fato é registrado. Quem decide é o
    `first_queued_ms` — que `add_queued_contact` escreve com **NX** para os dois
    tiers (`registry.py:2618-2633`). **Sem esse carimbo não há espera a declarar, e
    aí não se emite nada**: ausência honesta, nunca um `duration_ms` fabricado a
    partir de `now`.

    ⚠️ O nome antigo prometia menos do que a função faz agora; mantê-lo seria a
    armadilha que este repositório já catalogou duas vezes (*"o nome pode ser mais
    largo que o conteúdo"* — `session_transitions`, `SessionMeta`). Aqui é o
    inverso: o conteúdo ficou mais largo que o nome.

    ── `close_reason` e o fim do segundo emissor (2026-08-24, fatia B) ───────────
    A lacuna que este docstring nomeava — *"`emit_segment=False` para o caminho de
    max_wait, porque o `_emit_queue_timeout` emite o segmento dele; unificar é fatia
    à parte"* — **está fechada**, e o que a fechou foi o parâmetro abaixo.

    O emissor de lá era um SEGUNDO produtor do mesmo fato, com três diferenças que
    não eram escolha de ninguém: `uuid4()` no lugar do id derivado (duas emissões =
    duas linhas), publish **sem `key=`** no tópico de 3 partições, e — a que doía —
    ele vivia no ramo `else` do teste `queue:agent_active`, logo **só o tier MUDO
    registrava espera**. `max_wait_exceeded` na fila ATENDIDA saía sem segmento
    nenhum, e a ausência sumia no mesmo silêncio que a D12 já tinha catalogado.

    A única coisa que aquele emissor tinha e esta função não era o `close_reason`
    (hardcoded `""` aqui). Um campo — e era ele que sustentava o segundo produtor.

    ⚠️ **Não há emissão dupla**, e isso é medido, não deduzido: no tier ATENDIDO o
    bridge emite `role='specialist'` para o agente de fila desde a D12
    (`orchestrator-bridge/main.py:6007`), não `role='queue'`. O único produtor de
    espera é este.

    Devolve `True` se emitiu (ou se havia passagem de fila a encerrar).
    """
    # Bookkeeping de admissão — não é mais o portão do registro (ver docstring).
    await redis_client.srem(unadmitted_key(tenant_id), session_id)

    fq_key = first_queued_key(tenant_id, session_id)
    raw    = _decode(await redis_client.get(fq_key))
    if not raw:
        # Nunca passou pela fila (ou o carimbo expirou): não há espera a declarar.
        #
        # ⚠️ Era `if raw is None`, e esse teste NUNCA era verdadeiro: `_decode`
        # devolve `""` para chave ausente (linha 60-63), não `None`. O portão
        # ficava morto e todo contato roteado DIRETO — sem fila nenhuma —
        # emitia um segmento `role='queue' outcome='handoff' duration_ms=0`,
        # porque logo abaixo `int(float(raw)) if raw else now_ms` cai no
        # `now_ms` e a subtração dá zero. Espera fantasma no relatório de
        # Fila/SLA, que é justamente o que este produtor existe para consertar.
        #
        # Medido 2026-08-21, coorte de 3 contatos: 2 sem fila → 2 fantasmas
        # (100%); o contato com espera real (47 327 ms) saiu correto ao lado.
        # Invisível na query canônica do Problema 36.2, que conta
        # `outcome='abandoned'` — a fantasma é `handoff`.
        #
        # É o "valor plausível" da § Postura de Engenharia: `""` no lugar de
        # ausência derruba a guarda sem deixar rastro, e o defeito só aparece
        # quando alguém conta a população que NÃO devia ter linha.
        return False
    await redis_client.delete(fq_key)

    if not emit_segment:
        return True

    now    = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    try:
        first_ms = int(float(raw)) if raw else now_ms
    except ValueError:
        # Degradação NUNCA silenciosa: com carimbo ilegível a duração vira 0 E o
        # id passa a depender de `now` (deixa de ser determinístico). Os dois
        # sintomas são plausíveis — `duration_ms=0` some no relatório e um id
        # novo vira "outra passagem" —, então o motivo tem de ficar no log.
        logger.warning(
            "queue exit: carimbo ilegível (%r) session=%s pool=%s — duração vai a "
            "0 e o segment_id deixa de ser estável para esta passagem",
            raw, session_id, pool_id,
        )
        first_ms = now_ms
    duration_ms = max(0, now_ms - first_ms)
    joined_iso  = datetime.fromtimestamp(first_ms / 1000, tz=timezone.utc).isoformat()

    try:
        # ── `key=session_id` — mesma correção do bridge (2026-08-18), que faltava
        # AQUI (medido 2026-08-28) ────────────────────────────────────────────────
        # `conversations.participants` tem 3 partições e este publish era SEM CHAVE.
        # Publicar sem chave nesse tópico é o defeito mais caro já registrado neste
        # repositório (CLAUDE.md § Postura de Engenharia; conference-mechanics.md
        # § Problema 34): sem chave o particionador espalha, e ordem no Kafka é por
        # PARTIÇÃO.
        #
        # ⚠️ Por que não mordia HOJE — e por que isso não é justificativa para deixar:
        # este caminho emite UM evento só (`participant_left`, já com `joined_at` e
        # `duration_ms`), então não existe o par `joined`/`left` que possa se
        # inverter. A proteção é ACIDENTAL, não projetada: ela desaparece no instante
        # em que alguém acrescentar o `participant_joined` — e desaparece com o
        # agravante de o caminho já parecer testado. Ordenar também contra os DEMAIS
        # eventos da mesma sessão (que é o que a leitura de topologia assume) só se
        # obtém com a chave.
        #
        # Chave = `session_id`, não `segment_id`: idêntica à do bridge (`main.py:3534`),
        # de propósito — duas convenções de chave no mesmo tópico reabririam o
        # problema pelo lado da cardinalidade.
        #
        # NOTE: o producer tem `value_serializer=json.dumps().encode` — o `value` vai
        # como dict; a CHAVE não passa por serializer nenhum e vai como bytes.
        await producer.send("conversations.participants", key=session_id.encode("utf-8"), value={
            "event_id":       str(uuid.uuid4()),
            "type":           "participant_left",
            "session_id":     session_id,
            "tenant_id":      tenant_id,
            # D12 emenda 1: id DERIVADO, não sorteado — ver queue_wait_segment_id.
            # `first_ms` (2026-09-01) discrimina a PASSAGEM: sem ele, duas esperas
            # da mesma sessão colapsam numa linha e a primeira é perdida.
            "segment_id":     queue_wait_segment_id(tenant_id, session_id, first_ms),
            "participant_id": "system-queue",
            "pool_id":        pool_id,
            "agent_type_id":  "system",
            "agent_type":     "system",
            "role":           "queue",
            "sequence_index": 0,
            "joined_at":      joined_iso,
            "timestamp":      now.isoformat(),
            "duration_ms":    duration_ms,
            "outcome":        outcome,
            "close_reason":   close_reason,
        })
        # "queue exit", não "mute queue exit": a função cobre os DOIS tiers desde a
        # D12, e desde a fatia B cobre também o max_wait. Log que descreve escopo
        # menor que o do código é a mesma armadilha do nome antigo da função.
        logger.info(
            "queue exit: session=%s pool=%s outcome=%s close_reason=%s wait_ms=%d",
            session_id, pool_id, outcome, close_reason or "-", duration_ms,
        )
    except Exception as exc:
        logger.warning(
            "queue exit: failed to publish synthetic segment session=%s — %s",
            session_id, exc,
        )
    return True


# `buffer_usage` REMOVIDA (fatia 3, 2026-08-02). Único chamador era
# `main._try_overflow_enqueue`, que checava se o buffer grátis tinha vaga antes de
# acomodar o overflow da admissão — overflow que ficou sem entrada possível quando
# sessão humana deixou de ser gateada por `C`. O teto do buffer (`max_queue_total`)
# continua vivo: é o denominador da linha `__buffer__` da série e do tile do Monitor.
