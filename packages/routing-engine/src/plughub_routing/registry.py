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
  {tenant_id}:instance:{instance_id}:sessions               — SET de ocupantes (semáforo de vagas). Membro
                                                              "__wrapup_hold__::{origin}::{expires_at_ms}" =
                                                              vaga SEGURA entre o fim do contato e o auto-claim
                                                              do wrap-up inline (Phase 2 — hand-off da vaga)
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

def _pool_active_count_key(tenant_id: str, pool_id: str) -> str:
    """
    Atomic counter of active sessions currently being served in the pool.
    INCR'd synchronously in mark_busy (at routing time, same call chain as
    write_pool_snapshot), DECR'd in remove_conversation (agent_done).
    """
    return f"{tenant_id}:pool:{pool_id}:active_count"

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
    Stores which pool_id currently has an active_count increment for this session.
    Written on each routing event; used to detect cross-pool transfers (escalations)
    and decrement the origin pool's counter without relying on agent_done.
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
    """Operational snapshot — written by router after each routing event. TTL 120s."""
    return f"{tenant_id}:pool:{pool_id}:snapshot"

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
# OCCUPANT = "{session_id}::{conference_id}"  (conference_id vazio p/ contato normal).
# Por quê: duas conferências da MESMA sessão (ex.: fan-out de wrap-up) têm conference_ids
# distintos → occupants distintos → NÃO dividem a mesma vaga (a 2ª recebe -1 e re-seleciona
# outra instância). Já o RELEASE só conhece o session_id (o agent_done não carrega
# conference_id) → libera por PREFIXO "{session_id}::" (remove a(s) vaga(s) desta sessão
# nesta instância). Simétrico: claim deriva de (session_id, conference_id); release de (session_id).
#
# ── Wrap-up unificado Phase 2 — hand-off da vaga ──────────────────────────────
# HOLD = ocupante especial que SEGURA a vaga da origem entre o fim do contato e o
# auto-claim do wrap-up inline (auto-atendimento). Sem ele a ocupação OSCILA (o
# release do agent_done libera a vaga; o auto-claim a reivindica ~2-3s depois) e um
# push que chegue na janela toma a vaga a max_concurrent=1 — o agente recebe contato
# novo com wrap-up pendente, exatamente o que a ocupação do wrap-up deve impedir.
#
# Membro: "__wrapup_hold__::{origin_session_id}::{expires_at_ms}"
#   · o prefixo NÃO colide com o prefixo de sessão "{session_id}::" (uuid) → o
#     release por prefixo de sessão nunca remove um hold, e vice-versa;
#   · origin_session_id é OBSERVABILIDADE (o casamento é FUNGÍVEL: cada hold vale
#     uma vaga, cada wrap-up consome uma — a instância já é a mesma);
#   · expires_at_ms sustenta a expiração PASSIVA (não há sweeper): sem ela, um
#     wrap-up que nunca chega (webhook non-2xx, workflow falha, logout, browser
#     fechado) prenderia a vaga até o EXPIRE de 24h do SET.
_WRAPUP_HOLD_PREFIX = "__wrapup_hold__::"

# Janela mínima entre dois reaps de vagas órfãs da MESMA instância. Só corre para
# instância que já aparece lotada, então 60 s é folgado: o custo do atraso é um
# contato a mais indo para outro agente ou para a fila; o custo de não limitar é
# SMEMBERS + N×EXISTS a cada decisão de roteamento.
_REAP_COOLDOWN_S = 60

# claim: KEYS[1]=sessions set; ARGV[1]=occupant_id; ARGV[2]=max_concurrent; ARGV[3]=ttl_s;
#        ARGV[4]=now_ms; ARGV[5]="1" se este claim pode HERDAR um hold (auto_attend)
#   retorna a nova ocupação (>=1) em sucesso/idempotente; -1 se lotado.
#
# Phase 2: varre holds e (a) DESCARTA os expirados p/ QUALQUER claim — senão um hold
# vazado bloquearia o push permanentemente; (b) HERDA um hold vivo só quando ARGV[5]=1
# (swap net 0 — a ocupação nunca oscila). Tolerante às duas ordens de chegada: se o
# release já ocorreu (sem hold), cai no claim normal com a checagem de capacidade.
_CLAIM_INSTANCE_LUA = """
if redis.call('SISMEMBER', KEYS[1], ARGV[1]) == 1 then
  return redis.call('SCARD', KEYS[1])
end
local HOLD = '__wrapup_hold__::'
local hlen = string.len(HOLD)
local now  = tonumber(ARGV[4])
local members = redis.call('SMEMBERS', KEYS[1])
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
#   ARGV[2]=membro do hold; ARGV[3]=ttl_s do SET. Substitui o RELEASE quando o contato
#   tem wrap-up INLINE seguindo: remove a(s) vaga(s) da origem e põe o hold no lugar
#   (net 0 — a ocupação NUNCA oscila). Retorna a ocupação resultante.
#
#   IDEMPOTENTE por construção: só cria o hold se DE FATO removeu a origem. Um
#   redelivery de agent_done (a origem já saiu) não ressuscita um hold que o wrap-up
#   já consumiu — o que criaria uma vaga fantasma permanente.
_SWAP_TO_HOLD_LUA = """
local members = redis.call('SMEMBERS', KEYS[1])
local prefix  = ARGV[1]
local plen    = string.len(prefix)
local removed = 0
for i = 1, #members do
  if string.sub(members[i], 1, plen) == prefix then
    redis.call('SREM', KEYS[1], members[i])
    removed = removed + 1
  end
end
if removed > 0 then
  redis.call('SADD', KEYS[1], ARGV[2])
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
    def _occupant_id(session_id: str, conference_id: str | None) -> str:
        return f"{session_id}::{conference_id or ''}"

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
        ttl_seconds:       int = 86_400,
        can_inherit_hold:  bool = False,
    ) -> int:
        """Reserva atômica de uma vaga (occupant = session_id::conference_id). Retorna a
        nova ocupação (>=1) em sucesso/idempotente; -1 se lotado. Quem recebe -1 deve
        re-selecionar outro best instance. Duas confs da mesma sessão (conference_ids
        distintos) NÃO compartilham vaga → vão para instâncias distintas.

        Phase 2 (hand-off): `can_inherit_hold=True` (claim do wrap-up auto-atendido)
        permite HERDAR um hold vivo desta instância — swap net 0, a ocupação não
        oscila. Holds EXPIRADOS são descartados em qualquer claim (inclusive push),
        senão um hold vazado bloquearia a instância."""
        async def _try() -> int:
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            return int(await self._redis.eval(
                _CLAIM_INSTANCE_LUA, 1,
                _instance_sessions_key(tenant_id, instance_id),
                self._occupant_id(session_id, conference_id),
                str(int(max_concurrent)), str(int(ttl_seconds)),
                str(now_ms), "1" if can_inherit_hold else "0",
            ))

        res = await _try()
        if res != -1:
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

        Idempotente: sem vaga da origem no SET → nenhum hold é criado."""
        expires_at_ms = int(
            datetime.now(timezone.utc).timestamp() * 1000 + int(hold_ttl_s) * 1000
        )
        member = f"{_WRAPUP_HOLD_PREFIX}{session_id}::{expires_at_ms}"
        res = await self._redis.eval(
            _SWAP_TO_HOLD_LUA, 1,
            _instance_sessions_key(tenant_id, instance_id),
            self._session_prefix(session_id),
            member, str(int(set_ttl_s)),
        )
        logger.info(
            "swap_to_hold: instance=%s origin=%s hold_ttl_s=%d expires_at_ms=%d "
            "occupancy=%d (vaga SEGURA para o wrap-up inline)",
            instance_id, session_id, int(hold_ttl_s), expires_at_ms, int(res),
        )
        return int(res)

    async def release_instance(
        self,
        tenant_id:   str,
        instance_id: str,
        session_id:  str,
    ) -> int:
        """Libera a(s) vaga(s) desta sessão na instância (prefixo "{session_id}::",
        idempotente). Retorna a ocupação restante."""
        res = await self._redis.eval(
            _RELEASE_INSTANCE_LUA, 1,
            _instance_sessions_key(tenant_id, instance_id),
            self._session_prefix(session_id),
        )
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
        (espelho current_sessions, state, DECR de pool:active_count, exclusão
        guardada do serving_pool) é IDÊNTICO — só o membro do SET muda.
        """
        await self._redis.srem(
            _instance_conversations_key(tenant_id, instance_id), conversation_id
        )
        # NOTE: serving-pool deletion is deferred below — inside the try block —
        # so we can guard it against conference specialists wiping the primary
        # contact's serving_pool key.  See guarded delete after pools_to_decr.

        # Decrement active-session counters and update snapshots in-place.
        # We look up which pools this instance belongs to from its meta record.
        # For the common single-pool case this is exact; for multi-pool agents
        # we decrement all pools (floor 0) which may transiently undercount a
        # sibling pool — acceptable given 120s snapshot TTL and self-correction
        # on the next routing event.
        # ── F3 — qual pool decrementar é fato POR-SESSÃO, não por-recurso ────────
        # (ADR adr-human-agent-pool-scoped-identity)
        #
        # A precedência era `meta.pools` (per-RECURSO, e portanto o conjunto
        # INTEIRO de pools do agente) na frente do `pools` do evento. Para um
        # humano multi-pool isso decrementa o `active_count` de pools que não
        # serviram este contato: o pool que serviu fica com carga fantasma (fila
        # não drena) e os outros vão a zero. O evento `agent_done` é emitido por
        # quem sabe QUAL pool serviu esta sessão — é a fonte no escopo certo.
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
            # the pools we are about to decrement.  A conference specialist (e.g.
            # auth_form_ia) shares the same session_id as the primary contact; if
            # we delete unconditionally we wipe the primary's "retencao_humano"
            # entry, causing the primary's own remove_conversation to skip the
            # cross-pool chain and leaving active_count[retencao] stuck at 1.
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
                    "tenant=%s instance=%s conv=%s — active_count NOT decremented "
                    "(instance state already reset above)",
                    tenant_id, instance_id, conversation_id,
                )
                return

            # Phase 1: Decrement active_count atomically for each pool.
            new_active_counts: dict[str, int] = {}
            for pool_id in effective_pools:
                new_val = await self._redis.decr(_pool_active_count_key(tenant_id, pool_id))
                if new_val < 0:
                    await self._redis.set(_pool_active_count_key(tenant_id, pool_id), 0)
                    new_val = 0
                new_active_counts[pool_id] = new_val
                logger.info(
                    "remove_conversation: DECR pool=%s new_active_count=%d",
                    pool_id, new_val,
                )

            # Phase 3: Update pool set membership and patch snapshots.
            for pool_id in effective_pools:
                new_val = new_active_counts[pool_id]
                pool_key = _pool_instances_key(tenant_id, pool_id)
                busy_key = _pool_busy_instances_key(tenant_id, pool_id)

                # Restore ready_set membership if agent is now below capacity
                if new_state == "ready":
                    await self._redis.sadd(pool_key, instance_id)
                # Remove from busy_set when fully idle
                if new_current_sessions == 0:
                    await self._redis.srem(busy_key, instance_id)

                # Patch snapshot with updated busy + available so the SSE dashboard
                # reflects the change without waiting for the next routing event.
                snap_key = _pool_snapshot_key(tenant_id, pool_id)
                raw_snap = await self._redis.get(snap_key)
                if raw_snap:
                    snap = json.loads(raw_snap)
                    snap["busy"] = new_val
                    # Increment available by 1: one session ended, so one extra
                    # capacity slot is now free.  This is always +1 regardless of
                    # max_concurrent because: if the instance was at capacity and
                    # transitioned busy→ready it gained exactly 1 slot; if it was
                    # already in the ready_set it still gained exactly 1 slot.
                    # Using SCARD here would regress to the instance-count model
                    # instead of the capacity-sum model written by write_pool_snapshot.
                    snap["available"] = snap.get("available", 0) + 1
                    await self._redis.set(snap_key, json.dumps(snap), keepttl=True)

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
        Increments current_sessions on the instance and updates pool active-count.

        session_id (optional) — when provided, enables three guards:

        0. Closed-session guard: if the session is already closing/closed
           (session:{id}:close_fired or session:{id}:closed keys exist), mark_busy
           is a no-op.  This prevents active_count from being incremented for a
           session that already had its counter decremented by remove_conversation().
           The primary guard lives in _process_message() (routing engine main.py);
           this is a belt-and-suspenders second layer for tight-race conditions.

        1. Same-pool re-entry guard: if the session is already counted in pool_id
           (prev_pool == pool_id), mark_busy is a no-op.  This prevents double-
           counting when a specialist returns and _try_affinity re-routes the
           primary's session back to the same pool.

        2. Cross-pool transfer: if the session was previously served by a different
           pool (escalation / agent_transfer), that pool's active-count is
           decremented and its snapshot is patched in-place.

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

        prev_pool_for_decr: str | None = None

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
                    # The session was parked in this pool's queue by
                    # release_session_from_pool but NOT yet counted in
                    # active_count.  We must NOT skip the INCR below.
                    # The previous pool's counter was already decremented
                    # inside release_session_from_pool, so no cross-DECR
                    # needed here either — fall through to the INCR.
                    pass
                elif prev_raw == pool_id:
                    # True same-pool re-entry: session already counted in
                    # active_count for this pool (e.g. specialist returns →
                    # _try_affinity fires → mark_busy called again for the
                    # primary pool).  No-op — prevents double-counting.
                    return
                else:
                    # Cross-pool transfer: session was counted in a different
                    # pool.  Decrement that pool's counter after the INCR.
                    prev_pool_for_decr = prev_raw

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

        # Increment atomic active-session counter for the new pool.
        await self._redis.incr(_pool_active_count_key(tenant_id, pool_id))

        # Cross-pool transfer: decrement the previous pool's counter and patch snapshot.
        if prev_pool_for_decr:
            new_val = await self._redis.decr(_pool_active_count_key(tenant_id, prev_pool_for_decr))
            if new_val < 0:
                await self._redis.set(_pool_active_count_key(tenant_id, prev_pool_for_decr), 0)
                new_val = 0
            # Patch previous pool snapshot so SSE reflects the change immediately
            snap_key = _pool_snapshot_key(tenant_id, prev_pool_for_decr)
            raw_snap = await self._redis.get(snap_key)
            if raw_snap:
                snap = json.loads(raw_snap)
                snap["busy"] = new_val
                await self._redis.set(snap_key, json.dumps(snap), keepttl=True)

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
            # active_count NOT yet incremented" from "session is already counted
            # in active_count for this pool".  Using the bare pool_id caused
            # the same-pool re-entry guard in mark_busy to fire as a no-op,
            # leaving active_count at 0 after the agent dequeued the contact.
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
            new_val = await self._redis.decr(_pool_active_count_key(tenant_id, actual_prev))
            if new_val < 0:
                await self._redis.set(_pool_active_count_key(tenant_id, actual_prev), 0)
                new_val = 0
            # Patch the previous pool's snapshot in-place so SSE clients see the
            # update without waiting for the next routing event.
            snap_key = _pool_snapshot_key(tenant_id, actual_prev)
            raw_snap = await self._redis.get(snap_key)
            if raw_snap:
                snap = json.loads(raw_snap)
                snap["busy"] = new_val
                await self._redis.set(snap_key, json.dumps(snap), keepttl=True)

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

    async def get_available_count(self, tenant_id: str, pool_id: str) -> int:
        """Returns count of ready instances in the pool."""
        return await self._redis.scard(_pool_instances_key(tenant_id, pool_id))

    async def get_busy_count(self, tenant_id: str, pool_id: str) -> int:
        """
        Returns the count of ACTIVE SESSIONS currently being served in the pool.

        Uses an atomic INCR/DECR counter keyed at _pool_active_count_key.
        The counter is incremented synchronously in mark_busy (at routing time,
        in the same call chain as write_pool_snapshot) and decremented in
        remove_conversation (called on agent_done Kafka event).

        This correctly handles agents with max_concurrent > 1: every routed
        session increments the counter, so N sessions on one instance → N.
        """
        raw = await self._redis.get(_pool_active_count_key(tenant_id, pool_id))
        return max(0, int(raw)) if raw else 0

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

    async def get_total_instances_count(self, tenant_id: str, pool_id: str) -> int:
        """
        Returns the count of distinct instances registered to this pool.
        Uses SUNION(ready_set, busy_set) to avoid double-counting agents with
        max_concurrent > 1, who appear in both sets simultaneously while serving
        a session below their capacity limit.
        """
        union = await self._redis.sunion(
            _pool_instances_key(tenant_id, pool_id),
            _pool_busy_instances_key(tenant_id, pool_id),
        )
        return len(union)

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
    ) -> None:
        """
        Writes an operational pool snapshot to Redis after each routing event.
        TTL: 120s — refreshed on every route() or dequeue() call.
        Key: {tenant_id}:pool:{pool_id}:snapshot

        Fields:
          available       — instances currently in 'ready' state (idle capacity)
          busy            — active sessions being served (may exceed instance count
                            when max_concurrent > 1)
          total_instances — distinct instances registered to this pool (ready + busy),
                            regardless of session load; dimensioning metric
          queue_length    — contacts waiting in queue
        """
        # ── Step 1: snapshot the ready_set BEFORE calling get_ready_instances() ──
        # We read all set members now so that the SCARD used for total_instances
        # is consistent with the state at routing time (right after mark_busy()).
        # Do NOT evict stale entries here — stale entries (expired keys still in the
        # set) must remain so that len(_all_pool_members) gives the correct total.
        # The bootstrap heartbeat (every 15 s) restores expired keys automatically.
        _pool_set_key       = _pool_instances_key(tenant_id, pool_id)
        _all_pool_members   = await self._redis.smembers(_pool_set_key)
        _all_pool_member_ids = {
            m.decode() if isinstance(m, bytes) else m for m in _all_pool_members
        }

        # ── Step 2: enumerate valid ready instances ──────────────────────────────
        # get_ready_instances() returns instances with valid keys, state=ready,
        # available capacity, and no wrap_up_pending.  It skips — but does NOT
        # evict — entries whose keys have expired (see comment there).
        ready_instances      = await self.get_ready_instances(tenant_id, pool_id)
        _ready_instance_ids  = {inst.instance_id for inst in ready_instances}

        # ── Steps 3 & 4 combined: total_capacity, available, total_instances ────────
        #
        # Pool membership spans TWO Redis sets:
        #   • ready_set  ({tenant}:pool:{pool}:instances)     — read above as _all_pool_members
        #   • busy_set   ({tenant}:pool:{pool}:busy_instances) — read below
        #
        # The bootstrap may remove an instance from the ready_set when it goes
        # busy (and add it to the busy_set), so instances in the busy_set but NOT
        # in the ready_set are invisible to loops that only iterate over
        # _all_pool_member_ids.  We must inspect the busy_set separately.
        #
        # total_capacity = gross capacity across ALL instances (ready_set ∪ busy_set),
        #                  regardless of current load.
        # available      = max(0, total_capacity − busy)
        #                  where busy = atomic active_count (INCR/DECR in
        #                  mark_busy / remove_conversation).
        # total_instances = number of DISTINCT agents dimensioned to the pool
        #                   (ready_set ∪ valid busy_set entries).
        _default_max_concurrent = (
            ready_instances[0].max_concurrent if ready_instances else 1
        )

        # --- Capacity from ready_set members ---
        total_capacity = sum(inst.max_concurrent for inst in ready_instances)
        for _mid in _all_pool_member_ids - _ready_instance_ids:
            # Members that exist in the ready_set but were skipped by
            # get_ready_instances() (state=busy, wrap_up_pending, etc.)
            _raw_mid = await self._redis.get(_instance_key(tenant_id, _mid))
            if _raw_mid:
                try:
                    _inst_data = json.loads(_raw_mid)
                    total_capacity += _inst_data.get(
                        "max_concurrent", _default_max_concurrent
                    )
                except Exception:
                    total_capacity += _default_max_concurrent
            else:
                # Key expired — bootstrap restores within ~15 s; count full capacity
                total_capacity += _default_max_concurrent

        # --- Capacity from busy_set members NOT in ready_set ---
        # These are instances the bootstrap moved OUT of the ready_set because they
        # are busy.  They are already counted in total_instances via
        # valid_busy_not_in_ready_set, but we also need their max_concurrent in
        # total_capacity so the available calculation is correct.
        busy_key  = _pool_busy_instances_key(tenant_id, pool_id)
        busy_iids = await self._redis.smembers(busy_key)
        valid_busy_not_in_ready_set = 0
        for _iid in busy_iids:
            iid_str = _iid.decode() if isinstance(_iid, bytes) else _iid
            if iid_str in _all_pool_member_ids:
                # Already counted via ready_set loop above; skip.
                # (happens when _sync_pool_sets re-adds a busy instance to the
                # ready_set during the 5-min reconciliation pass)
                continue
            raw_inst = await self._redis.get(_instance_key(tenant_id, iid_str))
            if not raw_inst:
                # State key expired → stale busy entry; evict
                await self._redis.srem(busy_key, _iid)
                continue
            try:
                state = json.loads(raw_inst)
                if state.get("current_sessions", 0) > 0:
                    # Genuinely busy instance outside the ready_set — count for
                    # BOTH total_instances and total_capacity.
                    valid_busy_not_in_ready_set += 1
                    total_capacity += state.get(
                        "max_concurrent", _default_max_concurrent
                    )
                else:
                    # Idle but still in busy_set — evict (remove_conversation()
                    # should have cleaned this; tolerate here for robustness).
                    await self._redis.srem(busy_key, _iid)
            except Exception:
                await self._redis.srem(busy_key, _iid)

        busy      = await self.get_busy_count(tenant_id, pool_id)
        available = max(0, total_capacity - busy)

        # total_instances = total concurrent capacity across all agents in this pool
        # (sum of max_concurrent, not count of distinct agents).
        # For pools where max_concurrent=1 per agent (AI pools), this equals the
        # agent count.  For human pools where max_concurrent>1, this gives the
        # correct dimensioning metric: e.g. 1 agent × max_concurrent=3 → total=3.
        total_instances = total_capacity
        queue_length     = await self.get_queue_length(tenant_id, pool_id)

        # Arc 19 (revisado 2026-06-04): max_concurrent_sessions pool-level é um
        # THROTTLE OPCIONAL de downstream (backpressure p/ sistemas frágeis) —
        # display-only no snapshot; NÃO gateia alocação. Capacidade real de
        # webhook pool = slots de instância do deploy (Bootstrap, Fase C) +
        # admissão híbrida (Fase B). Ausente (caso normal) → o snapshot reflete
        # a capacidade real por instâncias, como qualquer pool.
        is_webhook_pool = "webhook" in channel_types
        if is_webhook_pool and max_concurrent_sessions is not None:
            # Throttle configurado — Monitor exibe o teto de backpressure
            available       = max(0, max_concurrent_sessions - busy)
            total_instances = max_concurrent_sessions

        snapshot: dict = {
            "pool_id":          pool_id,
            "tenant_id":        tenant_id,
            "available":        available,
            "busy":             busy,
            "total_instances":  total_instances,
            "queue_length":     queue_length,
            "sla_target_ms":    sla_target_ms,
            "channel_types":    channel_types,
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
    ) -> None:
        """
        Convenience wrapper: recompute and write the pool snapshot, reusing the
        sla_target_ms, channel_types, and max_reply_time_ms from the existing
        snapshot so callers don't need to supply pool config.

        Used after agent_logout / agent_paused events where only the capacity
        numbers need updating, not the pool-level config fields.
        Falls back to defaults if no existing snapshot is found.
        """
        existing = await self.get_pool_snapshot(tenant_id, pool_id)
        sla_target_ms          = int(existing.get("sla_target_ms", 480_000)) if existing else 480_000
        channel_types          = existing.get("channel_types", []) if existing else []
        max_reply_time_ms      = existing.get("max_reply_time_ms") if existing else None
        # Arc 19: preserve webhook pool fields from the existing snapshot
        webhook_skill_id       = existing.get("webhook_skill_id") if existing else None
        max_concurrent_sessions = existing.get("max_concurrent_sessions") if existing else None
        await self.write_pool_snapshot(
            tenant_id,
            pool_id,
            sla_target_ms=           sla_target_ms,
            channel_types=           channel_types,
            max_reply_time_ms=       max_reply_time_ms,
            webhook_skill_id=        webhook_skill_id,
            max_concurrent_sessions= max_concurrent_sessions,
        )

    async def patch_pool_snapshot_available(
        self, tenant_id: str, pool_id: str, delta: int
    ) -> bool:
        """
        Increments snap["available"] by delta if the snapshot already exists.

        Used when a single agent returns from a session — only one capacity slot
        is freed, so we add +delta (typically max_concurrent_sessions of that
        one instance, usually 1) instead of doing a full recount via
        write_pool_snapshot() which would sum all ready instances and produce
        a large jump for hook pools (e.g. wrapup_ia with 400 agents → +400).

        Returns True when the snapshot existed and was patched.
        Returns False when no snapshot was present; the caller must fall back
        to a full write_pool_snapshot().
        """
        snap_key = _pool_snapshot_key(tenant_id, pool_id)
        raw_snap = await self._redis.get(snap_key)
        if not raw_snap:
            return False
        try:
            snap = json.loads(raw_snap)
        except Exception:
            return False
        snap["available"] = snap.get("available", 0) + delta
        snap["updated_at"] = datetime.now(timezone.utc).isoformat()
        await self._redis.set(snap_key, json.dumps(snap), keepttl=True)
        return True

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
