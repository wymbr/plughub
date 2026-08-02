"""
admission.py — Session admission control.

**Fatia 3 (2026-08-02) — o POTE MISTO foi removido; sobrou UM gate.**

O que este módulo fazia até aqui, e por que estava errado:

    shared_limit = {t}:quota:max_concurrent_sessions − Σ session_reservation

`max_concurrent_sessions` era **360 IA + 10 humanos = 370**: a soma de duas moedas que
não se substituem. Gatear sessão humana contra esse pote é a mesma falácia de
aditividade que o rollup de capacidade (F4) recusou um nível acima, onde NÃO existe
`available` escalar no topo — só `available_by_kind`. E era gate DUPLO: a licença humana
é por LOGIN, não por sessão, e já é cobrada em `mcp-server/server.ts` (instâncias
`human-*` ≥ `C_human` ⇒ `human_capacity_exhausted`). Consequência medida: 10 licenças
humanas rendem 30 sessões servíveis e contribuíam 10 ao pote — o sistema rejeitava
contato real com capacidade ociosa (`shared_full` → outage).

O que sobrou, e por que ESTE fica:

    {t}:admission:kind:ai  ≤  {t}:quota:capacity:ai_agent

Moeda correta (só IA contra teto de IA) e, hoje, o ÚNICO teto de IA — a licença
materializada na aquisição da instância está adiada por medição. A unidade ainda é
SESSÃO onde o contrato é INSTÂNCIA, mas o bootstrap só cria instância de IA com
`max_concurrent=1` (instância == sessão), então o defeito de unidade é LATENTE. Quando
as licenças existirem, este gate é SUBSTITUÍDO por elas — não somado a elas.

O que saiu junto (e não deve voltar sem levar o modelo junto):
  · `{t}:admission:shared`              SET  — o pote misto
  · `{t}:admission:reserved:{pool_id}`  SET  — fatia de SESSÃO por pool: mesmo pote
                                               misto, e fragmenta um recurso que é
                                               compartilhado (contraria o invariante
                                               "capacidade é do RECURSO"). Zero pools
                                               usavam (medição Q2, 2026-07-31)
  · `{t}:admission:member:{session_id}` STR  — só existia p/ saber de qual dos baldes
                                               acima a sessão saía
  · `_shared_limit` / `_sum_reservations`     — a aritmética do pote

Chaves vivas:
  {t}:admission:kind:ai                  SET    — sessões em pools `agent_kind='ai'`
  {t}:admission:kind_member:{session_id} STRING — "ai"|"human" (TTL 7d)
  {t}:admission:ai_pools                 HASH   {session_id → pool_id} — atribuição por
                                                pool das sessões que debitam C_ai.
                                                Renomeado de `shared_pools`: o nome
                                                antigo é o que manteria o pote em
                                                circulação depois de ele deixar de
                                                existir (mesma lição de
                                                `get_available_count`, F5)
  {t}:quota:capacity:ai_agent            STRING — C_ai (pricing quota sync)

Mecânica (inalterada onde continua valendo):
  - Contadores são SETs de `session_id` (SCARD = concorrência) — idempotentes em
    re-publicação (drain, crash-recovery) e auto-curáveis pelo reconciler.
  - Roda em TODA requisição de roteamento. Escalação entre pools = migração de tipo
    (IA→humano libera a licença de IA; humano→IA re-checa o teto).
  - Rejeição só na PORTA (`cause="quota"` → outage, visível na demanda reprimida como
    "Teto contratado"). Migração de sessão ATIVA para IA saturada é fail-open: nunca
    derrubar contato em andamento.
  - Release é feito pelo reconciler periódico: membros com `session:{id}:closed`
    são removidos (~60 s de atraso, aceitável para um medidor de admissão).

Fail-open: pool sem `agent_kind`, ou `C_ai` ausente → sem gate (mantém o tracking).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import redis.asyncio as aioredis

from .models import PoolConfig

logger = logging.getLogger("plughub.routing.admission")

_MEMBER_TTL_S = 604_800   # 7 days — same horizon as session:closed markers


def _decode(value) -> str:
    if value is None:
        return ""
    return value.decode() if isinstance(value, bytes) else str(value)


@dataclass
class AdmissionDecision:
    admitted: bool
    cause:    str = ""          # "quota" — único valor desde a fatia 3
    pool_id:  str = ""
    limit:    int | None = None
    current:  int | None = None


class AdmissionController:
    def __init__(self, redis_client: aioredis.Redis) -> None:
        # `pool_registry` saiu do construtor na fatia 3: seu único uso era
        # `_sum_reservations`, que existia para calcular o pote misto.
        self._redis = redis_client

    # ── Admission ──────────────────────────────────────────────────────────────

    async def admit(
        self,
        tenant_id:  str,
        session_id: str,
        pool:       PoolConfig | None,
        pool_id:    str,
    ) -> AdmissionDecision:
        """
        Admite a sessão contra o teto de IA, quando o pool é de IA.
        Idempotente: re-admitir uma sessão já contabilizada é no-op.

        Pool humano (ou sem `agent_kind`) **não é gateado aqui** — a licença humana é
        por login, e é cobrada no `agent_login`. Antes da fatia 3 este caminho debitava
        um pote misto e podia recusar contato com humano ocioso.
        """
        kind        = (pool.agent_kind if pool else None) or None
        kind_member = f"{tenant_id}:admission:kind_member:{session_id}"
        prev_kind   = _decode(await self._redis.get(kind_member))
        kind_set    = f"{tenant_id}:admission:kind:ai"

        if kind == "ai" and prev_kind != "ai":
            c_ai = await self._type_limit(tenant_id, "ai_agent")
            if c_ai is not None:
                added  = bool(await self._redis.sadd(kind_set, session_id))
                kcount = await self._redis.scard(kind_set)
                if kcount > c_ai:
                    if added:
                        await self._redis.srem(kind_set, session_id)   # rollback
                    if not prev_kind:
                        # Porta: entrada além da capacidade de IA contratada → outage.
                        logger.warning(
                            "admission rejected: tenant=%s session=%s pool=%s kind=ai "
                            "current=%d limit=%d",
                            tenant_id, session_id, pool_id, kcount, c_ai,
                        )
                        return AdmissionDecision(
                            admitted=False, cause="quota", pool_id=pool_id,
                            limit=c_ai, current=kcount,
                        )
                    # Migração de sessão ATIVA para IA saturada: fail-open, mantendo a
                    # atribuição de ORIGEM (nunca meio-estado no tracking).
                    logger.warning(
                        "admission type-gate migration fail-open: tenant=%s session=%s "
                        "pool=%s current=%d limit=%d",
                        tenant_id, session_id, pool_id, kcount, c_ai,
                    )
                    kind = prev_kind or None
            else:
                # Sem C_ai configurado → sem gate, mas mantém o tracking de kind.
                await self._redis.sadd(kind_set, session_id)

        await self._commit_kind(tenant_id, session_id, kind, prev_kind, kind_member, kind_set)
        await self._commit_ai_attribution(tenant_id, session_id, kind, pool_id)
        return AdmissionDecision(admitted=True)

    async def _commit_ai_attribution(
        self, tenant_id: str, session_id: str, kind: str | None, pool_id: str
    ) -> None:
        """Item 7a: HASH {sid→pool} espelhando quem debita C_ai, por pool.

        Renomeado de `_commit_shared_attribution` (fatia 3). A pergunta que ele responde
        mudou junto com o balde: era "onde está a sessão do pote compartilhado", virou
        "qual pool está consumindo licença de IA".
        """
        try:
            hash_key = f"{tenant_id}:admission:ai_pools"
            if kind == "ai":
                await self._redis.hset(hash_key, session_id, pool_id)
            else:
                # Escalação IA→humano: a licença sai, a atribuição sai junto.
                await self._redis.hdel(hash_key, session_id)
        except Exception as exc:
            logger.warning(
                "ai attribution failed (reconciler heals) session=%s — %s",
                session_id, exc,
            )

    async def _commit_kind(
        self,
        tenant_id:   str,
        session_id:  str,
        kind:        str | None,
        prev_kind:   str,
        kind_member: str,
        kind_set:    str,
    ) -> None:
        """Atualiza o tracking de kind após admissão bem-sucedida."""
        if kind is None:
            return  # pool sem tipagem → não mexe no tracking (conservador)
        await self._redis.set(kind_member, kind, ex=_MEMBER_TTL_S)
        if prev_kind == "ai" and kind != "ai":
            await self._redis.srem(kind_set, session_id)

    async def has_headroom(
        self,
        tenant_id:  str,
        pool_id:    str,
        agent_kind: str | None = None,
    ) -> bool:
        """
        Checagem READ-ONLY de vaga (fila de sistema, Fase A): os drains só
        re-publicam sessão NÃO-ADMITIDA quando há vaga — sem isso, re-publicar com o
        teto cheio vira churn rejeita→re-enfileira a cada ciclo. Espelha o `admit()`.

        Fatia 3: pool humano devolve `True` sempre — não há mais teto de sessão humana
        para consultar. O parâmetro `session_reservation` saiu junto com os baldes
        reservados; chamador que ainda o passe recebe `TypeError`, que é o que se quer
        (silenciosamente ignorá-lo faria o drain achar que consultou algo).
        """
        try:
            if agent_kind == "ai":
                c_ai = await self._type_limit(tenant_id, "ai_agent")
                if c_ai is not None:
                    kcount = await self._redis.scard(f"{tenant_id}:admission:kind:ai")
                    if kcount >= c_ai:
                        return False
            return True
        except Exception as exc:
            logger.warning("has_headroom failed (fail-open) tenant=%s — %s", tenant_id, exc)
            return True

    async def release(self, tenant_id: str, session_id: str) -> None:
        """
        Fila de sistema (system-queue.md Fase A): libera a licença de IA de uma sessão
        que entrou em fila MUDA — ela deixa de debitar C_ai enquanto espera. A
        re-admissão acontece naturalmente quando o drain re-publica o contato.
        """
        kind_member = f"{tenant_id}:admission:kind_member:{session_id}"
        prev_kind = _decode(await self._redis.get(kind_member))
        if prev_kind:
            if prev_kind == "ai":
                await self._redis.srem(f"{tenant_id}:admission:kind:ai", session_id)
            await self._redis.delete(kind_member)
        await self._redis.hdel(f"{tenant_id}:admission:ai_pools", session_id)

    async def _type_limit(self, tenant_id: str, resource_type: str) -> int | None:
        """C por tipo ({t}:quota:capacity:{type}, pricing quota sync). None = sem gate."""
        raw = _decode(await self._redis.get(f"{tenant_id}:quota:capacity:{resource_type}"))
        if not raw:
            return None
        try:
            v = int(float(raw))
        except ValueError:
            return None
        return v if v > 0 else None

    # ── Reconciliation (release) ───────────────────────────────────────────────

    async def reconcile(self) -> int:
        """
        Remove sessões fechadas do balde de IA e do buffer gratuito.
        Um membro é liberado quando `session:{id}:closed` existe (escrito pelo
        bridge/mcp-server em todo fechamento, TTL 7d). Devolve o total liberado.
        """
        released = 0
        try:
            # Fila de sistema (Fase A): backstop do buffer grátis — remove sessões
            # fechadas que os drains não limparam.
            async for key in self._redis.scan_iter(match="*:queue:unadmitted", count=200):
                tenant_id = _decode(key).split(":queue:", 1)[0]
                async for member in self._redis.sscan_iter(_decode(key), count=200):
                    sid = _decode(member)
                    if await self._redis.exists(f"session:{sid}:closed"):
                        await self._redis.srem(_decode(key), sid)
                        await self._redis.delete(f"{tenant_id}:queue:first_queued:{sid}")
                        released += 1

            buckets: list[str] = []
            async for key in self._redis.scan_iter(match="*:admission:kind:ai", count=200):
                buckets.append(_decode(key))

            for bucket in buckets:
                tenant_id = bucket.split(":admission:", 1)[0]
                hash_key  = f"{tenant_id}:admission:ai_pools"
                async for member in self._redis.sscan_iter(bucket, count=200):
                    session_id = _decode(member)
                    if await self._redis.exists(f"session:{session_id}:closed"):
                        await self._redis.srem(bucket, session_id)
                        await self._redis.delete(
                            f"{tenant_id}:admission:kind_member:{session_id}"
                        )
                        await self._redis.hdel(hash_key, session_id)
                        released += 1
                # Item 7a — higiene do HASH de atribuição: entradas cujo sid já não
                # está no SET (crash entre os dois writes) são removidas, garantindo
                # Σ fatias == SCARD(kind:ai).
                try:
                    for sid_raw in await self._redis.hkeys(hash_key):
                        sid = _decode(sid_raw)
                        if not await self._redis.sismember(bucket, sid):
                            await self._redis.hdel(hash_key, sid)
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("admission reconcile failed — %s", exc)
        if released:
            logger.info("admission reconcile: released %d closed session(s)", released)
        return released
