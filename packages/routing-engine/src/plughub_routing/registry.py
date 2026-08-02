"""
registry.py
Instance state and pool configurations — exclusively via Redis.
Spec: PlugHub v24.0 sections 3.3, 4.5 and 4.6

Rule: never access PostgreSQL directly.
Pool configs read from Redis cache (populated by kafka_listener from agent.registry.events).
Instance state read from Redis (populated by kafka_listener from agent.lifecycle).

Redis key structure:
  {tenant_id}:instance:{instance_id}                        — instance state (TTL 30s)
  {tenant_id}:pool:{pool_id}:instances                      — set of instance_ids in pool
  {tenant_id}:pool_config:{pool_id}                         — pool config JSON (TTL 24h, via PLUGHUB_POOL_CONFIG_TTL_SECONDS)
  {tenant_id}:pools                                         — set of pool_ids for the tenant
  {tenant_id}:pool:{pool_id}:queue                          — sorted set of contacts (score = queued_at_ms)
  {tenant_id}:instance:{instance_id}:sessions               — SET de ocupantes (semáforo de vagas). Membros:
                                                              "{session_id}::{conference_id}::{pool_id}" = vaga
                                                              ocupada, TAGGED com o pool que serviu (F1);
                                                              "__wrapup_hold__::{origin}::{pool_id}::{expires_at_ms}"
                                                              = vaga SEGURA entre o fim do contato e o auto-claim
                                                              do wrap-up inline (Phase 2 — hand-off da vaga).
                                                              Invariante: o pool é SEMPRE o 3º campo "::"
  {tenant_id}:queue_contact:{session_id}                    — queued contact JSON
  session_instance:{session_id}                             — session affinity (stateful)
  {tenant_id}:routing:instance:{instance_id}:meta           — HASH no TTL (pools, agent_type_id)
  {tenant_id}:routing:instance:{instance_id}:conversations  — SET no TTL of active conversation_ids
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis

from .models import AgentInstance, InstanceMeta, PoolConfig, QueuedContact, RoutingExpression
from .config import get_settings
from .mute_queue import first_queued_key, _TTL_S as _FIRST_QUEUED_TTL_S

logger = logging.getLogger("plughub.routing.registry")


# ─────────────────────────────────────────────
# Redis key helpers
# ─────────────────────────────────────────────

def _instance_key(tenant_id: str, instance_id: str) -> str:
    """Spec: {tenant_id}:instance:{instance_id}"""
    return f"{tenant_id}:instance:{instance_id}"

def _instance_sessions_key(tenant_id: str, instance_id: str) -> str:
    """Per-instance occupancy SET — semáforo de contagem atômico (claim/release).

    Membros = occupant_ids (conference_id quando presente, senão session_id). SCARD =
    current_sessions real (fonte de verdade). Usado por claim_instance/release_instance
    (Lua atômico) para eliminar a corrida de sobre-alocação do select→mark_busy
    não-atômico. Ver TODO § Router (corrida de sobre-alocação).
    """
    return f"{tenant_id}:instance:{instance_id}:sessions"

def _pool_instances_key(tenant_id: str, pool_id: str) -> str:
    """Set of instance_ids present (ready) in the pool."""
    return f"{tenant_id}:pool:{pool_id}:instances"

def _pool_busy_instances_key(tenant_id: str, pool_id: str) -> str:
    """Set of instance_ids currently handling at least one session in the pool."""
    return f"{tenant_id}:pool:{pool_id}:busy_instances"

# `_pool_active_count_key` REMOVIDA (fatia 2 da capacidade compartilhada).
# Era o contador atômico `{t}:pool:{p}:active_count` — INCR em `mark_busy`, DECR em
# `remove_conversation` — e a FONTE do defeito A: contava por POOL uma capacidade que
# é do RECURSO, então vaga tomada num pool não descontava nos irmãos (1 humano de 3
# vagas em 3 pools ⇒ três linhas dizendo `available 3`, soma 6, verdade 2). Também
# era a origem do `available 4 / total 3` remendado por teto/chão em 2026-07-30.
# `busy` agora é DERIVADO do semáforo do recurso — ver `_RECOMPUTE_POOL_OCCUPANCY_LUA`.
# Não ressuscitar: um contador paralelo à fonte diverge por construção.

def _pool_config_key(tenant_id: str, pool_id: str) -> str:
    """Pool configuration cache — populated by kafka_listener."""
    return f"{tenant_id}:pool_config:{pool_id}"

def _pool_set_key(tenant_id: str) -> str:
    """Set of all pool_ids for the tenant."""
    return f"{tenant_id}:pools"

def _claim_lease_key(tenant_id: str, pool_id: str, session_id: str) -> str:
    """
    Frente 1 (pull): lease do claim — {instance_id, claimed_at}, TTL de
    `routing.claim_lease_s` (default 180 s).

    ATENÇÃO — a lease é escrita UMA vez (`work_task_claim`) e apagada no release
    explícito ou no re-parque. **Não há heartbeat que a renove nem reaper que a
    colete**: ela expira passivamente no Redis e ninguém repara nisso. Item
    reivindicado e abandonado fica fora da fila, sem lease, com a vaga do agente
    ocupada até o reap de ocupantes órfãos passar.

    *(Este docstring afirmava o contrário — "TTL curto renovado por heartbeat (F1.3);
    ao expirar, o auto-release re-enfileira" — descrevendo um mecanismo que nunca foi
    implementado. Corrigido em 2026-07-30. Documentação que promete uma rede
    inexistente é pior que a ausência dela: quem lê para de procurar o vazamento.)*

    O único consumidor é `work_task_holder` (leitura A5 pelo channel-gateway), que
    **falha aberto** quando a lease sumiu — o resume não é bloqueado por isso.
    """
    return f"{tenant_id}:pool:{pool_id}:claim:{session_id}"

def _queue_key(tenant_id: str, pool_id: str) -> str:
    """Sorted set of queued contacts (score = queued_at_ms)."""
    return f"{tenant_id}:pool:{pool_id}:queue"

def _queue_contact_key(tenant_id: str, session_id: str) -> str:
    return f"{tenant_id}:queue_contact:{session_id}"

def _session_serving_pool_key(tenant_id: str, session_id: str) -> str:
    """
    Stores which pool_id is currently serving this session.
    Written on each routing event; used to detect cross-pool transfers (escalations)
    and to know which sibling snapshot to recompute without relying on agent_done.

    Depois da fatia 2 este valor NÃO é mais o índice de um contador — a ocupação vem
    da tag no membro do semáforo. Ele sobrevive como discriminador de RE-ENTRADA no
    mesmo pool (o guard de `mark_busy`) e como pista de qual pool reescrever.
    TTL: 24h (sessions don't last longer).
    """
    return f"{tenant_id}:session:pool:{session_id}"

def _session_instance_key(session_id: str) -> str:
    """Session affinity for stateful agents."""
    return f"session_instance:{session_id}"

def _instance_meta_key(tenant_id: str, instance_id: str) -> str:
    """HASH with no TTL: instance pools and agent_type_id. Used by CrashDetector."""
    return f"{tenant_id}:routing:instance:{instance_id}:meta"

def _instance_conversations_key(tenant_id: str, instance_id: str) -> str:
    """SET with no TTL of active conversation_ids on the instance. Used by CrashDetector."""
    return f"{tenant_id}:routing:instance:{instance_id}:conversations"

def _pool_snapshot_key(tenant_id: str, pool_id: str) -> str:
    """Operational snapshot — written by router after each routing event. TTL 3600s
    (o default de `write_pool_snapshot(snapshot_ttl=3600)`; três docs diziam 120s —
    corrigido 2026-07-31 após medir 2958s restantes numa chave de 11 min)."""
    return f"{tenant_id}:pool:{pool_id}:snapshot"

def _pool_peak_key(tenant_id: str, pool_id: str, minute_bucket: str) -> str:
    """Watermark de ocupação do pool no minuto (P1 — pico EVENT-DRIVEN).

    Pico é o máximo de uma FUNÇÃO ESCADA, e o valor só muda na alocação/liberação.
    Amostrar por relógio é o método errado por construção: qualquer intervalo de
    amostra pode cair inteiro entre duas subidas, e não é questão de escolher um
    intervalo menor. Esta chave guarda o máximo alcançado no minuto; quem escreve são
    as TRANSIÇÕES (ver `record_pool_peak`), e o flusher só lê e publica.

    TTL curto (`_PEAK_TTL_S`): a chave é buffer entre a transição e o flush do minuto,
    não armazenamento — a série durável é `analytics.pool_occupancy_peaks`.
    """
    return f"{tenant_id}:pool:{pool_id}:peak:{minute_bucket}"

def _pool_peak_cap_key(tenant_id: str, pool_id: str, minute_bucket: str) -> str:
    """Capacidade provisionada NO INSTANTE DO PICO (achado 1 de 2026-08-02).

    O flusher chamava `_pool_capacity` na virada do minuto, enquanto o pico vinha do
    minuto que passou — duas grandezas do mesmo registro medidas em instantes
    diferentes. Consequência observada: `peak 1 / provisioned 0`, impossível por
    construção, e `headroom`/`utilization` derivados com denominador de outro momento.
    Escrita junto com o pico, e só quando o pico avança: é a capacidade *daquele*
    instante, de propósito.
    """
    return f"{tenant_id}:pool:{pool_id}:peakcap:{minute_bucket}"

# 2 h: folga generosa sobre o minuto que o flusher precisa reler, sem virar histórico.
_PEAK_TTL_S = 7200


def minute_bucket(when: "datetime | None" = None) -> str:
    """Rótulo do bucket de um minuto (UTC). Ponto único — a chave de escrita (nas
    transições) e a de leitura (no flusher) precisam derivar do MESMO formato, senão
    o flusher lê um bucket que ninguém escreveu e publica zero com cara de medição."""
    dt = when or datetime.now(timezone.utc)
    return dt.strftime("%Y%m%d%H%M")


def _tenant_occupancy_zset_key(tenant_id: str) -> str:
    """Ocupação corrente por INSTÂNCIA (P2) — `member = instance_id`, `score = SCARD`.

    Existe porque o pico do TENANT não é derivável dos watermarks por pool: `max` de
    SOMAS ≠ soma de `max`. Provado na série de 2026-08-02 — quatro pools registraram
    pico 1 no mesmo minuto e o `__total__` foi 2, porque os picos ocorreram em instantes
    diferentes e no máximo dois coexistiram.

    **É a FONTE, não um contador.** Instância com ocupação 0 é removida (`ZREM`), então
    a cardinalidade é O(instâncias ocupadas) e a soma dos scores é a verdade a qualquer
    momento. O contador ao lado é só o atalho O(1); quando os dois discordam, quem manda
    é este.
    """
    return f"{tenant_id}:occupancy"

def _tenant_occupancy_total_key(tenant_id: str) -> str:
    """Total de vagas ocupadas no tenant (P2) — atalho O(1) do ZSET acima.

    > **Este é um CONTADOR, a mesma família do `{t}:pool:{p}:active_count` que este arco
    > removeu.** A diferença que o torna aceitável não é de forma, é de regime:
    >   · escopo CERTO (tenant, que é onde a grandeza existe) — o `active_count` contava
    >     por POOL uma capacidade que é do RECURSO;
    >   · tem FONTE contra a qual conferir (o ZSET), que o `active_count` não tinha;
    >   · é CONFERIDO de fato, 1×/min, e a divergência é LOGADA e corrigida.
    >
    > Remover a reconciliação devolve este contador exatamente à condição do anterior.
    > Se ela sair, este contador tem de sair junto.
    """
    return f"{tenant_id}:occupancy:total"

# TTL das duas chaves. Generoso: elas são estado vivo, não histórico, e o `EXPIRE` é
# rede contra tenant que some — não mecanismo de correção (isso é a reconciliação).
_TENANT_OCCUPANCY_TTL_S = 86_400


def _tenant_capacity_key(tenant_id: str) -> str:
    """Rollup de capacidade do TENANT, por tipo de licença (F4, defeito C).

    Existe porque `Σ available(pool)` está errado e **não é corrigível na linha do
    pool**: a informação de sobreposição não está lá. Um humano `max_concurrent 3`
    logado em 3 pools aparece — corretamente — como `available 3` em cada linha; quem
    soma obtém 9 (ou 6, com dois pools) para UM recurso de 3 vagas. A linha do pool não
    é o defeito: somá-la é. Daí uma SEGUNDA superfície, sobre instâncias DISTINTAS.

    Nunca existe um campo `available` no topo: disponibilidade de humano e de IA são
    moedas não-fungíveis, e somá-las repetiria a falácia de aditividade um nível acima —
    em vez de contar o mesmo recurso duas vezes, contaria recursos que não se
    substituem. Ver `docs/product/shared-capacity-pool-as-tag-design.md` §3.
    """
    return f"{tenant_id}:capacity:snapshot"

def _tenant_capacity_cooldown_key(tenant_id: str) -> str:
    """Throttle do rollup. Ele alimenta KPI e decisão em escala humana (oferta de canal,
    tela de monitor), não gate de milissegundo — recomputar a cada transição de ocupação
    de um tenant movimentado seria custo sem consumidor."""
    return f"{tenant_id}:capacity:cooldown"

# Janela do throttle. Mesmo espírito do `_REAP_COOLDOWN_S`: N transições concorrentes
# disparam UM recompute, não N.
_CAPACITY_ROLLUP_COOLDOWN_S = 5
_CAPACITY_ROLLUP_TTL_S      = 3600


def _agent_perf_key(tenant_id: str, agent_type_id: str) -> str:
    """
    Arc 7d: historical performance score for an agent type.
    Written by analytics-api performance_job every 5 minutes.
    Value: str(float) in [0.0, 1.0].
    TTL: 6 hours (refreshed by performance_job before expiry).
    """
    return f"{tenant_id}:agent_perf:{agent_type_id}"


# ─────────────────────────────────────────────
# Atomic instance semaphore (claim/release) — Lua
# ─────────────────────────────────────────────
# Elimina a corrida de sobre-alocação do select→mark_busy não-atômico: a reserva é
# um ato atômico (Redis executa Lua single-threaded). Modelo = semáforo de contagem
# por-instância sobre um SET de occupant_ids; SCARD é a contagem real. claim/release
# são IDEMPOTENTES (cobre de quebra o redelivery de agent_done). Single-key (cluster-safe).
#
# OCCUPANT = "{session_id}::{conference_id}::{pool_id}"  (conference_id vazio p/ contato
# normal; pool_id vazio quando o chamador não o informa = UNTAGGED).
# Por quê: duas conferências da MESMA sessão (ex.: fan-out de wrap-up) têm conference_ids
# distintos → occupants distintos → NÃO dividem a mesma vaga (a 2ª recebe -1 e re-seleciona
# outra instância). Já o RELEASE só conhece o session_id (o agent_done não carrega
# conference_id) → libera por PREFIXO "{session_id}::" (remove a(s) vaga(s) desta sessão
# nesta instância). Simétrico: claim deriva de (session_id, conference_id); release de (session_id).
#
# ── Tag de pool (F1, capacidade compartilhada) ────────────────────────────────
# O pool entra como 3º campo "::" — nos DOIS tipos de membro, então o parse é um só
# (`occupant_pool`). É PROJEÇÃO, nunca contagem: a capacidade é do RECURSO e não
# fragmenta por pool; a tag só diz QUAL pool consumiu a vaga, para o snapshot por pool
# parar de ignorar o consumo dos irmãos (fatia 2). Restrições que o formato preserva:
#   · release por prefixo "{session_id}::" intacto (a tag entra DEPOIS);
#   · prefixo "__wrapup_hold__::" não colide com prefixo de sessão (uuid);
#   · parse de expiração do hold `::(%d+)$` intacto (no hold a tag entra ANTES do ts).
# IDEMPOTÊNCIA: com a tag, a MESMA (sessão, conferência) reivindicada por OUTRO pool
# — transferência cross-pool para instância logada nos dois — passaria num SISMEMBER
# exato com string diferente e criaria um SEGUNDO membro (dupla ocupação da mesma
# sessão no mesmo recurso, número 1 acima e plausível). Por isso a checagem passa a ser
# por PREFIXO "{session_id}::{conference_id}::" e, em hit com pool diferente, o Lua
# faz SREM+SADD (RE-TAG, contagem inalterada).
# LEGADO: membros de 2 campos escritos antes do deploy (o SET tem TTL 24h) são
# UNTAGGED — contam na ocupação do recurso e em nenhuma projeção por pool. O claim
# também os reconhece como hit de idempotência (senão um redelivery na janela de
# migração duplicaria a vaga) e os re-taga ao tocá-los.
#
# ── Wrap-up unificado Phase 2 — hand-off da vaga ──────────────────────────────
# HOLD = ocupante especial que SEGURA a vaga da origem entre o fim do contato e o
# auto-claim do wrap-up inline (auto-atendimento). Sem ele a ocupação OSCILA (o
# release do agent_done libera a vaga; o auto-claim a reivindica ~2-3s depois) e um
# push que chegue na janela toma a vaga a max_concurrent=1 — o agente recebe contato
# novo com wrap-up pendente, exatamente o que a ocupação do wrap-up deve impedir.
#
# Membro: "__wrapup_hold__::{origin_session_id}::{pool_id}::{expires_at_ms}"
#   · o prefixo NÃO colide com o prefixo de sessão "{session_id}::" (uuid) → o
#     release por prefixo de sessão nunca remove um hold, e vice-versa;
#   · origin_session_id é OBSERVABILIDADE (o casamento é FUNGÍVEL: cada hold vale
#     uma vaga, cada wrap-up consome uma — a instância já é a mesma);
#   · expires_at_ms sustenta a expiração PASSIVA (não há sweeper): sem ela, um
#     wrap-up que nunca chega (webhook non-2xx, workflow falha, logout, browser
#     fechado) prenderia a vaga até o EXPIRE de 24h do SET;
#   · o pool_id (F1) é HERDADO do occupant que o swap remove — o pool que serviu é,
#     por construção, o do membro removido. Nenhum parâmetro novo atravessa
#     `remove_conversation`; a atribuição do hold a um pool sai de brinde.
_WRAPUP_HOLD_PREFIX = "__wrapup_hold__::"


def occupant_pool(member: str) -> str | None:
    """Pool que consumiu a vaga (3º campo "::" do membro), ou None se UNTAGGED.

    Ponto ÚNICO de parse da tag — os dois formatos de membro têm o pool no mesmo
    campo, de propósito:
        occupant  "{session_id}::{conference_id}::{pool_id}"
        hold      "__wrapup_hold__::{origin}::{pool_id}::{expires_at_ms}"

    Devolve None (untagged, não ""): membro legado de 2 campos escrito antes do
    deploy da tag, e membro cujo escritor não informou o pool. Untagged conta na
    ocupação do RECURSO e em nenhuma projeção por pool — quem agrega precisa
    contá-los e publicá-los, nunca descartá-los em silêncio.
    """
    if not member:
        return None
    parts = member.split("::")
    # hold  → ['__wrapup_hold__', origin, pool, expires]  (3 campos = hold legado)
    # occup → [session, conference, pool]                 (2 campos = occupant legado)
    minimo = 4 if member.startswith(_WRAPUP_HOLD_PREFIX) else 3
    if len(parts) < minimo:
        return None
    return parts[2] or None

# Janela mínima entre dois reaps de vagas órfãs da MESMA instância. Só corre para
# instância que já aparece lotada, então 60 s é folgado: o custo do atraso é um
# contato a mais indo para outro agente ou para a fila; o custo de não limitar é
# SMEMBERS + N×EXISTS a cada decisão de roteamento.
_REAP_COOLDOWN_S = 60

# claim: KEYS[1]=sessions set; ARGV[1]=occupant_id; ARGV[2]=max_concurrent; ARGV[3]=ttl_s;
#        ARGV[4]=now_ms; ARGV[5]="1" se este claim pode HERDAR um hold (auto_attend);
#        ARGV[6]=prefixo de IDENTIDADE do occupant "{session_id}::{conference_id}::"
#   retorna a nova ocupação (>=1) em sucesso/idempotente; -1 se lotado.
#
# F1 (tag de pool): a idempotência é por PREFIXO de identidade, não por SISMEMBER
# exato — a mesma (sessão, conferência) reivindicada por outro pool tem string
# diferente e criaria um 2º membro. Em hit com pool diferente: SREM antigo + SADD
# novo (RE-TAG, contagem inalterada). O membro LEGADO de 2 campos (idpfx sem o "::"
# final) também é hit — e é re-tagado ao ser tocado.
#
# Phase 2: varre holds e (a) DESCARTA os expirados p/ QUALQUER claim — senão um hold
# vazado bloquearia o push permanentemente; (b) HERDA um hold vivo só quando ARGV[5]=1
# (swap net 0 — a ocupação nunca oscila). Tolerante às duas ordens de chegada: se o
# release já ocorreu (sem hold), cai no claim normal com a checagem de capacidade.
# A varredura de holds NÃO roda no caminho idempotente (retorno antecipado), igual
# ao comportamento pré-tag: quem já tem vaga não altera o estado de ninguém.
_CLAIM_INSTANCE_LUA = """
local members = redis.call('SMEMBERS', KEYS[1])
local idpfx  = ARGV[6]
local ilen   = string.len(idpfx)
local legacy = string.sub(idpfx, 1, ilen - 2)
local existing = nil
for i = 1, #members do
  local m = members[i]
  if string.sub(m, 1, ilen) == idpfx or m == legacy then
    existing = m
    break
  end
end
if existing ~= nil then
  if existing ~= ARGV[1] then
    redis.call('SREM', KEYS[1], existing)
    redis.call('SADD', KEYS[1], ARGV[1])
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
  end
  return redis.call('SCARD', KEYS[1])
end
local HOLD = '__wrapup_hold__::'
local hlen = string.len(HOLD)
local now  = tonumber(ARGV[4])
local inherit = nil
for i = 1, #members do
  local m = members[i]
  if string.sub(m, 1, hlen) == HOLD then
    local exp = tonumber(string.match(m, '::(%d+)$'))
    if exp == nil or exp <= now then
      redis.call('SREM', KEYS[1], m)
    elseif inherit == nil and ARGV[5] == '1' then
      inherit = m
    end
  end
end
if inherit ~= nil then
  redis.call('SREM', KEYS[1], inherit)
  redis.call('SADD', KEYS[1], ARGV[1])
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
  return redis.call('SCARD', KEYS[1])
end
local n = redis.call('SCARD', KEYS[1])
if n >= tonumber(ARGV[2]) then
  return -1
end
redis.call('SADD', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return n + 1
"""

# release: KEYS[1]=sessions set; ARGV[1]=prefixo de sessão ("{session_id}::")
#   remove TODAS as vagas desta sessão na instância; retorna a ocupação restante.
#   Idempotente (sessão ausente = no-op).
_RELEASE_INSTANCE_LUA = """
local members = redis.call('SMEMBERS', KEYS[1])
local prefix = ARGV[1]
local plen = string.len(prefix)
for i = 1, #members do
  if string.sub(members[i], 1, plen) == prefix then
    redis.call('SREM', KEYS[1], members[i])
  end
end
local n = redis.call('SCARD', KEYS[1])
if n <= 0 then
  redis.call('DEL', KEYS[1])
  return 0
end
return n
"""

# swap-to-hold (Phase 2): KEYS[1]=sessions set; ARGV[1]=prefixo de sessão ("{origin}::");
#   ARGV[2]=cabeça do hold ("__wrapup_hold__::{origin}"); ARGV[3]=ttl_s do SET;
#   ARGV[4]=expires_at_ms. Substitui o RELEASE quando o contato tem wrap-up INLINE
#   seguindo: remove a(s) vaga(s) da origem e põe o hold no lugar (net 0 — a ocupação
#   NUNCA oscila). Retorna a ocupação resultante.
#
#   F1: o hold HERDA a tag de pool do PRIMEIRO occupant removido — o membro final é
#   montado aqui, no Lua, justamente para que nenhum parâmetro novo precise atravessar
#   `remove_conversation`. Origem untagged (membro legado) → tag vazia, e o membro
#   segue com 4 campos ("…::{origin}::::{exp}") para o parse `::(%d+)$` não mudar.
#
#   IDEMPOTENTE por construção: só cria o hold se DE FATO removeu a origem. Um
#   redelivery de agent_done (a origem já saiu) não ressuscita um hold que o wrap-up
#   já consumiu — o que criaria uma vaga fantasma permanente.
_SWAP_TO_HOLD_LUA = """
local members = redis.call('SMEMBERS', KEYS[1])
local prefix  = ARGV[1]
local plen    = string.len(prefix)
local removed = 0
local tag     = ''
for i = 1, #members do
  local m = members[i]
  if string.sub(m, 1, plen) == prefix then
    if removed == 0 then
      local rest = string.sub(m, plen + 1)
      local t = string.match(rest, '::(.*)$')
      if t ~= nil then tag = t end
    end
    redis.call('SREM', KEYS[1], m)
    removed = removed + 1
  end
end
if removed > 0 then
  redis.call('SADD', KEYS[1], ARGV[2] .. '::' .. tag .. '::' .. ARGV[4])
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
end
local n = redis.call('SCARD', KEYS[1])
if n <= 0 then
  redis.call('DEL', KEYS[1])
  return 0
end
return n
"""


# ─────────────────────────────────────────────
# Ocupação do TENANT — ZSET + contador conferível (P2)
# ─────────────────────────────────────────────
# KEYS[1]=ZSET de ocupação por instância; KEYS[2]=contador do total.
# ARGV[1]=instance_id; ARGV[2]=nova ocupação da instância; ARGV[3]=ttl.
#
# O delta sai de dentro: `ZSCORE` antes, `ZADD` depois, `INCRBY (novo − antigo)`. É por
# isso que o contador consegue ser O(1) e ainda assim derivar de um fato observável —
# ele nunca é incrementado por "achar" que houve uma alocação, e sim pela diferença
# entre dois estados do mesmo recurso.
#
# Ocupação 0 → `ZREM`: a cardinalidade do ZSET fica O(instâncias OCUPADAS), não
# O(instâncias), então a reconciliação (soma dos scores) é barata mesmo com 353 agentes.
#
# NÃO clampa negativo. Total negativo é impossível se o invariante vale, então é sinal
# de drift — e o chamador o LOGA. Clampar aqui repetiria o erro que a fatia 2 desfez:
# o teto e o chão do `available` existiam para esconder um modelo errado.
_UPDATE_TENANT_OCCUPANCY_LUA = """
local old = tonumber(redis.call('ZSCORE', KEYS[1], ARGV[1])) or 0
local new = tonumber(ARGV[2])
if new <= 0 then
  redis.call('ZREM', KEYS[1], ARGV[1])
else
  redis.call('ZADD', KEYS[1], new, ARGV[1])
end
local total
local delta = new - old
if delta ~= 0 then
  total = redis.call('INCRBY', KEYS[2], delta)
else
  total = tonumber(redis.call('GET', KEYS[2]) or '0')
end
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[3]))
return total
"""

# Reconciliação: soma os scores do ZSET (a FONTE) e devolve {soma, contador}. Não
# corrige aqui — quem corrige loga primeiro, senão o conserto apaga a evidência.
_RECONCILE_TENANT_OCCUPANCY_LUA = """
local members = redis.call('ZRANGE', KEYS[1], 0, -1, 'WITHSCORES')
local soma = 0
for i = 2, #members, 2 do
  soma = soma + tonumber(members[i])
end
local contador = tonumber(redis.call('GET', KEYS[2]) or '0')
return {soma, contador}
"""


# ─────────────────────────────────────────────
# Watermark de ocupação do POOL — Lua (P1, pico event-driven)
# ─────────────────────────────────────────────
# KEYS[1]=peak key; KEYS[2]=capacity key; ARGV[1]=valor; ARGV[2]=capacidade; ARGV[3]=ttl.
# Só sobe (max), nunca desce — a única escrita que faz o pico crescer é uma ocupação que
# DE FATO existiu. Capacidade é gravada junto e só quando o pico avança: ela é a
# capacidade *daquele* instante (achado 1), não a do momento do flush.
#
# Atômico de propósito: dois claims concorrentes na mesma instância chegam aqui com
# valores diferentes, e um GET+SET em Python perderia o maior (lost update — a mesma
# classe de defeito que o `+= 1` do mark_busy tinha antes da fatia B).
# ARGV[4]="1" grava a capacidade junto; "0" grava só o pico. O `__total__` (P2) usa
# "0": a capacidade deduplicada do tenant é montada pelo flusher a partir dos baldes
# por tipo (F4c), e gravar 0 aqui plantaria um zero plausível na chave-irmã.
_RECORD_POOL_PEAK_LUA = """
local cur = tonumber(redis.call('GET', KEYS[1]))
local val = tonumber(ARGV[1])
local ttl = tonumber(ARGV[3])
if cur ~= nil and cur >= val then
  return cur
end
redis.call('SET', KEYS[1], val, 'EX', ttl)
if ARGV[4] == '1' then
  redis.call('SET', KEYS[2], ARGV[2], 'EX', ttl)
end
return val
"""


# ─────────────────────────────────────────────
# Recompute da ocupação do POOL — Lua (fatia 2, defeito A)
# ─────────────────────────────────────────────
# A ocupação de um pool passa a ser DERIVADA do semáforo do RECURSO
# (`{t}:instance:{iid}:sessions`), nunca de um contador. Sobre
# I = ready_set(P) ∪ busy_set(P):
#
#   total_capacity = Σ max_concurrent(i)
#   used_global    = Σ SCARD(sessions_i)                  ← inclui irmãos E holds
#   used_here      = Σ #{ m ∈ sessions_i : tag(m) = P }   ← projeção pela tag (F1)
#   untagged       = Σ #{ m ∈ sessions_i : tag(m) = nil } ← membro legado / escritor mudo
#
# Por que não adotar `current_sessions` (o espelho no registro da instância), que
# hoje está CERTO: é da mesma família do contador por pool — número paralelo que
# pode derivar. Trocar um contador por outro não fecha a classe de defeito, só muda
# qual deles vai mentir depois. O SET é a fonte que o `claim_instance` arbitra.
#
# `untagged` é publicado, nunca descartado: ele conta na ocupação do recurso (senão
# a capacidade apareceria maior do que é) e em projeção de pool nenhuma. Deve ir a
# zero em ≤ 24 h (TTL do SET); persistente = bug de escritor.
#
# NÃO é cluster-safe — deriva chaves de instância dentro do script, diferente do
# claim/release que são single-key de propósito. Deliberado (uma ida e volta em vez
# de um laço de N round-trips); se Redis Cluster entrar em cena, ou hash-tag por
# tenant, ou volta a pipeline.
#
# KEYS[1] = ready_set  ({t}:pool:{p}:instances)
# KEYS[2] = busy_set   ({t}:pool:{p}:busy_instances)
# ARGV[1] = tenant_id   ARGV[2] = pool_id
# → { total_capacity, used_global, used_here, untagged, instances, evicted, unknown }
_RECOMPUTE_POOL_OCCUPANCY_LUA = """
local HOLD = '__wrapup_hold__::'
local HLEN = string.len(HOLD)

local function tag_of(m)
  local parts = {}
  local start = 1
  while true do
    local s, e = string.find(m, '::', start, true)
    if s == nil then
      parts[#parts + 1] = string.sub(m, start)
      break
    end
    parts[#parts + 1] = string.sub(m, start, s - 1)
    start = e + 1
  end
  local minimo = 3
  if string.sub(m, 1, HLEN) == HOLD then minimo = 4 end
  if #parts < minimo then return nil end
  local p = parts[3]
  if p == nil or p == '' then return nil end
  return p
end

local tenant = ARGV[1]
local pool   = ARGV[2]

local ready = redis.call('SMEMBERS', KEYS[1])
local busy  = redis.call('SMEMBERS', KEYS[2])
local in_ready = {}
for i = 1, #ready do in_ready[ready[i]] = true end

local total_capacity = 0
local used_global    = 0
local used_here      = 0
local untagged       = 0
local instances      = 0
local evicted        = 0
local unknown        = 0
local default_mc     = 0

local function occupancy(iid)
  local members = redis.call('SMEMBERS', tenant .. ':instance:' .. iid .. ':sessions')
  local n, here, unt = 0, 0, 0
  for i = 1, #members do
    n = n + 1
    local t = tag_of(members[i])
    if t == nil then
      unt = unt + 1
    elseif t == pool then
      here = here + 1
    end
  end
  return n, here, unt
end

local function max_concurrent_of(iid)
  local raw = redis.call('GET', tenant .. ':instance:' .. iid)
  if not raw then return nil end
  local ok, data = pcall(cjson.decode, raw)
  if not ok or type(data) ~= 'table' then return nil end
  local v = tonumber(data['max_concurrent'])
  if v == nil or v < 1 then return nil end
  return v
end

local function account(iid)
  instances = instances + 1
  local mc = max_concurrent_of(iid)
  if mc ~= nil then
    if default_mc == 0 then default_mc = mc end
    total_capacity = total_capacity + mc
  else
    -- Chave da instância expirada/ilegível. O bootstrap a restaura em ~15 s; contar
    -- capacidade cheia (não zero) evita que a capacidade pisque para baixo no meio
    -- da janela — mesma escolha do modelo anterior, agora CONTABILIZADA em `unknown`
    -- em vez de indistinguível de capacidade medida.
    unknown = unknown + 1
  end
  local n, here, unt = occupancy(iid)
  used_global = used_global + n
  used_here   = used_here + here
  untagged    = untagged + unt
end

for i = 1, #ready do
  account(ready[i])
end

for i = 1, #busy do
  local iid = busy[i]
  if not in_ready[iid] then
    -- Instância que o bootstrap moveu para FORA do ready_set por estar ocupada.
    -- Só conta se de fato ocupa alguma vaga NO SEMÁFORO (não no espelho): sem
    -- ocupação e sem chave é entrada podre, e o modelo anterior já a despejava.
    local n = redis.call('SCARD', tenant .. ':instance:' .. iid .. ':sessions')
    local exists = redis.call('EXISTS', tenant .. ':instance:' .. iid)
    if exists == 0 or n == 0 then
      redis.call('SREM', KEYS[2], iid)
      evicted = evicted + 1
    else
      account(iid)
    end
  end
end

-- Default para instâncias sem chave legível: o `max_concurrent` da primeira que
-- pôde ser lida (1 quando nenhuma pôde). Preserva a heurística do modelo anterior.
if default_mc == 0 then default_mc = 1 end
total_capacity = total_capacity + (unknown * default_mc)

return { total_capacity, used_global, used_here, untagged, instances, evicted, unknown }
"""


# ─────────────────────────────────────────────
# InstanceRegistry
# ─────────────────────────────────────────────

class InstanceRegistry:
    """
    Queries and updates agent instance state in Redis.
    Key: {tenant_id}:instance:{instance_id} — TTL 30s (spec 4.5).
    Populated by kafka_listener from agent.lifecycle events.
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis    = redis_client
        self._settings = get_settings()

    # ── Atomic instance semaphore (claim/release) ────────────────────────────
    # Reserva/libera atômica de uma vaga na instância. Substitui o `current_sessions
    # += 1` (mark_busy) e o `-= 1` (remove_conversation) não-atômicos pela primitiva
    # correta. occupant = "{session_id}::{conference_id}"; release por prefixo de sessão
    # (o agent_done só carrega session_id). Ver TODO § Router (corrida de sobre-alocação).
    @staticmethod
    def _occupant_id(
        session_id: str, conference_id: str | None, pool_id: str | None = None
    ) -> str:
        """Membro do semáforo. O pool é sempre o 3º campo (F1); ausente = untagged."""
        return f"{session_id}::{conference_id or ''}::{pool_id or ''}"

    @staticmethod
    def _occupant_identity_prefix(session_id: str, conference_id: str | None) -> str:
        """Prefixo que identifica a vaga INDEPENDENTE do pool — base da idempotência
        do claim (o mesmo par (sessão, conferência) por outro pool é RE-TAG, não vaga
        nova)."""
        return f"{session_id}::{conference_id or ''}::"

    @staticmethod
    def _session_prefix(session_id: str) -> str:
        return f"{session_id}::"

    async def claim_instance(
        self,
        tenant_id:         str,
        instance_id:       str,
        session_id:        str,
        conference_id:     str | None,
        max_concurrent:    int,
        pool_id:           str | None = None,
        ttl_seconds:       int = 86_400,
        can_inherit_hold:  bool = False,
    ) -> int:
        """Reserva atômica de uma vaga (occupant = session_id::conference_id::pool_id).
        Retorna a nova ocupação (>=1) em sucesso/idempotente; -1 se lotado. Quem recebe
        -1 deve re-selecionar outro best instance. Duas confs da mesma sessão
        (conference_ids distintos) NÃO compartilham vaga → vão para instâncias distintas.

        F1 (tag de pool): `pool_id` é o pool que CONSUMIU a vaga — projeção, não
        contagem. Reivindicar a mesma (sessão, conferência) por outro pool RE-TAGA o
        membro existente; a ocupação não muda (é o mesmo recurso servindo o mesmo
        contato). `pool_id=None` grava untagged — legítimo só para escritor que não
        conhece o pool.

        Phase 2 (hand-off): `can_inherit_hold=True` (claim do wrap-up auto-atendido)
        permite HERDAR um hold vivo desta instância — swap net 0, a ocupação não
        oscila. Holds EXPIRADOS são descartados em qualquer claim (inclusive push),
        senão um hold vazado bloquearia a instância."""
        async def _try() -> int:
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            return int(await self._redis.eval(
                _CLAIM_INSTANCE_LUA, 1,
                _instance_sessions_key(tenant_id, instance_id),
                self._occupant_id(session_id, conference_id, pool_id),
                str(int(max_concurrent)), str(int(ttl_seconds)),
                str(now_ms), "1" if can_inherit_hold else "0",
                self._occupant_identity_prefix(session_id, conference_id),
            ))

        res = await _try()
        if res != -1:
            # P2 — espelha a ocupação desta instância no ZSET do tenant. Aqui, e não no
            # call site, porque `claim_instance` tem 3 chamadores em `router.py` e o
            # quarto que aparecer amanhã não vai lembrar de espelhar.
            await self._sync_tenant_occupancy(tenant_id, instance_id, res)
            return res

        # ── Lotação pode ser mentira: reap antes de aceitar o -1 ──────────────
        # Há DOIS modos de vazamento de vaga, e eles aparecem em lugares
        # diferentes:
        #   1. espelho `current_sessions` sincronizado com o vazamento (mark_busy
        #      rodou depois) → a instância parece cheia e é filtrada em
        #      `get_ready_instances`, que faz o reap lá;
        #   2. espelho DEFASADO (nada sincronizou depois do vazamento) → a
        #      instância parece livre, é selecionada, e a mentira só aparece
        #      AQUI, no confronto com o SCARD.
        # Cobrir só (1) deixaria o contato indo para a fila exatamente como
        # antes. O cooldown é compartilhado com o outro site, então uma rajada de
        # claims não vira uma rajada de reaps.
        if await self._try_take_reap_slot(tenant_id, instance_id):
            if await self.reap_stale_occupants(tenant_id, instance_id):
                res = await _try()
                if res != -1:
                    logger.info(
                        "claim recuperado após reap: tenant=%s instance=%s session=%s "
                        "ocupação=%d — a lotação era vaga órfã",
                        tenant_id, instance_id, session_id, res,
                    )
        if res != -1:
            # O caminho do reap também ocupa vaga — espelhar só o caminho feliz deixaria
            # o contador baixo justamente nos tenants que têm vaga órfã, que são os que
            # mais precisam do número certo.
            await self._sync_tenant_occupancy(tenant_id, instance_id, res)
        return res

    async def swap_to_hold(
        self,
        tenant_id:   str,
        instance_id: str,
        session_id:  str,
        hold_ttl_s:  int,
        set_ttl_s:   int = 86_400,
    ) -> int:
        """Phase 2 — troca a(s) vaga(s) da sessão por um HOLD de wrap-up (net 0).
        Usado no lugar de `release_instance` quando o contato que fecha tem wrap-up
        INLINE seguindo. Retorna a ocupação resultante (mesma de antes, se houve troca).

        Idempotente: sem vaga da origem no SET → nenhum hold é criado.

        F1: a tag de pool do hold é HERDADA do occupant removido (montagem do membro
        acontece no Lua) — por isso nenhum pool_id atravessa `remove_conversation`."""
        expires_at_ms = int(
            datetime.now(timezone.utc).timestamp() * 1000 + int(hold_ttl_s) * 1000
        )
        head = f"{_WRAPUP_HOLD_PREFIX}{session_id}"
        res = await self._redis.eval(
            _SWAP_TO_HOLD_LUA, 1,
            _instance_sessions_key(tenant_id, instance_id),
            self._session_prefix(session_id),
            head, str(int(set_ttl_s)), str(expires_at_ms),
        )
        logger.info(
            "swap_to_hold: instance=%s origin=%s hold_ttl_s=%d expires_at_ms=%d "
            "occupancy=%d (vaga SEGURA para o wrap-up inline)",
            instance_id, session_id, int(hold_ttl_s), expires_at_ms, int(res),
        )
        # P2 — o swap é net 0 na ocupação, então o total do tenant NÃO muda. Espelha
        # assim mesmo: o `ZADD` com o mesmo score é no-op para o contador (delta 0) e
        # mantém o ZSET fiel caso o hold tenha alterado a contagem por algum caminho de
        # borda. Pular por "sei que é zero" é como o drift começa.
        await self._sync_tenant_occupancy(tenant_id, instance_id, int(res))
        return int(res)

    async def release_instance(
        self,
        tenant_id:   str,
        instance_id: str,
        session_id:  str,
    ) -> int:
        """Libera a(s) vaga(s) desta sessão na instância (prefixo "{session_id}::",
        idempotente). Retorna a ocupação restante.

        P1 — a liberação NÃO grava pico (max só sobe na alocação), mas SEMEIA o bucket
        corrente com o valor de ANTES caso ele ainda esteja vazio: é o seed da virada,
        disparado por evento em vez de por relógio, para o pico que sobe e desce
        inteiro entre duas passadas do flusher. Ponto único porque `release_instance` é
        a única porta de saída da vaga (`remove_conversation`, `work_task_release`,
        `work_task_expire` passam todos por aqui) — semear em cada chamador seria
        quatro sítios para errar em silêncio."""
        await self._seed_peaks_before_release(tenant_id, instance_id)
        res = await self._redis.eval(
            _RELEASE_INSTANCE_LUA, 1,
            _instance_sessions_key(tenant_id, instance_id),
            self._session_prefix(session_id),
        )
        await self._sync_tenant_occupancy(tenant_id, instance_id, int(res))
        return int(res)

    async def _try_take_reap_slot(self, tenant_id: str, instance_id: str) -> bool:
        """Cooldown do reap: devolve True no máximo uma vez a cada `_REAP_COOLDOWN_S`
        por instância. `SET NX EX` é atômico, então N decisões de roteamento
        concorrentes disparam UM reap, não N. Falha de Redis → False (não reaper é
        degradação aceitável; o pior caso é a capacidade seguir encolhida, que é o
        estado de hoje)."""
        try:
            return bool(await self._redis.set(
                f"{tenant_id}:instance:{instance_id}:reap_cooldown",
                "1", nx=True, ex=_REAP_COOLDOWN_S,
            ))
        except Exception:
            return False

    async def reap_stale_occupants(
        self, tenant_id: str, instance_id: str
    ) -> int:
        """Remove do semáforo os ocupantes cuja SESSÃO já fechou. Devolve quantos saiu.

        **O problema.** A vaga só é liberada no `agent_done` (`release_instance`). Se a
        sessão morrer por outro caminho — instância apagada debaixo dela, bridge
        reiniciado, contato forçado — o ocupante fica no SET até o EXPIRE de 24 h. O
        agente aparece lotado com `current_sessions: 0` no registro, e o sintoma é
        **capacidade que encolhe em silêncio**: `max_concurrent=3` se comportando como
        `max=1`, indistinguível de config errada de pool. Observado ao vivo 2026-07-28.

        **O sinal.** `session:{sid}:closed` é gravado pelo `ContactClosedHandler` deste
        mesmo serviço em TODO caminho de fechamento (disconnect, timeout, agent_done),
        com TTL de 7 dias — bem maior que as 24 h do semáforo. É afirmação positiva
        ("esta sessão acabou"), não inferência por ausência, e não depende do
        `agent_done` que é justamente o que falha nos casos que vazam.

        Descartadas duas alternativas:
        - reconciliar contra `routing:instance:{iid}:conversations` — é mantido pelo
          MESMO `agent_done`; dois conjuntos que vazam juntos não se corrigem;
        - carimbar `expires_at_ms` no ocupante, como o hold faz — exigiria escolher um
          teto de duração de atendimento, que é POLÍTICA, e um atendimento longo
          legítimo perderia a vaga no meio.

        Holds de wrap-up (`__wrapup_hold__::…`) são preservados: eles têm expiração
        própria e são descartados no `claim_instance`. Aqui só se olha ocupante real.
        """
        key = _instance_sessions_key(tenant_id, instance_id)
        try:
            members = await self._redis.smembers(key)
        except Exception as exc:
            logger.warning(
                "reap: SMEMBERS falhou tenant=%s instance=%s — %s",
                tenant_id, instance_id, exc,
            )
            return 0

        stale: list[str] = []
        for member in members:
            if member.startswith(_WRAPUP_HOLD_PREFIX):
                continue          # hold tem expiração própria (claim_instance)
            session_id = member.split("::", 1)[0]
            if not session_id:
                continue
            try:
                if await self._redis.exists(f"session:{session_id}:closed"):
                    stale.append(member)
            except Exception:
                continue          # na dúvida, NÃO remove: perder vaga é pior que segurar

        if not stale:
            return 0

        await self._redis.srem(key, *stale)
        # Nunca silencioso: cada vaga recuperada aqui é uma que o agent_done deveria
        # ter liberado e não liberou. A frequência disto MEDE o buraco.
        logger.warning(
            "reap: %d vaga(s) órfã(s) recuperada(s) tenant=%s instance=%s occupants=%s "
            "— sessões já fechadas cujo agent_done não liberou a vaga",
            len(stale), tenant_id, instance_id, stale,
        )
        return len(stale)

    async def instance_session_count(self, tenant_id: str, instance_id: str) -> int:
        """Ocupação real da instância (SCARD do SET de sessões). Fonte de verdade
        para os leitores na Fatia B (get_ready_instances/snapshots)."""
        return int(await self._redis.scard(
            _instance_sessions_key(tenant_id, instance_id)
        ))

    # ── Ocupação do pool: DERIVADA do semáforo do recurso (fatia 2) ───────────

    async def compute_pool_occupancy(
        self, tenant_id: str, pool_id: str
    ) -> dict[str, int]:
        """Recompute atômico da ocupação do pool a partir do SET do RECURSO.

        Uma ida e volta (Lua) sobre `ready_set ∪ busy_set`. Ver
        `_RECOMPUTE_POOL_OCCUPANCY_LUA` para a fórmula e para por que nenhum
        contador (`active_count`, `current_sessions`) entra na conta.

        Falha de Redis/script degrada devolvendo zeros **e logando o motivo** — quem
        chama precisa poder distinguir "pool vazio" de "não consegui medir", e é o
        log que faz essa distinção existir.

        `untagged` é DEVOLVIDO aqui e ALERTADO no `write_pool_snapshot` (caminho
        dirigido por evento). Alertar aqui faria o amostrador de ocupação — que roda
        a cada 5 s sobre todos os pools — transformar o aviso em ruído de fundo, e
        aviso que vira ruído deixa de ser aviso.
        """
        try:
            raw = await self._redis.eval(
                _RECOMPUTE_POOL_OCCUPANCY_LUA, 2,
                _pool_instances_key(tenant_id, pool_id),
                _pool_busy_instances_key(tenant_id, pool_id),
                tenant_id, pool_id,
            )
            vals = [int(v) for v in (raw or [])]
        except Exception as exc:
            logger.warning(
                "compute_pool_occupancy FALHOU tenant=%s pool=%s — %s. "
                "O snapshot resultante NÃO mede capacidade; procurar a causa.",
                tenant_id, pool_id, exc,
            )
            vals = []
        vals += [0] * (7 - len(vals))
        occ = {
            "total_capacity": vals[0],
            "used_global":    vals[1],
            "used_here":      vals[2],
            "untagged":       vals[3],
            "instances":      vals[4],
            "evicted":        vals[5],
            "unknown":        vals[6],
        }
        return occ

    # ── Rollup de capacidade do tenant, por tipo de licença (F4, defeito C) ───

    async def compute_tenant_capacity(
        self, tenant_id: str, only_pools: list[str] | None = None
    ) -> dict:
        """Agrega capacidade sobre instâncias DISTINTAS, separadas por tipo de licença.

        `only_pools` restringe o cálculo ao DOMÍNIO do chamador (`accessible_pools` do
        JWT). Precisa ser um parâmetro do cálculo, não um recorte do resultado: depois
        de agregar, a informação de qual instância pertence a qual pool foi consumida —
        do `available: 353` publicado não há como saber quantas daquelas vagas um
        subconjunto de pools alcança. Semântica do escopo: **"quanto os MEUS pools
        alcançam"**, não "quanto é meu" — um recurso logado dentro e fora do domínio
        conta inteiro, porque o domínio de fato o alcança inteiro; que outro pool possa
        consumi-lo antes é `busy_elsewhere`, não uma fatia a descontar aqui.

        `Σ` sobre instâncias distintas de `max(0, max_concurrent − SCARD(sessions))`.
        A dedução por instância é a única forma de não contar o mesmo recurso uma vez
        por pool — e é por isso que este número não sai das linhas de pool.

        **O tipo vem do POOL (`agent_kind`), que é a autoridade canônica** — não de
        `source`/`agent_type_id` da instância, que seriam uma segunda fonte de verdade
        para a mesma pergunta (foi por isso que `agent_group_members.is_human` morreu em
        2026-07-02). Instância cujos pools DISCORDAM de tipo, ou cujo pool não declara
        `agent_kind`, cai no balde **`unknown`** — publicado como tipo próprio, nunca
        dobrado em `human` ou `ai`: dobrar escolheria um lado em silêncio, e o número
        resultante seria plausível e errado.

        `by_channel`: uma instância serve o canal `ch` se ALGUM pool seu declara `ch`.

        > **`by_channel` é PROJEÇÃO, não PARTIÇÃO — nunca somar entre canais.** Uma
        > instância que serve dois canais conta nos dois, então `Σ by_channel.available`
        > excede `available` do tipo (medido no tenant demo: 275 + 286 + 67 = 628 para
        > 353 instâncias). É a mesma falácia de aditividade um nível abaixo, agora
        > dentro do campo criado para consertá-la. O número deduplicado do tipo é
        > `by_kind[k].available`; o de um canal é `by_kind[k].by_channel[ch].available`;
        > não existe soma válida entre canais.

        `pools_available` continua sendo contagem de POOLS com vaga — grandeza aditiva
        legítima, que responde outra pergunta ("há por onde entrar?") e por isso
        sobrevive ao lado do `available` deduplicado. Chaveada por (TIPO, canal): contar
        só por canal fazia `human/whatsapp` publicar 19 num tenant com 2 pools humanos.

        Degradação: falha de Redis devolve `{}` **e loga**. Quem chama precisa poder
        distinguir "tenant sem capacidade" de "não consegui medir" — zero é a resposta
        plausível que apagaria a diferença.
        """
        try:
            pool_ids = list(await self._redis.smembers(_pool_set_key(tenant_id)))
        except Exception as exc:
            logger.warning(
                "rollup de capacidade: SMEMBERS de pools falhou tenant=%s — %s",
                tenant_id, exc,
            )
            return {}
        if only_pools is not None:
            allowed  = set(only_pools)
            pool_ids = [p for p in pool_ids if p in allowed]
        if not pool_ids:
            return {}

        # Uma passada por pool: membership (ready ∪ busy) + config (kind, canais).
        pipe = self._redis.pipeline(transaction=False)
        for pid in pool_ids:
            pipe.smembers(_pool_instances_key(tenant_id, pid))
            pipe.smembers(_pool_busy_instances_key(tenant_id, pid))
            pipe.get(_pool_config_key(tenant_id, pid))
            pipe.get(_pool_snapshot_key(tenant_id, pid))
        try:
            raw = await pipe.execute()
        except Exception as exc:
            logger.warning(
                "rollup de capacidade: pipeline de pools falhou tenant=%s — %s",
                tenant_id, exc,
            )
            return {}

        inst_kinds:    dict[str, set[str]] = {}
        inst_channels: dict[str, set[str]] = {}
        # Chaveado por (TIPO, canal), não só por canal. Dentro de `by_kind.human`, um
        # `pools_available` que contasse pools de IA responderia "há 19 portas humanas"
        # quando há uma — o mesmo erro de fungibilidade que motivou separar as moedas,
        # reintroduzido no campo vizinho. Observado no tenant real em 2026-08-02:
        # human/whatsapp e ai/whatsapp publicando 19 idênticos.
        pools_available_by_kind_ch: dict[tuple[str, str], int] = {}

        for i, pid in enumerate(pool_ids):
            ready, busy, cfg_raw, snap_raw = raw[4 * i: 4 * i + 4]
            kind, channels = None, []
            if cfg_raw:
                try:
                    cfg      = json.loads(cfg_raw) or {}
                    kind     = cfg.get("agent_kind") or None
                    channels = list(cfg.get("channel_types") or [])
                except Exception:
                    pass
            if snap_raw:
                try:
                    snap = json.loads(snap_raw) or {}
                    if (snap.get("available") or 0) > 0:
                        for ch in channels:
                            key = (kind or "unknown", ch)
                            pools_available_by_kind_ch[key] = (
                                pools_available_by_kind_ch.get(key, 0) + 1
                            )
                except Exception:
                    pass
            for iid in set(list(ready or [])) | set(list(busy or [])):
                inst_kinds.setdefault(iid, set()).add(kind or "unknown")
                inst_channels.setdefault(iid, set()).update(channels)

        if not inst_kinds:
            return {}

        instance_ids = sorted(inst_kinds)
        pipe = self._redis.pipeline(transaction=False)
        for iid in instance_ids:
            pipe.get(_instance_key(tenant_id, iid))
            pipe.scard(_instance_sessions_key(tenant_id, iid))
        try:
            raw2 = await pipe.execute()
        except Exception as exc:
            logger.warning(
                "rollup de capacidade: pipeline de instâncias falhou tenant=%s — %s",
                tenant_id, exc,
            )
            return {}

        by_kind: dict[str, dict] = {}
        mixed:   list[str] = []
        for i, iid in enumerate(instance_ids):
            inst_raw, used_raw = raw2[2 * i: 2 * i + 2]
            kinds = inst_kinds[iid]
            if len(kinds) > 1:
                mixed.append(iid)
                kind = "unknown"
            else:
                kind = next(iter(kinds))
            mc = 1
            if inst_raw:
                try:
                    d  = json.loads(inst_raw) or {}
                    mc = int(d.get("max_concurrent") or d.get("max_concurrent_sessions") or 1)
                except Exception:
                    pass
            used  = int(used_raw or 0)
            avail = max(0, mc - used)

            k = by_kind.setdefault(kind, {
                "total_capacity": 0, "used": 0, "available": 0,
                "instances": 0, "by_channel": {},
            })
            k["total_capacity"] += mc
            k["used"]           += used
            k["available"]      += avail
            k["instances"]      += 1
            for ch in inst_channels.get(iid, set()):
                c = k["by_channel"].setdefault(
                    ch, {"available": 0, "instances": 0, "pools_available": 0}
                )
                c["available"] += avail
                c["instances"] += 1

        for kind_name, k in by_kind.items():
            for ch, c in k["by_channel"].items():
                c["pools_available"] = pools_available_by_kind_ch.get((kind_name, ch), 0)

        if mixed:
            # Nunca silencioso: instância em pools de tipos diferentes é contradição de
            # configuração, não caso de borda. Ela consome licença de UMA moeda, e qual
            # delas é indeterminado a partir daqui.
            logger.warning(
                "rollup de capacidade tenant=%s: %d instância(s) em pools de tipos "
                "DIFERENTES (%s) — contadas como `unknown`, não dobradas em human/ai. "
                "É contradição de config: `agent_kind` é fato do pool e a instância "
                "consome uma licença só.",
                tenant_id, len(mixed), ", ".join(sorted(mixed)[:5]),
            )
        if "unknown" in by_kind:
            logger.info(
                "rollup de capacidade tenant=%s: %d instância(s) sem tipo resolvido — "
                "pool sem `agent_kind` no cache de config, ou config ainda não replicada",
                tenant_id, by_kind["unknown"]["instances"],
            )
        out = {
            "tenant_id":   tenant_id,
            "by_kind":     by_kind,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }
        if only_pools is not None:
            # Carimba o recorte: um número escopado que não se anuncia como tal vira
            # "capacidade do tenant" na cabeça de quem lê a tela.
            out["scoped_to_pools"] = sorted(pool_ids)
        return out

    async def get_tenant_capacity(self, tenant_id: str) -> dict | None:
        """Lê o rollup publicado (tenant inteiro). `None` = não publicado ainda."""
        try:
            raw = await self._redis.get(_tenant_capacity_key(tenant_id))
            if not raw:
                return None
            parsed = json.loads(raw)
            return parsed if parsed.get("by_kind") else None
        except Exception:
            return None

    async def refresh_tenant_capacity(
        self, tenant_id: str, force: bool = False
    ) -> dict | None:
        """Recomputa e publica o rollup, respeitando o throttle. Devolve o que gravou.

        `force=True` ignora o cooldown — para teste e para o caminho periódico. O
        cooldown usa `SET NX EX`, atômico, então uma rajada de transições dispara UM
        recompute, não N.
        """
        if not force:
            try:
                got = await self._redis.set(
                    _tenant_capacity_cooldown_key(tenant_id), "1",
                    nx=True, ex=_CAPACITY_ROLLUP_COOLDOWN_S,
                )
                if not got:
                    return None
            except Exception:
                return None   # sem throttle confiável, pular é mais barato que martelar
        roll = await self.compute_tenant_capacity(tenant_id)
        if not roll:
            return None
        try:
            await self._redis.set(
                _tenant_capacity_key(tenant_id), json.dumps(roll),
                ex=_CAPACITY_ROLLUP_TTL_S,
            )
        except Exception as exc:
            logger.warning(
                "rollup de capacidade: escrita falhou tenant=%s — %s", tenant_id, exc
            )
            return None
        return roll

    # ── Ocupação do TENANT: ZSET + contador conferível (P2) ───────────────────

    async def _sync_tenant_occupancy(
        self, tenant_id: str, instance_id: str, occupancy: int
    ) -> int | None:
        """Espelha a ocupação da instância no ZSET do tenant e devolve o total.

        Chamado de DENTRO de `claim_instance`/`release_instance`/`swap_to_hold`, logo
        após o Lua de cada um — um ponto por operação, não um por call site. É fora do
        Lua de propósito: claim e release são **single-key** por decisão (cluster-safe,
        §2 do desenho), e escrever o ZSET lá dentro custaria essa propriedade no código
        de maior consequência da plataforma. O preço é que a atualização não é atômica
        com a reserva da vaga: uma queda entre as duas deixa drift — que é exatamente o
        que a reconciliação de 1×/min existe para encontrar e denunciar.

        Total negativo é impossível se o invariante vale; se aparecer, é drift e vai
        para o log. Não é corrigido aqui: quem conserta antes de contar apaga a
        evidência de que havia o que consertar.
        """
        try:
            total = int(await self._redis.eval(
                _UPDATE_TENANT_OCCUPANCY_LUA, 2,
                _tenant_occupancy_zset_key(tenant_id),
                _tenant_occupancy_total_key(tenant_id),
                instance_id, str(int(occupancy)), str(_TENANT_OCCUPANCY_TTL_S),
            ))
        except Exception as exc:
            logger.warning(
                "ocupação do tenant NÃO sincronizada tenant=%s instance=%s ocupação=%d "
                "— %s. O total do tenant fica defasado até a próxima reconciliação.",
                tenant_id, instance_id, int(occupancy), exc,
            )
            return None
        if total < 0:
            logger.warning(
                "ocupação do tenant NEGATIVA tenant=%s total=%d — impossível pelo "
                "invariante, portanto é DRIFT do contador. A reconciliação corrige no "
                "próximo minuto; a causa é uma atualização perdida entre o Lua da vaga "
                "e este espelho.",
                tenant_id, total,
            )
        return total

    async def reconcile_tenant_occupancy(self, tenant_id: str) -> tuple[int, int] | None:
        """Confere o contador contra a FONTE (soma dos scores do ZSET). Devolve
        `(soma, contador_antes)`; corrige o contador quando divergem, sempre logando.

        **Esta função é o que separa este contador do `active_count` que o arco
        removeu.** Aquele não tinha fonte contra a qual conferir e ninguém o conferia;
        divergia em silêncio e a tela mentia. Se esta reconciliação for removida ou
        deixar de rodar, o contador volta à mesma condição — e deve ser removido junto.
        """
        try:
            raw = await self._redis.eval(
                _RECONCILE_TENANT_OCCUPANCY_LUA, 2,
                _tenant_occupancy_zset_key(tenant_id),
                _tenant_occupancy_total_key(tenant_id),
            )
            soma, contador = int(raw[0]), int(raw[1])
        except Exception as exc:
            logger.warning(
                "reconciliação de ocupação FALHOU tenant=%s — %s. O contador segue sem "
                "conferência; se isto persistir, o total do tenant não é confiável.",
                tenant_id, exc,
            )
            return None
        if soma != contador:
            logger.warning(
                "DRIFT de ocupação tenant=%s: contador=%d, fonte(ZSET)=%d, delta=%d. "
                "Corrigido para a fonte. Drift recorrente significa atualização perdida "
                "entre o Lua da vaga e o espelho — procurar o caminho que não passa por "
                "claim_instance/release_instance/swap_to_hold.",
                tenant_id, contador, soma, contador - soma,
            )
            try:
                await self._redis.set(
                    _tenant_occupancy_total_key(tenant_id), str(soma),
                    ex=_TENANT_OCCUPANCY_TTL_S,
                )
            except Exception as exc:
                logger.warning(
                    "reconciliação: correção do contador falhou tenant=%s — %s",
                    tenant_id, exc,
                )
        return soma, contador

    async def get_tenant_occupancy(self, tenant_id: str) -> int | None:
        """Total corrente de vagas ocupadas no tenant (leitura O(1) do contador).
        `None` = sem valor publicado — desconhecido, nunca zero."""
        try:
            raw = await self._redis.get(_tenant_occupancy_total_key(tenant_id))
            return int(raw) if raw is not None else None
        except Exception:
            return None

    # ── Pico de ocupação: watermark EVENT-DRIVEN (P1) ─────────────────────────

    async def record_pool_peak(
        self,
        tenant_id:  str,
        pool_id:    str,
        value:          int,
        capacity:       int,
        bucket:         str | None = None,
        write_capacity: bool = True,
    ) -> int:
        """Registra `value` como pico do minuto SE for maior que o já registrado.

        Primitivo único de escrita do pico — e são exatamente TRÊS chamadores, cada
        um com uma razão distinta (a regra de gravação está fechada no TODO
        § Pico de ocupação VERDADEIRO):

        1. **ALOCAÇÃO** (`mark_busy`, sobre o `used_here` que o recompute devolveu).
           É o único que faz o pico SUBIR. Liberação nunca cria máximo novo.
        2. **Virada do bucket** (flusher): `max(bucket novo) := ocupação corrente`.
           Sem isto, carga carregada — um minuto que começa alto e só desce, ou sem
           transição alguma — registraria zero, que é o valor plausível que esconde
           justamente a ocupação sustentada.
        3. **Liberação** (`_seed_peaks_before_release`, com o valor de ANTES). Não é
           "gravar max na liberação": é o MESMO seed do item 2, disparado por EVENTO
           em vez de por relógio, para o caso em que a ocupação cai antes de o flusher
           acordar (ele acorda em 00:00.4, a queda foi em 00:00.1 — 300 ms de pico
           perdidos, justo a classe que motivou sair da amostragem).

        > **Nunca chamar isto de dentro de `write_pool_snapshot`.** Se "quem escreve
        > snapshot também sobe o pico", a F3a passa a bumpar em LIBERAÇÕES e o pico
        > volta a ser amostrado nos instantes em que alguém escreve snapshot — sem
        > nada ficar vermelho. O bump mora na costura de alocação, e só nela.

        Falha degrada devolvendo -1 **e logando**: um pico perdido é um buraco na
        série, e um buraco silencioso se lê como "não houve carga".
        """
        b = bucket or minute_bucket()
        try:
            res = await self._redis.eval(
                _RECORD_POOL_PEAK_LUA, 2,
                _pool_peak_key(tenant_id, pool_id, b),
                _pool_peak_cap_key(tenant_id, pool_id, b),
                str(int(value)), str(int(capacity)), str(_PEAK_TTL_S),
                "1" if write_capacity else "0",
            )
            return int(res)
        except Exception as exc:
            logger.warning(
                "record_pool_peak FALHOU tenant=%s pool=%s bucket=%s valor=%d — %s. "
                "O minuto sai da série com pico subestimado; procurar a causa.",
                tenant_id, pool_id, b, int(value), exc,
            )
            return -1

    async def _seed_peaks_before_release(
        self, tenant_id: str, instance_id: str
    ) -> None:
        """Seed por EVENTO (chamador 3 de `record_pool_peak`): antes de derrubar a
        ocupação, garante que o bucket corrente já carrega o valor de ANTES.

        Roda DENTRO de `release_instance`, isto é, **antes** do Lua que remove o
        membro — o valor que interessa é o pré-liberação, e depois ele não existe mais
        em lugar nenhum.

        Atalho honesto: se o bucket já tem valor, não há o que semear. Desde o início
        do minuto a ocupação só pode ter SUBIDO por alocação (que grava) ou DESCIDO
        por liberação (que não cria máximo), logo um bucket já gravado é ≥ a ocupação
        corrente e o `max` do primitivo seria no-op. O `EXISTS` evita o recompute.

        `swap_to_hold` não passa por aqui de propósito: a troca é net 0 (a vaga não é
        devolvida, é segurada pelo hold), então não há queda a preservar.
        """
        try:
            b = minute_bucket()
            # P2 — o `__total__` precisa do MESMO seed por evento: um pico de tenant que
            # sobe e desce entre duas passadas do flusher se perderia igual. Vem antes
            # do laço dos pools porque `pools_of_instance` pode devolver vazio (agente
            # que nunca publicou membership) e sair cedo — e o total não depende disso.
            total = await self.get_tenant_occupancy(tenant_id)
            if total is not None:
                await self.record_pool_peak(
                    tenant_id, "__total__", total, 0,
                    bucket=b, write_capacity=False,
                )
            pools = await self.pools_of_instance(tenant_id, instance_id)
            if not pools:
                return
            for pool_id in pools:
                if await self._redis.exists(_pool_peak_key(tenant_id, pool_id, b)):
                    continue
                occ = await self.compute_pool_occupancy(tenant_id, pool_id)
                await self.record_pool_peak(
                    tenant_id, pool_id,
                    occ["used_here"], occ["total_capacity"], bucket=b,
                )
        except Exception as exc:
            logger.warning(
                "seed de pico na liberação FALHOU tenant=%s instance=%s — %s. "
                "Um pico que subiu e desceu dentro deste minuto pode sair da série.",
                tenant_id, instance_id, exc,
            )

    async def pools_of_instance(
        self, tenant_id: str, instance_id: str
    ) -> list[str]:
        """Pools em que o RECURSO está logado — o alcance do fan-out do snapshot.

        Fonte primária = registro da instância (`pools`), que é a autoridade de
        membership desde 2026-07-28 (o `agent_ready` deixou de ser autoritativo:
        o mcp-server escreve a membership ANTES de publicar, e o Console abre uma
        conexão por pool, sem ordem garantida entre partições). `meta.pools` é
        fallback para agentes que nunca publicaram `agent_ready`.
        """
        try:
            raw = await self.get_instance_raw(tenant_id, instance_id)
            pools = [p for p in (raw or {}).get("pools", []) if p]
            if pools:
                return list(dict.fromkeys(pools))
        except Exception:
            pass
        meta = await self.get_instance_meta(tenant_id, instance_id)
        return list(dict.fromkeys([p for p in (meta.pools if meta else []) if p]))

    async def refresh_snapshots_for_instance(
        self,
        tenant_id:   str,
        instance_id: str,
        extra_pools: list[str] | None = None,
    ) -> dict[str, dict[str, int]]:
        """Fan-out: reescreve o snapshot de TODOS os pools do recurso.

        A mudança estrutural da fatia 2. Antes o refresh era *"o pool roteado"* —
        e por isso, mesmo com a fórmula certa, a linha do pool irmão só seria
        reescrita quando algo o tocasse, que é literalmente o defeito relatado.

        **Só reescreve pool que JÁ tem snapshot.** Um pool sem snapshot não tem de
        onde tirar `sla_target_ms`/`channel_types`, e inventá-los publicaria config
        falsa num registro que o `system_availability_check` lê para decidir oferta
        de canal ao cliente. O bootstrap cria o snapshot inicial de todo pool
        configurado a cada 15 s, então a lacuna se fecha sozinha — e o pulo é logado.

        **Devolve** `{pool_id: occ}` com a ocupação recomputada de cada pool do
        recurso (P1). É DADO, não efeito: quem decide o que fazer com ele é o
        chamador — e só a costura de ALOCAÇÃO (`mark_busy`) o usa para subir o
        watermark do pico. Subir o pico aqui dentro faria a liberação (F3a) bumpar
        também, e o pico voltaria a ser amostrado nos instantes de escrita de
        snapshot, sem nada ficar vermelho.

        O pool PULADO por falta de snapshot também entra no retorno, recomputado à
        parte: o pico é outra grandeza, com outro consumidor, e herdar a heurística
        de "só se já existe snapshot" o faria sumir da série por um motivo que nada
        tem a ver com ele.
        """
        pools = set(await self.pools_of_instance(tenant_id, instance_id))
        pools |= {p for p in (extra_pools or []) if p}
        if not pools:
            logger.warning(
                "fan-out de snapshot SEM pools: tenant=%s instance=%s — nenhuma "
                "linha será atualizada por esta transição de ocupação",
                tenant_id, instance_id,
            )
            return {}
        occupancies: dict[str, dict[str, int]] = {}
        for pool_id in sorted(pools):
            try:
                if not await self._redis.exists(_pool_snapshot_key(tenant_id, pool_id)):
                    logger.info(
                        "fan-out: pool=%s sem snapshot — pulado (o bootstrap cria o "
                        "primeiro; recomputar aqui inventaria sla/channel_types)",
                        pool_id,
                    )
                    occupancies[pool_id] = await self.compute_pool_occupancy(
                        tenant_id, pool_id
                    )
                    continue
                occupancies[pool_id] = await self.refresh_pool_snapshot(
                    tenant_id, pool_id
                )
            except Exception as exc:
                logger.warning(
                    "fan-out: falha ao recomputar snapshot pool=%s tenant=%s — %s",
                    pool_id, tenant_id, exc,
                )

        # F4 — o rollup do tenant é da mesma transição, então este é o gatilho certo:
        # "a ocupação deste RECURSO mudou". Throttled (5 s), e diferente do pico ele
        # roda nos dois sentidos — subida e descida — porque é ocupação corrente, não
        # máximo. Falha aqui não pode derrubar o snapshot, que é o dado principal.
        try:
            await self.refresh_tenant_capacity(tenant_id)
        except Exception as exc:
            logger.warning(
                "fan-out: rollup de capacidade falhou tenant=%s — %s", tenant_id, exc
            )
        return occupancies

    async def get_ready_instances(
        self, tenant_id: str, pool_id: str
    ) -> list[AgentInstance]:
        """Returns instances with state=ready and available capacity."""
        instance_ids = await self._redis.smembers(
            _pool_instances_key(tenant_id, pool_id)
        )
        instances: list[AgentInstance] = []
        for iid in instance_ids:
            raw = await self._redis.get(_instance_key(tenant_id, iid))
            if not raw:
                # Instance key expired (TTL ran out) but ID is still in the pool set.
                # Skip without evicting: removing the stale entry HERE causes an
                # off-by-1 bug in write_pool_snapshot().
                #
                # Problem: when _allocate() calls get_ready_instances() and evicts
                # the stale entry (SREM), the ready_set shrinks from N to N-1 BEFORE
                # mark_busy() fires.  mark_busy() then removes the allocated instance
                # → N-2.  write_pool_snapshot() (called after mark_busy) computes
                # total_instances = len(ready_instances) + at_capacity = (N-2) + 1 = N-1
                # instead of the correct N.  Both available and total appear 1 too low
                # in the Monitor while the session is active.
                #
                # Fix: stale entries are evicted explicitly in write_pool_snapshot()
                # AFTER mark_busy() has already updated the ready_set, so the cleanup
                # cannot race with mark_busy().  The bootstrap heartbeat (every 15 s)
                # also restores expired keys, making stale entries transient (< 15 s).
                continue
            try:
                data = json.loads(raw)
                # Normalise 'status' (mcp-server) → 'state' (internal model)
                if "status" in data and "state" not in data:
                    data["state"] = data["status"]
                inst = AgentInstance.model_validate(data)

                # ── Reap preguiçoso de vagas órfãs (modo 1: espelho cheio) ───
                # Quando o espelho `current_sessions` já reflete o vazamento, a
                # instância é filtrada AQUI e nunca chega ao `claim_instance` —
                # ficaria invisível para sempre. O modo 2 (espelho defasado, a
                # mentira só aparece no confronto com o SCARD) é coberto dentro do
                # próprio `claim_instance`; os dois compartilham o cooldown.
                #
                # Cooldown por instância limita o custo: no máximo um reap a cada
                # `_REAP_COOLDOWN_S`, e só para quem JÁ aparece lotado — o caminho
                # feliz (há vaga) não paga nada.
                if inst.state == "ready" and inst.current_sessions >= inst.max_concurrent:
                    if await self._try_take_reap_slot(tenant_id, iid):
                        if await self.reap_stale_occupants(tenant_id, iid):
                            inst.current_sessions = await self.instance_session_count(
                                tenant_id, iid
                            )

                if inst.state == "ready" and inst.current_sessions < inst.max_concurrent:
                    # Wrap-up unificado (Camada E2, Phase 3): o skip por `wrap_up_pending`
                    # (bloqueio de instância INTEIRA durante o wrap-up inline antigo) foi
                    # REMOVIDO. O wrap-up (inline ou detached) ocupa UMA vaga pelo semáforo
                    # (claim_instance) como qualquer sessão — a capacidade natural cuida do
                    # ACW, sem bloquear as demais vagas do agente (correto p/ max_concurrent>1).
                    instances.append(inst)
            except Exception as exc:
                # Degradação nunca é silenciosa. Um `continue` mudo aqui faz a
                # instância SUMIR do roteamento ("No agents available for this
                # pool") sem deixar rastro — indistinguível de "não há ninguém
                # logado". Custou um diagnóstico; não custa de novo.
                logger.warning(
                    "instância DESCARTADA do pool ready: tenant=%s pool=%s instance=%s — %s",
                    tenant_id, pool_id, iid, exc,
                )
                continue
        return instances

    async def get_instance(
        self, tenant_id: str, instance_id: str
    ) -> AgentInstance | None:
        """Returns an instance by ID.

        `None` tem DOIS significados que o chamador não consegue distinguir
        (`instance_not_found` no claim é o mesmo texto para os dois): a chave não
        existe, ou existe e não valida contra o modelo. Cada um pede uma ação
        diferente — relogar o agente × consertar o produtor do registro. Por isso
        os dois logam, e dizem qual é qual.
        """
        raw = await self._redis.get(_instance_key(tenant_id, instance_id))
        if not raw:
            logger.warning(
                "instância AUSENTE no Redis: tenant=%s instance=%s (chave %s). "
                "Humano: só o login WS cria (ADR adr-human-agent-pool-scoped-identity, F1) "
                "— agente precisa reconectar. IA: TTL de 30 s sem heartbeat.",
                tenant_id, instance_id, _instance_key(tenant_id, instance_id),
            )
            return None
        try:
            data = json.loads(raw)
            if "status" in data and "state" not in data:
                data["state"] = data["status"]
            return AgentInstance.model_validate(data)
        except Exception as exc:
            logger.warning(
                "instância PRESENTE mas INVÁLIDA: tenant=%s instance=%s — %s",
                tenant_id, instance_id, exc,
            )
            return None

    async def get_instance_raw(
        self, tenant_id: str, instance_id: str
    ) -> dict | None:
        """Registro CRU da instância (dict do JSON), sem validar contra o modelo.

        Existe porque `AgentInstance` não carrega `source` — e `source ==
        "human_login"` é justamente o discriminador de que os leitores de
        identidade precisam (ver ADR `adr-human-agent-pool-scoped-identity`).
        Mantém a construção da chave dentro do registry (nenhum chamador monta
        `{tenant}:instance:{iid}` por conta própria).
        """
        raw = await self._redis.get(_instance_key(tenant_id, instance_id))
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    async def set_instance(
        self, instance: AgentInstance
    ) -> None:
        """
        Persists instance state in Redis.

        - AI agents: TTL = instance_ttl_seconds (30s), renewed on each heartbeat.
        - Human agents (source = "human_login" in the existing Redis key): TTL is
          preserved with KEEPTTL.  The mcp-server writes the key with no TTL
          (permanent) and owns the lifetime; overwriting with 30s would expire the
          key and make the orchestrator-bridge unable to read execution_model when
          it processes conversations.routed.
          KEEPTTL on a key with no TTL keeps it permanent.
          KEEPTTL on a missing key creates a key with no TTL — also correct.

        `source` agora É campo do modelo (ver `AgentInstance.source`), então o
        caminho primário de detecção é o próprio objeto; a leitura da chave viva
        permanece como rede de segurança para chamadores que construam a instância
        sem o campo. Antes o modelo não o carregava, e todo round-trip
        `model_validate → model_dump` (`mark_busy`, `remove_conversation`) o
        apagava — a chave humana virava efêmera na primeira alocação.
        """
        key  = _instance_key(instance.tenant_id, instance.instance_id)
        data = instance.model_dump()
        # Alias 'state' → 'status' for mcp-server compatibility
        data["status"] = data.pop("state")

        # Detect human agents: campo do modelo primeiro, chave viva como fallback.
        is_human = instance.source == "human_login"
        try:
            existing_raw = await self._redis.get(key)
            if existing_raw:
                existing = json.loads(existing_raw)
                if existing.get("source") == "human_login":
                    is_human = True
                    # Re-inject source so bridge can still detect it after update.
                    data["source"] = "human_login"
                    # Also preserve execution_model from the original key.
                    # The agent_ready Kafka event may carry execution_model="stateless"
                    # (the kafka_listener default) even for human agents.  If we let
                    # the AgentInstance value overwrite the key, the bridge's fallback-2
                    # check (execution_model == "stateful") will fail and the contact
                    # will never be delivered to the Agent Assist UI.
                    if existing.get("execution_model"):
                        data["execution_model"] = existing["execution_model"]
        except Exception:
            pass

        if is_human:
            # Preserve whatever TTL the mcp-server set (typically none = permanent).
            await self._redis.set(key, json.dumps(data), keepttl=True)
        else:
            await self._redis.set(
                key,
                json.dumps(data),
                ex=self._settings.instance_ttl_seconds,
            )
        # Update the pool instance set if the instance is ready
        for pool_id in instance.pools:
            pool_key = _pool_instances_key(instance.tenant_id, pool_id)
            if data["status"] == "ready":
                await self._redis.sadd(pool_key, instance.instance_id)
            else:
                await self._redis.srem(pool_key, instance.instance_id)
            # Remove from busy set when agent is fully free (ready with no active sessions)
            if data["status"] == "ready" and instance.current_sessions == 0:
                await self._redis.srem(
                    _pool_busy_instances_key(instance.tenant_id, pool_id),
                    instance.instance_id,
                )

    async def remove_from_pool_sets(
        self, tenant_id: str, instance_id: str, pools: list[str]
    ) -> None:
        """Remove a instância dos SETs de roteamento dos pools indicados.

        `set_instance` só percorre os pools que a instância AINDA declara — então
        um pool do qual ela saiu nunca era limpo por ele. No logout parcial de
        humano quem limpava era o `unregisterHumanAgent` (mcp-server) por escrita
        direta; se o evento chegasse sem essa escrita, a instância continuava
        alocável num pool do qual já tinha saído. Idempotente (SREM).
        """
        for pool_id in pools:
            if not pool_id:
                continue
            await self._redis.srem(_pool_instances_key(tenant_id, pool_id), instance_id)
            await self._redis.srem(
                _pool_busy_instances_key(tenant_id, pool_id), instance_id
            )

    # ── Instance meta (no TTL) ────────────────────────────────────────────────

    async def update_instance_meta(
        self, tenant_id: str, instance_id: str, pools: list[str], agent_type_id: str
    ) -> None:
        """
        Persiste o meta da instância (sem TTL). Chamado no `agent_ready`.

        ATENÇÃO — o docstring antigo dizia *"pools and agent_type_id do not change
        during the instance lifetime"*. **É FALSO para agentes humanos**, e era a
        premissa sobre a qual `remove_conversation` e `crash_detector` foram
        construídos (ambos escolhem um pool a partir daqui):

        - `pools` MUDA a cada login/logout parcial — um humano entra e sai de pools
          o tempo todo pelo Console, com a MESMA instância `human-{userId}`;
        - `agent_type_id` de humano nem sequer é um fato do recurso: é função do
          pool (`human_agent_{pool}`), e o valor guardado aqui é o resíduo de um
          login qualquer. Ver `resolve_agent_type` em `models.py` (F2 do ADR
          `adr-human-agent-pool-scoped-identity`).

        Portanto este meta é um CACHE do conjunto de membership no instante do
        último `agent_ready` — não uma constante. Quem precisa do pool de um
        ATENDIMENTO específico lê `session:{sid}:routing:{iid}` (o único fato
        por-(sessão, instância)), e é por isso que a F3 inverteu a precedência em
        `remove_conversation`: o `pools` do evento vence o `meta.pools`.
        """
        await self._redis.hset(
            _instance_meta_key(tenant_id, instance_id),
            mapping={"pools": json.dumps(pools), "agent_type_id": agent_type_id},
        )

    async def add_conversation(
        self, tenant_id: str, instance_id: str, conversation_id: str
    ) -> None:
        """
        Registers an active conversation on the instance.
        Called on agent_busy. SADD is atomic — no race condition.
        """
        await self._redis.sadd(
            _instance_conversations_key(tenant_id, instance_id), conversation_id
        )

    async def remove_conversation(
        self, tenant_id: str, instance_id: str, conversation_id: str,
        fallback_pools: list[str] | None = None,
        hold_for_wrapup: bool = False,
        hold_ttl_s: int = 90,
    ) -> None:
        """
        Removes a completed conversation from the instance.
        Called on agent_done. SREM is atomic — no race condition.

        Also deletes the session:serving:pool key so that reconnects or
        re-routings of this session_id start with a clean slate and the
        same-pool re-entry guard in mark_busy does not fire spuriously.

        fallback_pools: pool list to use when instance_meta is absent (e.g.
        human agents in demo mode that never published agent_ready and therefore
        have no persisted meta). The bridge includes pools[] in every agent_done
        payload it synthesises, so this covers the human-agent counter decrement.

        hold_for_wrapup (Phase 2): quando o contato que fecha tem wrap-up INLINE
        seguindo (flag `keep_slot_for_wrapup` no evento agent_done, carimbado pelo
        bridge que conhece os hooks do pool), a vaga NÃO é liberada — é trocada por
        um HOLD (swap net 0) que o auto-claim do wrap-up herda. Elimina a janela em
        que um push tomaria a vaga a max_concurrent=1. Todo o resto do fluxo abaixo
        (espelho current_sessions, state, membership dos SETs, exclusão guardada do
        serving_pool, fan-out do snapshot) é IDÊNTICO — só o membro do SET muda.
        """
        await self._redis.srem(
            _instance_conversations_key(tenant_id, instance_id), conversation_id
        )
        # NOTE: serving-pool deletion is deferred below — inside the try block —
        # so we can guard it against conference specialists wiping the primary
        # contact's serving_pool key.  See guarded delete after pools_to_decr.

        # Quais pools este contato tocou — usado para a membership dos SETs.
        # ── F3 — qual pool o contato serviu é fato POR-SESSÃO, não por-recurso ───
        # (ADR adr-human-agent-pool-scoped-identity)
        #
        # A precedência era `meta.pools` (per-RECURSO, e portanto o conjunto
        # INTEIRO de pools do agente) na frente do `pools` do evento. Para um
        # humano multi-pool isso decrementava o `active_count` de pools que não
        # serviram este contato: o pool que serviu ficava com carga fantasma (fila
        # não drena) e os outros iam a zero. O evento `agent_done` é emitido por
        # quem sabe QUAL pool serviu esta sessão — é a fonte no escopo certo.
        #
        # NOTA (fatia 2): a classe de defeito que a F3 corrigia deixou de existir
        # aqui. Não há mais contador a decrementar — `busy` é derivado da TAG do
        # membro do semáforo, então o pool que serviu é lido do próprio dado. A
        # precedência sobrevive porque ainda decide a MEMBERSHIP dos SETs (ready/
        # busy), e errá-la ainda tira o agente do pool errado.
        #
        # `meta.pools` continua como fallback para o caso que o motivou (agentes
        # que nunca publicaram agent_ready e cujo agent_done não traz `pools`).
        try:
            meta = await self.get_instance_meta(tenant_id, instance_id)
            pools_to_decr = (fallback_pools or []) or (meta.pools if meta else [])

            # Phase 2 (runs FIRST, before the pools gate): Decrement current_sessions
            # in the instance key and restore state=ready when the agent drops below
            # max_concurrent capacity.  This MUST run even when pools_to_decr is empty
            # (e.g. YAML-fallback agents that never published agent_ready to Kafka and
            # therefore have no instance_meta, and whose agent_done event omits pools).
            # Without this early update the instance stays stuck as status=busy forever,
            # causing stale busy instances to accumulate across sessions and degrading
            # the Monitor's "available" counter on every new contact.
            new_current_sessions: int | None = None
            new_state: str | None = None
            inst_pools: list[str] = []
            try:
                inst_key = _instance_key(tenant_id, instance_id)
                raw_inst = await self._redis.get(inst_key)
                if raw_inst:
                    inst_data = json.loads(raw_inst)
                    # Normalise status → state alias (mcp-server compat)
                    if "status" in inst_data and "state" not in inst_data:
                        inst_data["state"] = inst_data["status"]
                    inst = AgentInstance.model_validate(inst_data)
                    # Capture pools from instance data as ultimate fallback (used below
                    # when neither instance_meta nor the agent_done event carry pools).
                    inst_pools = list(inst.pools or [])
                    old_sessions = inst.current_sessions
                    # Fatia B: libera a vaga ATOMICAMENTE (release por prefixo de sessão)
                    # e sincroniza o espelho current_sessions com a fonte de verdade (SCARD).
                    # Substitui o `-= 1` não-atômico (mesma classe de lost-update do mark_busy).
                    # conversation_id == session_id → release_instance remove "{session_id}::*".
                    # Phase 2: com wrap-up inline seguindo, TROCA a vaga por um hold
                    # (net 0) em vez de liberar — a ocupação não oscila.
                    if hold_for_wrapup:
                        remaining = await self.swap_to_hold(
                            tenant_id, instance_id, conversation_id, hold_ttl_s,
                        )
                    else:
                        remaining = await self.release_instance(
                            tenant_id, instance_id, conversation_id,
                        )
                    inst.current_sessions = remaining
                    if inst.current_sessions < inst.max_concurrent:
                        inst.state = "ready"
                    new_current_sessions = inst.current_sessions
                    new_state = inst.state
                    out = inst.model_dump()
                    out["status"] = out.pop("state")
                    # Preserve human-agent source field so bridge detection still works
                    if "source" in inst_data:
                        out["source"] = inst_data["source"]
                    await self._redis.set(inst_key, json.dumps(out), keepttl=True)
                    logger.info(
                        "remove_conversation: instance=%s current_sessions=%d→%d state=%s",
                        instance_id, old_sessions, inst.current_sessions, inst.state,
                    )
            except Exception as exc:
                logger.warning(
                    "remove_conversation: failed to update instance state for %s: %s",
                    instance_id, exc,
                )

            # Effective pools: prefer meta/event pools; fall back to instance data pools.
            # inst_pools is the last resort so we can still SADD/SREM the pool sets and
            # patch the snapshot even when the agent_done event has no pools field.
            effective_pools = pools_to_decr or inst_pools

            # ── Guarded serving-pool deletion ─────────────────────────────────
            # Only delete the serving_pool key if it currently points to one of
            # the pools this contact actually touched.  A conference specialist (e.g.
            # auth_form_ia) shares the same session_id as the primary contact; if
            # we delete unconditionally we wipe the primary's "retencao_humano"
            # entry, and o primário perde o discriminador de re-entrada no mesmo pool
            # (guard do `mark_busy`) e a pista de qual linha reescrever.
            serving_key = _session_serving_pool_key(tenant_id, conversation_id)
            raw_sp = await self._redis.get(serving_key)
            sp_val = (raw_sp.decode() if isinstance(raw_sp, bytes) else raw_sp) if raw_sp else None
            if sp_val is not None:
                # strip optional "queued:" prefix used during queue waits
                sp_clean = sp_val[len("queued:"):] if sp_val.startswith("queued:") else sp_val
                if sp_clean in effective_pools:
                    await self._redis.delete(serving_key)
                    logger.info(
                        "remove_conversation: deleted serving_pool key session=%s pool=%s",
                        conversation_id, sp_clean,
                    )
                else:
                    logger.info(
                        "remove_conversation: SKIPPED serving_pool delete "
                        "session=%s current_pool=%s our_pools=%s "
                        "(conference specialist — primary contact owns this key)",
                        conversation_id, sp_clean, effective_pools,
                    )
            else:
                # Key absent (already cleaned up or never set) — safe no-op
                pass
            # ──────────────────────────────────────────────────────────────────
            logger.info(
                "remove_conversation: tenant=%s instance=%s conv=%s "
                "meta_pools=%s fallback_pools=%s inst_pools=%s effective_pools=%s",
                tenant_id, instance_id, conversation_id,
                meta.pools if meta else None,
                fallback_pools,
                inst_pools,
                effective_pools,
            )
            if not effective_pools:
                logger.warning(
                    "remove_conversation: NO pools found for "
                    "tenant=%s instance=%s conv=%s — membership dos SETs não "
                    "atualizada (estado da instância já foi resetado acima); os "
                    "snapshots ainda serão recomputados pelo fan-out do recurso",
                    tenant_id, instance_id, conversation_id,
                )

            # Phase 3: pool set membership.
            #
            # Fatia 2 — o DECR de `active_count` e o patch `available += 1` sumiram
            # daqui. Eram REMENDO: somavam/subtraíam sobre o número anterior em vez
            # de recalcular, e por isso precisavam de chão (DECR negativo) e de teto
            # (`available 4 / total 3`, corrigido em 2026-07-30). Um recompute
            # derivado do SET não pode ultrapassar a capacidade nem ficar negativo —
            # os dois clamps deixaram de existir porque a condição deixou de existir.
            for pool_id in effective_pools:
                pool_key = _pool_instances_key(tenant_id, pool_id)
                busy_key = _pool_busy_instances_key(tenant_id, pool_id)

                # Restore ready_set membership if agent is now below capacity
                if new_state == "ready":
                    await self._redis.sadd(pool_key, instance_id)
                # Remove from busy_set when fully idle
                if new_current_sessions == 0:
                    await self._redis.srem(busy_key, instance_id)

            # Fan-out: a vaga devolvida é do RECURSO, então TODOS os pools em que ele
            # está logado voltaram a ter capacidade — não só o que serviu. É o
            # simétrico exato do fan-out do `mark_busy`.
            await self.refresh_snapshots_for_instance(
                tenant_id, instance_id, extra_pools=effective_pools,
            )

        except Exception as exc:
            logger.error(
                "remove_conversation: FAILED tenant=%s instance=%s conv=%s — %s",
                tenant_id, instance_id, conversation_id, exc, exc_info=True,
            )

    async def get_instance_meta(
        self, tenant_id: str, instance_id: str
    ) -> InstanceMeta | None:
        """
        Returns persistent instance metadata.
        Returns None if the instance was never registered via agent_ready.
        """
        meta_key  = _instance_meta_key(tenant_id, instance_id)
        conv_key  = _instance_conversations_key(tenant_id, instance_id)

        raw_meta  = await self._redis.hgetall(meta_key)
        if not raw_meta:
            return None

        # Decode bytes keys/values — hgetall returns bytes when decode_responses=False.
        # This ensures .get("pools") and .get("agent_type_id") work regardless of
        # the Redis client configuration.
        decoded_meta: dict[str, str] = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in raw_meta.items()
        }

        conversations_raw = await self._redis.smembers(conv_key)
        # Decode bytes from smembers — same reason as above.
        conversations = [
            v.decode() if isinstance(v, bytes) else v
            for v in conversations_raw
        ]

        return InstanceMeta(
            pools                = json.loads(decoded_meta.get("pools", "[]")),
            agent_type_id        = decoded_meta.get("agent_type_id", ""),
            active_conversations = conversations,
        )

    async def delete_instance_meta(
        self, tenant_id: str, instance_id: str
    ) -> None:
        """
        Removes instance metadata and its conversations set.
        Called by CrashDetector after recovering orphaned conversations.
        """
        await self._redis.delete(
            _instance_meta_key(tenant_id, instance_id),
            _instance_conversations_key(tenant_id, instance_id),
        )

    async def get_session_serving_pool(
        self, tenant_id: str, session_id: str
    ) -> str | None:
        """
        Returns the pool_id currently serving this session (from the serving_pool key).
        Returns None if the session has no active allocation.
        Strips the 'queued:' prefix used during queue waits.
        Used by the router to detect same-pool re-entry for conference events.
        """
        raw = await self._redis.get(_session_serving_pool_key(tenant_id, session_id))
        if not raw:
            return None
        val = raw.decode() if isinstance(raw, bytes) else raw
        return val[len("queued:"):] if val.startswith("queued:") else val

    async def get_session_affinity(self, session_id: str) -> str | None:
        """
        Returns instance_id with affinity for the session (stateful agents).
        Spec 4.6: Routing Engine guarantees session affinity for stateful agents.
        """
        return await self._redis.get(_session_instance_key(session_id))

    async def set_session_affinity(
        self, session_id: str, instance_id: str, ttl_seconds: int = 86_400
    ) -> None:
        await self._redis.set(
            _session_instance_key(session_id), instance_id, ex=ttl_seconds
        )

    async def mark_busy(
        self,
        tenant_id:  str,
        pool_id:    str,
        instance_id: str,
        session_id: str | None = None,
    ) -> None:
        """
        Sincroniza o espelho `current_sessions`/`state` da instância com o semáforo,
        acerta a membership dos SETs do pool e recomputa os snapshots do RECURSO.

        **Fatia 2** — não há mais INCR de contador aqui. A vaga já foi reservada
        atomicamente pelo `claim_instance` (antes desta chamada) e a ocupação por
        pool é derivada da TAG do membro. O que sobrou é espelho, membership e o
        FAN-OUT do snapshot sobre `pools(instance) ∪ {pool_id}` — sem ele, mesmo com
        a fórmula certa, a linha do pool irmão só seria reescrita quando algo o
        tocasse, que é o defeito relatado.

        session_id (optional) — when provided, enables three guards:

        0. Closed-session guard: if the session is already closing/closed
           (session:{id}:close_fired or session:{id}:closed keys exist), mark_busy
           is a no-op.  The primary guard lives in _process_message() (routing engine
           main.py); this is a belt-and-suspenders second layer for tight races.

        1. Same-pool re-entry guard: quando `prev_pool == pool_id` a sessão já é
           servida por este pool (especialista volta e `_try_affinity` re-roteia o
           primário para o mesmo pool). Nada muda de estado — mas o fan-out roda
           assim mesmo, porque o claim que precedeu pode ter mexido na ocupação.

        2. Cross-pool transfer: sessão antes servida por OUTRO pool (escalação /
           agent_transfer). O `claim_instance` já fez o RE-TAG do membro (contagem
           inalterada); aqui o pool de origem só entra na lista do fan-out — pode
           não estar em `pools(instance)` se for de outro agente.

        Uses KEEPTTL to preserve the original instance TTL (see comment below).
        """
        # Guard 0 — belt-and-suspenders closed-session check (primary guard is in
        # _process_message in main.py; this catches the tight-race window).
        if session_id:
            # Guard 0 — belt-and-suspenders closed-session check.
            # Primary guard is in _process_message in main.py; this catches tight-race windows.
            #
            # NOTE: Do NOT add hook_pending keys here. When on_human_end/post_human hooks
            # fire, the hook agents (NPS, wrap-up) are themselves routed via mark_busy with
            # the same session_id. Adding hook_pending to this guard would block those
            # legitimate hook-agent allocations. The hook-phase reconnect guard lives
            # exclusively in channel-gateway/adapters/webchat.py (5-key exists() check).
            is_closing = await self._redis.exists(
                f"session:{session_id}:close_fired",
                f"session:{session_id}:closed",
            )
            if is_closing:
                logger.warning(
                    "mark_busy: skipping INCR for already-closing session=%s pool=%s",
                    session_id, pool_id,
                )
                return

        prev_pool_for_refresh: str | None = None

        if session_id:
            serving_key = _session_serving_pool_key(tenant_id, session_id)
            prev_raw    = await self._redis.getset(serving_key, pool_id)
            await self._redis.expire(serving_key, 86_400)   # 24h TTL

            # Normalise bytes → str (Redis client may return bytes when
            # decode_responses is not set on the connection pool).
            if isinstance(prev_raw, bytes):
                prev_raw = prev_raw.decode()

            if prev_raw:
                if prev_raw.startswith("queued:"):
                    # A sessão estava PARQUEADA na fila deste pool
                    # (release_session_from_pool), ainda sem vaga tomada. Não é
                    # re-entrada: segue o caminho normal. O sentinela "queued:"
                    # existe justamente para distinguir "esperando na fila deste
                    # pool" de "já sendo servida por este pool" — sem ele o guard
                    # abaixo dispararia e o contato sairia da fila sem ser contado.
                    pass
                elif prev_raw == pool_id:
                    # True same-pool re-entry: a sessão já é servida por ESTE pool
                    # (ex.: especialista volta → _try_affinity dispara → mark_busy
                    # de novo no pool primário). Nada muda de estado — mas o
                    # snapshot é recomputado assim mesmo: o claim que precedeu esta
                    # chamada pode ter mexido na ocupação do recurso, e sair daqui
                    # sem reescrever devolveria a linha antiga.
                    await self._bump_peaks(
                        tenant_id,
                        await self.refresh_snapshots_for_instance(
                            tenant_id, instance_id, extra_pools=[pool_id],
                        ),
                    )
                    return
                else:
                    # Cross-pool transfer: a sessão era servida por outro pool. O
                    # RE-TAG do membro já foi feito pelo `claim_instance`; guardamos
                    # o pool de origem só para incluí-lo no fan-out do snapshot.
                    prev_pool_for_refresh = prev_raw

        key = _instance_key(tenant_id, instance_id)
        raw = await self._redis.get(key)
        if not raw:
            return
        data = json.loads(raw)
        if "status" in data and "state" not in data:
            data["state"] = data["status"]
        inst = AgentInstance.model_validate(data)
        # Fatia B: a reserva da vaga já foi feita atomicamente por claim_instance
        # (no decide/route, ANTES deste mark_busy). Aqui apenas SINCRONIZAMOS o espelho
        # current_sessions do JSON com a fonte de verdade (SCARD do SET de occupants) —
        # não incrementamos mais (o `+= 1` não-atômico era a causa do lost update).
        inst.current_sessions = await self.instance_session_count(tenant_id, instance_id)
        if inst.current_sessions >= inst.max_concurrent:
            inst.state = "busy"

        # Serialize and update in Redis — preserve the existing TTL.
        # KEEPTTL: production agents renew TTL via agent_busy/heartbeat; seeded
        # instances have 24h TTL that must not be overwritten with the default 30s.
        out = inst.model_dump()
        out["status"] = out.pop("state")   # alias for mcp-server compat
        await self._redis.set(key, json.dumps(out), keepttl=True)

        # Sync pool membership: remove if at capacity or not ready
        pool_key = _pool_instances_key(tenant_id, pool_id)
        if inst.state != "ready" or inst.current_sessions >= inst.max_concurrent:
            await self._redis.srem(pool_key, instance_id)
        # (no sadd needed — the instance was already in the set before mark_busy)

        # Track busy instances (for membership visibility)
        await self._redis.sadd(_pool_busy_instances_key(tenant_id, pool_id), instance_id)

        # ── Fatia 2 — recompute com FAN-OUT, no lugar do INCR do contador ────────
        # O INCR de `{t}:pool:{p}:active_count` (e o DECR do pool anterior na
        # transferência cross-pool) sumiram: `busy` agora é derivado da tag no
        # membro do semáforo, e a transferência é um RE-TAG feito pelo próprio
        # `claim_instance` — contagem inalterada, nada a decrementar.
        #
        # O fan-out cobre os pools IRMÃOS: a vaga consumida aqui é do RECURSO, e sem
        # reescrever a linha deles o defeito A continuaria, agora com a fórmula
        # certa escrita no lugar errado. `prev_pool_for_refresh` entra na lista porque
        # pode ser um pool onde o recurso NÃO está logado (transferência entre pools
        # de agentes diferentes) — nesse caso `pools_of_instance` não o alcança.
        occupancies = await self.refresh_snapshots_for_instance(
            tenant_id, instance_id,
            extra_pools=[pool_id] + ([prev_pool_for_refresh] if prev_pool_for_refresh else []),
        )

        # ── P1 — o pico sobe AQUI, e só aqui ─────────────────────────────────────
        # Esta é a costura de ALOCAÇÃO: a vaga acabou de ser reservada pelo
        # `claim_instance` (3 sítios, todos em `router.py`, todos seguidos deste
        # `mark_busy` — cobertura de subida 100%). O valor é o `used_here` que o
        # recompute JÁ devolveu, então nenhuma conta é refeita e nenhum relógio entra
        # na medição: o pico passa a ser o máximo da própria função escada, em vez de
        # um máximo de amostras que pode cair inteiro entre duas subidas.
        await self._bump_peaks(tenant_id, occupancies)

    async def _bump_peaks(
        self, tenant_id: str, occupancies: dict[str, dict[str, int]]
    ) -> None:
        """Sobe o watermark de cada pool do fan-out **e o do tenant**. Só `mark_busy`."""
        b = minute_bucket()
        for pool_id, occ in (occupancies or {}).items():
            await self.record_pool_peak(
                tenant_id, pool_id,
                occ.get("used_here", 0), occ.get("total_capacity", 0), bucket=b,
            )
        # P2 — o `__total__` do tenant tem watermark PRÓPRIO, e não pode ser derivado
        # dos watermarks por pool: `max` de SOMAS ≠ soma de `max`. A série de
        # 2026-08-02 é a prova — quatro pools com pico 1 no mesmo minuto e total 2,
        # porque os picos foram em instantes diferentes. O valor vem do contador
        # conferível (`{t}:occupancy:total`), leitura O(1).
        total = await self.get_tenant_occupancy(tenant_id)
        if total is not None:
            await self.record_pool_peak(
                tenant_id, "__total__", total, 0,
                bucket=b, write_capacity=False,
            )

    async def release_session_from_pool(
        self,
        tenant_id:   str,
        session_id:  str,
        new_pool_id: str | None = None,
    ) -> None:
        """
        Releases the active-count claim from the session's current pool when the
        session moves to a queue without an agent allocation (escalation to queue).

        Called by router.route() when a contact cannot be immediately allocated and
        is placed in a pool's queue.  Detects whether the session was previously
        served by a different pool (via _session_serving_pool_key) and decrements
        that pool's active counter.

        Setting new_pool_id claims the session for the destination pool's queue so
        that a subsequent mark_busy (when the contact is dequeued and allocated)
        sees the session already at pool_id and does not double-decrement.

        If the session was never served (first contact, no previous pool), this is
        a safe no-op: GETSET returns None and no counter is touched.
        """
        serving_key = _session_serving_pool_key(tenant_id, session_id)
        if new_pool_id:
            # Write a "queued:" sentinel (not the bare pool_id) so that a
            # subsequent mark_busy can distinguish "session is parked in queue,
            # sem vaga tomada" from "session is already being served by this pool".
            # Using the bare pool_id caused the same-pool re-entry guard in
            # mark_busy to fire as a no-op, e o contato saía da fila sem que o
            # espelho/membership do pool fossem acertados.
            prev_pool = await self._redis.getset(serving_key, f"queued:{new_pool_id}")
            await self._redis.expire(serving_key, 86_400)   # 24h TTL
        else:
            prev_pool = await self._redis.get(serving_key)

        # Normalise bytes → str
        if isinstance(prev_pool, bytes):
            prev_pool = prev_pool.decode()

        # Strip "queued:" prefix if present (written by a previous queuing cycle).
        # This can occur on re-queuing: session was queued, re-routed on a new
        # event, and is being queued again before ever being allocated.
        _QUEUED_PFX = "queued:"
        actual_prev = prev_pool[len(_QUEUED_PFX):] if prev_pool and prev_pool.startswith(_QUEUED_PFX) else prev_pool

        if actual_prev and actual_prev != new_pool_id:
            # Fatia 2 — recompute em vez de DECR+patch. A vaga em si é liberada pelo
            # `release_instance`/`remove_conversation` (semáforo do recurso); aqui só
            # se reescreve a linha do pool de origem, que deixou de servir a sessão.
            # Sem instância em escopo (esta função só conhece a sessão) o fan-out por
            # recurso não se aplica: é um pool só.
            try:
                if await self._redis.exists(_pool_snapshot_key(tenant_id, actual_prev)):
                    await self.refresh_pool_snapshot(tenant_id, actual_prev)
            except Exception as exc:
                logger.warning(
                    "release_session_from_pool: falha ao recomputar snapshot "
                    "pool=%s session=%s — %s", actual_prev, session_id, exc,
                )

    async def add_queued_contact(
        self,
        tenant_id:    str,
        pool_id:      str,
        session_id:   str,
        contact_data: dict,
        queued_at_ms: int,
        ttl:          int = 14_400,
    ) -> bool:
        """
        Persist a queued contact.
        Sorted set score = queued_at_ms (lowest = oldest = served first for FIFO
        base, though queue_scorer may override with priority).
        Full event JSON is stored separately so it can be re-published verbatim
        to conversations.inbound when the contact is dequeued.

        Returns True if the contact was newly added, False if it was already in the
        queue (re-queue from periodic drain). Callers use this to suppress duplicate
        "waiting" notifications to the customer.
        """
        # Camada B (pull direcionado): se o item é reservado a um recurso
        # (assigned_to) e ainda não tem a âncora da janela, carimba assigned_at_ms
        # AGORA (1º enqueue). Como contact_data é persistido/re-passado verbatim,
        # a âncora sobrevive ao re-enfileiramento (release/rollback/re-rota) → a
        # janela de reserva conta desde a atribuição, não reinicia a cada requeue.
        if contact_data.get("assigned_to") and not contact_data.get("assigned_at_ms"):
            contact_data["assigned_at_ms"] = int(queued_at_ms)

        # I5 — o JSON não pode morrer ANTES do item. O TTL default (4 h) foi pensado
        # para contato de cliente; um item de trabalho de `delegate` vive até o
        # `timeout_hours` do step (24 h no wrap-up). Com o JSON expirado o membro do
        # ZSET sobrevive sozinho e o item passa a MENTIR: segue listado na inbox, sem
        # `assigned_to` (perde o author-binding, que é a razão de ser da fila interna)
        # e irreivindicável — `work_task_claim` lê o JSON e devolve `not_in_queue`.
        # Nada erra; o item só deixa de ser o que diz ser. Prazo ausente = default.
        effective_ttl = int(ttl)
        _deadline = contact_data.get("work_item_deadline") or ""
        if _deadline:
            try:
                _dl = datetime.fromisoformat(str(_deadline).replace("Z", "+00:00"))
                _remaining = int((_dl - datetime.now(timezone.utc)).total_seconds())
                # +1 h de folga: o expire da I5 precisa do JSON para saber o que limpar.
                effective_ttl = max(effective_ttl, _remaining + 3600)
            except Exception as _e:
                logger.warning(
                    "add_queued_contact: work_item_deadline inválido (%r) session=%s — "
                    "usando TTL default de %ds: %s",
                    _deadline, session_id, effective_ttl, _e,
                )

        added = await self._redis.zadd(
            _queue_key(tenant_id, pool_id), {session_id: queued_at_ms}
        )
        # Redis ZADD returns the number of NEW elements added (0 if already existed)
        newly_added = bool(added)
        await self._redis.set(
            _queue_contact_key(tenant_id, session_id),
            json.dumps(contact_data),
            ex=effective_ttl,
        )
        # P3 — wall-clock da PRIMEIRA vez na fila (NX: re-enfileiramentos — ex.:
        # devolução via work_task_release, re-rota no drain — NÃO sobrescrevem).
        # O score do sorted set (queued_at_ms) é reordenado no re-enqueue; este
        # carimbo preserva a espera REAL do contato para o display do inbox
        # (listQueue lê e cai em queued_at_ms se ausente/expirado). Mesmo padrão/
        # chave da fila muda (mute_queue.mark_mute_queued). TTL 7d; UUID por sessão
        # → sem colisão, limpeza por TTL (não precisa delete no claim/resolve).
        await self._redis.set(
            first_queued_key(tenant_id, session_id),
            str(int(queued_at_ms)),
            nx=True,
            ex=_FIRST_QUEUED_TTL_S,
        )
        # Patch queue_length in the pool snapshot in-place so the Monitor
        # tile reflects the new queue position immediately, without waiting
        # for the next routing event to call write_pool_snapshot.
        # (write_pool_snapshot is scheduled via create_task inside router.route()
        # BEFORE this ZADD executes, so the snapshot would show stale length 0.)
        try:
            snap_key = _pool_snapshot_key(tenant_id, pool_id)
            raw_snap = await self._redis.get(snap_key)
            if raw_snap:
                snap = json.loads(raw_snap)
                snap["queue_length"] = await self._redis.zcard(
                    _queue_key(tenant_id, pool_id)
                )
                await self._redis.set(snap_key, json.dumps(snap), keepttl=True)
        except Exception:
            pass  # non-critical; self-corrects on next routing event

        return newly_added

    async def remove_queued_contact(
        self, tenant_id: str, pool_id: str, session_id: str
    ) -> None:
        """Remove contact from sorted set and delete stored JSON."""
        await self._redis.zrem(_queue_key(tenant_id, pool_id), session_id)
        await self._redis.delete(_queue_contact_key(tenant_id, session_id))
        # Patch queue_length in snapshot in-place (mirrors add_queued_contact).
        try:
            snap_key = _pool_snapshot_key(tenant_id, pool_id)
            raw_snap = await self._redis.get(snap_key)
            if raw_snap:
                snap = json.loads(raw_snap)
                snap["queue_length"] = await self._redis.zcard(
                    _queue_key(tenant_id, pool_id)
                )
                await self._redis.set(snap_key, json.dumps(snap), keepttl=True)
        except Exception:
            pass

    async def atomic_claim_dequeue(
        self, tenant_id: str, pool_id: str, session_id: str
    ) -> bool:
        """
        Frente 1 (pull claim): remoção ATÔMICA do contato da fila do pool (ZREM de
        um membro específico = "um único vencedor", sem lock distribuído).
        Retorna True se ESTE chamador removeu (venceu o claim); False se já não
        estava na fila (outro agente levou / já saiu).

        NÃO apaga o JSON do contato (`_queue_contact_key`): em sucesso o caller o
        mantém para o release re-enfileirar; em rollback (sem capacidade) o caller
        re-`add_queued_contact`. O JSON expira por TTL no fim de vida do contato.
        """
        removed = await self._redis.zrem(_queue_key(tenant_id, pool_id), session_id)
        return bool(removed)

    async def write_claim_lease(
        self, tenant_id: str, pool_id: str, session_id: str,
        instance_id: str, ttl_seconds: int,
    ) -> None:
        """Frente 1 (pull): grava/renova a lease do claim (TTL curto)."""
        await self._redis.set(
            _claim_lease_key(tenant_id, pool_id, session_id),
            json.dumps({
                "instance_id": instance_id,
                "claimed_at":  datetime.now(timezone.utc).isoformat(),
            }),
            ex=int(ttl_seconds),
        )

    async def delete_claim_lease(
        self, tenant_id: str, pool_id: str, session_id: str
    ) -> None:
        """Frente 1 (pull): remove a lease do claim (release/auto-release)."""
        await self._redis.delete(_claim_lease_key(tenant_id, pool_id, session_id))

    async def read_claim_lease(
        self, tenant_id: str, pool_id: str, session_id: str
    ) -> dict | None:
        """Frente 1 (pull) / A5: lê a lease do claim (holder). None se ausente/expirada."""
        raw = await self._redis.get(_claim_lease_key(tenant_id, pool_id, session_id))
        if not raw:
            return None
        try:
            data = json.loads(raw if isinstance(raw, str) else raw.decode())
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    async def get_full_queued_contact(
        self, tenant_id: str, session_id: str
    ) -> dict | None:
        """
        Returns the full stored dict for a queued contact (used for re-routing).
        Includes all original ConversationInboundEvent fields plus queued_at_ms.
        """
        raw = await self._redis.get(_queue_contact_key(tenant_id, session_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def get_oldest_queue_wait_ms(
        self, tenant_id: str, pool_id: str
    ) -> int | None:
        """
        Returns the queued_at_ms timestamp of the oldest contact in queue.
        Used to compute sla_urgency = (now_ms - oldest_ms) / sla_target_ms.
        """
        members = await self._redis.zrange(
            _queue_key(tenant_id, pool_id), 0, 0, withscores=True
        )
        if not members:
            return None
        # ZRANGE score = queued_at_ms (lowest = oldest)
        _, oldest_score = members[0]
        return int(oldest_score)

    async def get_queued_contacts(
        self, tenant_id: str, pool_id: str, top_n: int = 10
    ) -> list[QueuedContact]:
        """Returns top_n contacts from queue by score (highest priority first).
        Uses ZREVRANGE for backwards compatibility with redis-py < 4.2."""
        members = await self._redis.zrevrange(
            _queue_key(tenant_id, pool_id), 0, top_n - 1
        )
        contacts: list[QueuedContact] = []
        for session_id in members:
            raw = await self._redis.get(_queue_contact_key(tenant_id, session_id))
            if not raw:
                continue
            try:
                contacts.append(QueuedContact.model_validate_json(raw))
            except Exception:
                continue
        return contacts

    # `get_available_count` REMOVIDA (F5, 2026-08-02). Era `SCARD(pool:instances)` —
    # contagem de PERTENCIMENTO ao pool, o modelo abandonado quando `max_concurrent > 1`
    # passou a existir: conta instância lotada como disponível e ignora a vaga que o
    # recurso gastou em pool irmão. Removida pelo mesmo motivo que
    # `get_total_instances_count` em 2026-07-30 — e por um a mais: sobreviver com o nome
    # `available` era o que a mantinha em circulação. Único chamador era
    # `_publish_queue_position`, que parou de publicar `available_agents`.
    # Capacidade por pool = snapshot (`available`); agregada = `{t}:capacity:snapshot`.

    # `get_busy_count` REMOVIDA (fatia 2). Lia `{t}:pool:{p}:active_count` — um
    # contador POR POOL de uma capacidade que é do RECURSO. Substituída por
    # `compute_pool_occupancy(...)["used_here"]`, derivado da tag no membro do
    # semáforo. O nome sobreviver seria convite a que o modelo voltasse.

    async def get_queue_length(self, tenant_id: str, pool_id: str) -> int:
        """Returns the number of contacts waiting in the pool queue."""
        return await self._redis.zcard(_queue_key(tenant_id, pool_id))

    async def get_queue_rank(
        self, tenant_id: str, pool_id: str, session_id: str
    ) -> int | None:
        """Posição 0-based DESTA sessão na fila do pool (ZRANK; score = queued_at_ms,
        então o rank é a ordem de chegada). `None` quando a sessão não está na fila —
        o chamador decide o fallback. Distinto de `get_queue_length` (tamanho da fila):
        posição é um fato DO CONTATO, tamanho é do POOL."""
        return await self._redis.zrank(_queue_key(tenant_id, pool_id), session_id)

    # `get_total_instances_count` REMOVIDA (2026-07-30). Era o modelo de CONTAGEM de
    # instâncias (SUNION dos dois sets), abandonado quando o snapshot passou a
    # publicar CAPACIDADE. Não tinha um único chamador — mas seu docstring era a
    # fonte do modelo mental errado ("total_instances = agentes distintos"), que
    # migrou para o docstring de `write_pool_snapshot` e para o comentário do
    # `MonitorTab`. Código morto que ensina algo falso custa mais que o espaço.

    async def write_pool_snapshot(
        self,
        tenant_id:              str,
        pool_id:                str,
        sla_target_ms:          int,
        channel_types:          list[str],
        max_reply_time_ms:      int | None = None,
        # Arc 19: webhook pool fields passed through from PoolConfig
        webhook_skill_id:       str | None = None,
        max_concurrent_sessions: int | None = None,
        snapshot_ttl:           int = 3600,
    ) -> dict[str, int]:
        """
        Writes an operational pool snapshot to Redis after each routing event.
        TTL: 3600s (ver `snapshot_ttl` acima).

        **Fatia 2 — a ocupação é DERIVADA do semáforo do RECURSO**, num único
        recompute em Lua (`_RECOMPUTE_POOL_OCCUPANCY_LUA`). Nenhum contador entra na
        conta: nem `active_count` (removido — contava por POOL uma capacidade que é
        do RECURSO), nem `current_sessions` (espelho da instância; correto hoje, mas
        da mesma família — trocar um contador por outro só muda qual mente depois).

        Key: {tenant_id}:pool:{pool_id}:snapshot

        Fields:
          available       — max(0, total_capacity − used_global). Desconta o consumo
                            do recurso em QUALQUER pool, que é o defeito A.
          busy            — sessões servidas NESTE pool (`used_here`, projeção pela
                            tag do membro do semáforo).
          busy_elsewhere  — vagas do MESMO recurso consumidas por pools irmãos.
                            Não é enfeite: sem ele a linha fica aritmeticamente
                            inexplicável (`available < total − busy`, sem motivo
                            visível) e alguém "conserta" de volta para o modelo
                            errado. Com ele, `available = total − busy −
                            busy_elsewhere` fecha na própria linha.
          untagged        — ocupantes sem tag de pool (membro legado de 2 campos, ou
                            escritor que não informou o pool). Contam na capacidade
                            do recurso e em projeção nenhuma. Publicado, nunca
                            descartado em silêncio: deve ir a zero em ≤ 24 h (TTL do
                            SET); persistente é bug de ESCRITOR.
          total_instances — CAPACIDADE total do pool (soma de `max_concurrent` sobre
                            ready_set ∪ busy_set), NÃO contagem de instâncias. O nome
                            é herança do modelo antigo (contagem), abandonado quando
                            `max_concurrent > 1` passou a existir.
                            INVARIANTE: `available ≤ total_instances`, sempre.
          queue_length    — contacts waiting in queue

        Gatilhos: `route()`, `mark_busy`, `remove_conversation`,
        `release_session_from_pool`, `work_task_release`/`work_task_expire` (F3a) e o
        listener de `agent.lifecycle` — todos menos o `route()` via
        `refresh_snapshots_for_instance`, que faz FAN-OUT sobre os pools do recurso.
        `work_task_claim` entra de carona no `mark_busy` que ele já chamava.

        **Esta função não grava pico.** Ela DEVOLVE o recompute (P1) para quem chamou;
        subir o watermark aqui faria os gatilhos de LIBERAÇÃO bumparem, e o pico
        voltaria a ser amostrado nos instantes de escrita de snapshot. Ver
        `record_pool_peak`.
        """
        occ = await self.compute_pool_occupancy(tenant_id, pool_id)
        total_capacity  = occ["total_capacity"]
        used_global     = occ["used_global"]
        busy            = occ["used_here"]
        busy_elsewhere  = max(0, used_global - busy)
        untagged        = occ["untagged"]
        available       = max(0, total_capacity - used_global)
        total_instances = total_capacity
        queue_length    = await self.get_queue_length(tenant_id, pool_id)

        if untagged:
            # Nunca silencioso. Membro sem tag é legítimo apenas na janela de 24 h
            # após o deploy da F1 (TTL do SET). Depois disso significa que existe um
            # escritor de ocupante fora do `claim_instance` — e ele consome
            # capacidade sem aparecer em `busy` de pool nenhum, que é precisamente o
            # tipo de vaga que some sem deixar rastro.
            logger.warning(
                "pool=%s tenant=%s: %d ocupante(s) UNTAGGED no semáforo — contam na "
                "capacidade do recurso e em projeção de pool nenhuma. Esperado zero "
                "após 24 h do deploy da tag; persistente = bug de ESCRITOR.",
                pool_id, tenant_id, untagged,
            )
        if occ["unknown"]:
            logger.info(
                "pool=%s tenant=%s: %d instância(s) sem chave legível — capacidade "
                "contada pelo default (o bootstrap restaura em ~15 s)",
                pool_id, tenant_id, occ["unknown"],
            )

        # Arc 19 (revisado 2026-06-04): max_concurrent_sessions pool-level é um
        # THROTTLE OPCIONAL de downstream (backpressure p/ sistemas frágeis) —
        # display-only no snapshot; NÃO gateia alocação. Capacidade real de
        # webhook pool = slots de instância do deploy (Bootstrap, Fase C) +
        # admissão híbrida (Fase B). Ausente (caso normal) → o snapshot reflete
        # a capacidade real por instâncias, como qualquer pool.
        is_webhook_pool = "webhook" in channel_types
        if is_webhook_pool and max_concurrent_sessions is not None:
            # Throttle configurado — Monitor exibe o teto de backpressure.
            # Aqui o teto é do POOL (exceção legítima, §4 do desenho), então a conta
            # usa `busy` (deste pool) e não `used_global`. `busy_elsewhere` segue
            # publicado como diagnóstico — para instância de webhook ele é 0 por
            # construção (uma instância pertence a um pool), e diferente de 0
            # significa que a premissa quebrou, que é justamente o que se quer ver.
            available       = max(0, max_concurrent_sessions - busy)
            total_instances = max_concurrent_sessions

        snapshot: dict = {
            "pool_id":          pool_id,
            "tenant_id":        tenant_id,
            "available":        available,
            "busy":             busy,
            "busy_elsewhere":   busy_elsewhere,
            "untagged":         untagged,
            "total_instances":  total_instances,
            "queue_length":     queue_length,
            "sla_target_ms":    sla_target_ms,
            "channel_types":    channel_types,
            # Discriminador de MODELO. O bootstrap escreve o seu próprio snapshot
            # (NX) com outra fonte; sem este campo as duas linhas são
            # indistinguíveis na tela e no diagnóstico.
            "model":            "resource_semaphore",
            "updated_at":       datetime.now(timezone.utc).isoformat(),
        }
        if max_reply_time_ms is not None:
            snapshot["max_reply_time_ms"] = max_reply_time_ms
        # Arc 19: always include webhook fields in snapshot when present
        if webhook_skill_id is not None:
            snapshot["webhook_skill_id"] = webhook_skill_id
        if max_concurrent_sessions is not None:
            snapshot["max_concurrent_sessions"] = max_concurrent_sessions
        await self._redis.set(
            _pool_snapshot_key(tenant_id, pool_id),
            json.dumps(snapshot),
            ex=snapshot_ttl,
        )
        # Devolve o recompute (P1) para que o chamador não precise repeti-lo. É DADO,
        # não gatilho: esta função NÃO grava pico. Ver `record_pool_peak` — se "quem
        # escreve snapshot também sobe o pico", a liberação (F3a) passa a bumpar e o
        # pico volta a ser amostrado nos instantes de escrita de snapshot.
        return occ

    async def get_pool_snapshot(
        self, tenant_id: str, pool_id: str
    ) -> dict | None:
        """Returns the most recent operational snapshot for a pool."""
        raw = await self._redis.get(_pool_snapshot_key(tenant_id, pool_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def refresh_pool_snapshot(
        self, tenant_id: str, pool_id: str
    ) -> dict[str, int]:
        """
        Convenience wrapper: recompute and write the pool snapshot, reusing the
        sla_target_ms, channel_types, and max_reply_time_ms from the existing
        snapshot so callers don't need to supply pool config.

        Used after agent_logout / agent_paused events where only the capacity
        numbers need updating, not the pool-level config fields.
        Falls back to defaults if no existing snapshot is found.

        Devolve o recompute (P1) — repassado de `write_pool_snapshot`, sem repetir a
        conta. Ver lá por que este caminho não grava pico.
        """
        existing = await self.get_pool_snapshot(tenant_id, pool_id)
        sla_target_ms          = int(existing.get("sla_target_ms", 480_000)) if existing else 480_000
        channel_types          = existing.get("channel_types", []) if existing else []
        max_reply_time_ms      = existing.get("max_reply_time_ms") if existing else None
        # Arc 19: preserve webhook pool fields from the existing snapshot
        webhook_skill_id       = existing.get("webhook_skill_id") if existing else None
        max_concurrent_sessions = existing.get("max_concurrent_sessions") if existing else None
        return await self.write_pool_snapshot(
            tenant_id,
            pool_id,
            sla_target_ms=           sla_target_ms,
            channel_types=           channel_types,
            max_reply_time_ms=       max_reply_time_ms,
            webhook_skill_id=        webhook_skill_id,
            max_concurrent_sessions= max_concurrent_sessions,
        )

    # `patch_pool_snapshot_available` REMOVIDA (2026-07-30). Nenhum chamador passava
    # delta — o único call site (`kafka_listener._refresh_pool_snapshots`) sempre
    # invocava com `delta=None`, e o comentário que dizia "remove_conversation() já
    # patcheia +1" estava certo sobre o FATO e errado sobre o LUGAR: quem incrementa
    # é o `remove_conversation` desta mesma classe, direto no snapshot.
    #
    # Removida porque induziu diagnóstico errado: caçando `available 4 / total 3`,
    # o nome desta função levou a investigar um caminho que não roda. Ela também não
    # tinha teto — se voltar a ser usada, precisa do mesmo clamp de `remove_conversation`.

    async def get_agent_performance_score(
        self,
        tenant_id:    str,
        agent_type_id: str,
        default:      float = 0.5,
    ) -> float:
        """
        Arc 7d — Historical performance score for an agent type.

        Written by analytics-api performance_job every 5 minutes.
        Key:   {tenant_id}:agent_perf:{agent_type_id}
        Value: str(float) in [0.0, 1.0], TTL 6 h.

        Returns `default` (0.5 = neutral) when:
          - No data yet (new agent type, first 7 days of operation)
          - Redis read fails (transient error)
          - Score cannot be parsed

        Default of 0.5 is intentionally neutral — does not favour or penalise
        agents without sufficient data.
        """
        try:
            raw = await self._redis.get(_agent_perf_key(tenant_id, agent_type_id))
            if raw is not None:
                score = float(raw)
                # Clamp in case Redis was written by a different version
                return max(0.0, min(1.0, score))
        except Exception:
            pass
        return default


# ─────────────────────────────────────────────
# PoolRegistry — reads from Redis cache (never direct HTTP)
# ─────────────────────────────────────────────

class PoolRegistry:
    """
    Queries pool configurations exclusively via Redis cache.
    Cache populated by kafka_listener when processing agent.registry.events.
    Spec: "Never access PostgreSQL directly".
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis    = redis_client
        self._settings = get_settings()

    async def get_pool(
        self, tenant_id: str, pool_id: str
    ) -> PoolConfig | None:
        """
        Returns the configuration for a single, explicitly identified pool.
        Used when the inbound event already carries pool_id (entry point config
        or escalation target) — avoids scanning all tenant pools.
        """
        return await self._get_pool_config(tenant_id, pool_id)

    async def get_candidate_pools(
        self, tenant_id: str, channel: str
    ) -> list[PoolConfig]:
        """
        Returns candidate pools for the conversation.
        Filters: supported channel + Redis cache available.
        """
        pool_ids = await self._redis.smembers(_pool_set_key(tenant_id))
        if not pool_ids:
            return []

        pools: list[PoolConfig] = []
        for pool_id in pool_ids:
            config = await self._get_pool_config(tenant_id, pool_id)
            if config and channel in config.channel_types:
                pools.append(config)
        return pools

    async def list_pools(self, tenant_id: str) -> list[PoolConfig]:
        """
        Returns all cached pool configurations for the tenant.
        Fase B (queue-attended-model): used by AdmissionController to compute
        Σ session_reservation for the shared-bucket limit.
        """
        pool_ids = await self._redis.smembers(_pool_set_key(tenant_id))
        pools: list[PoolConfig] = []
        for pool_id in pool_ids or []:
            config = await self._get_pool_config(tenant_id, pool_id)
            if config:
                pools.append(config)
        return pools

    async def _get_pool_config(
        self, tenant_id: str, pool_id: str
    ) -> PoolConfig | None:
        """Reads pool configuration from Redis cache."""
        raw = await self._redis.get(_pool_config_key(tenant_id, pool_id))
        if not raw:
            return None
        try:
            data = json.loads(raw)
            # Coerce routing_expression from the Redis payload:
            #   dict  → RoutingExpression instance (normal case after first reconcile)
            #   None  → delete key so Pydantic uses default_factory=RoutingExpression
            #           (Agent Registry returns null when pool was registered without it)
            if "routing_expression" in data:
                if isinstance(data["routing_expression"], dict):
                    data["routing_expression"] = RoutingExpression(**data["routing_expression"])
                elif data["routing_expression"] is None:
                    del data["routing_expression"]
            return PoolConfig.model_validate(data)
        except Exception as exc:
            import logging as _log
            _log.getLogger("plughub.routing.registry").warning(
                "pool_config validation failed pool=%s tenant=%s exc=%s",
                pool_id, tenant_id, str(exc).replace("\n", " | "),
            )
            return None

    async def save_pool_config(self, config: PoolConfig) -> None:
        """
        Persists pool configuration to Redis.
        Called by kafka_listener on receiving agent.registry.events.
        """
        key  = _pool_config_key(config.tenant_id, config.pool_id)
        data = config.model_dump()
        await self._redis.set(
            key,
            json.dumps(data),
            ex=self._settings.pool_config_ttl_seconds,
        )
        # Register pool_id in the tenant set
        await self._redis.sadd(_pool_set_key(config.tenant_id), config.pool_id)

    async def get_queued_contacts(
        self, tenant_id: str, pool_id: str, top_n: int = 10
    ) -> list[QueuedContact]:
        """Returns top_n contacts from the pool queue (highest score first).
        Uses ZREVRANGE for backwards compatibility with redis-py < 4.2."""
        members = await self._redis.zrevrange(
            _queue_key(tenant_id, pool_id), 0, top_n - 1
        )
        contacts: list[QueuedContact] = []
        for session_id in members:
            raw = await self._redis.get(_queue_contact_key(tenant_id, session_id))
            if not raw:
                continue
            try:
                contacts.append(QueuedContact.model_validate_json(raw))
            except Exception:
                continue
        return contacts
