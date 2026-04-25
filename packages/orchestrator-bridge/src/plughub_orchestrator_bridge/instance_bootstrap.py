"""
instance_bootstrap.py
Reconciliation-driven agent instance and pool management.

Princípio: o Agent Registry é a fonte de verdade — o Bootstrap funciona como
um controller que continuamente converge o estado atual do Redis para o estado
desejado definido pelo Registry, sem necessidade de reinicialização.

Escopo completo da reconciliação:
  A. Instâncias de agentes
       1. Lê desired = {instance_id → payload} calculado a partir de GET /v1/agent-types
       2. Lê actual  = SCAN {tenant}:instance:* no Redis
       3. Diff:
            to_create  — em desired mas não no Redis
            to_delete  — no Redis mas não em desired (se ready/idle)
            to_drain   — no Redis mas não em desired (se busy/paused) → aguardam fim de sessão
            to_update  — payload divergente (pools/channels/model mudou)
            to_renew   — idêntico ao desired, só renova TTL

  B. Pools
       1. Lê todos os pools do Registry via GET /v1/pools
       2. Para cada pool ativo: escreve/atualiza pool_config:{pool_id} no Redis
       3. Para pools removidos do Registry:
            - deleta pool_config:{pool_id}
            - limpa pool:{pool_id}:instances SET (se todas instâncias já saíram)
       4. Atualiza o SET global {tenant}:pools

  C. Pool:*:instances SETs
       Sincroniza membership com o estado desejado das instâncias.

Triggers:
  startup                   → reconcile() [full diff + apply; loga ReconciliationReport]
  heartbeat (15s)           → _heartbeat_tick() [só TTL, sem I/O do Registry]
  registry.changed (Kafka)  → reconcile() imediato
  RECONCILE_INTERVAL (5min) → reconcile() periódico de auto-healing

Dry-run:
  await bootstrap.dry_run(tenant_id)  → ReconciliationReport sem aplicar nada

Notas:
  - Human agents NÃO são gerenciados — login via Agent Assist UI.
  - Instâncias busy/paused nunca são deletadas forçadamente — recebem draining=True
    e são removidas pelo heartbeat quando voltam a ready.
  - pool_config keys são re-escritas quando o conteúdo diverge (não só quando expiram).
  - Idempotente: reconciliar N vezes produz o mesmo resultado que reconciliar uma.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import aiohttp
import redis.asyncio as aioredis

logger = logging.getLogger("plughub.orchestrator-bridge.bootstrap")

# ─── Tunables ─────────────────────────────────────────────────────────────────

# TTL das instâncias no Redis (> TTL do Routing Engine de 30s)
_INSTANCE_TTL_S        = 35
# Intervalo do heartbeat (só renova TTL, sem chamar o Registry)
_HEARTBEAT_INTERVAL_S  = 15
# Intervalo da reconciliação completa periódica (auto-healing de drift)
_RECONCILE_INTERVAL_S  = 300   # 5 min
# TTL dos pool_config no Redis
_POOL_CONFIG_TTL_S     = 3600   # 1h — sufficient for crash recovery, fast enough for cleanup


# ─── ReconciliationReport ─────────────────────────────────────────────────────

@dataclass
class ReconciliationReport:
    """
    Resultado de uma reconciliação completa (instâncias + pools) para um tenant.

    Instâncias:
      created    — instâncias novas escritas no Redis
      deleted    — instâncias removidas (estavam ready/idle)
      drained    — instâncias marcadas draining=True (estavam busy; removidas
                   pelo heartbeat quando voltarem a ready)
      updated    — instâncias cujo payload foi atualizado (pools/channels mudou)
      renewed    — instâncias sem mudança de payload; apenas TTL renovado
      unchanged  — instâncias idênticas ao desejado; sem operação Redis

    Pools:
      pools_written  — pool_config keys criadas ou atualizadas (pool novo ou mudança de dados)
      pools_removed  — pool_config keys deletadas (pool removido do Registry)
      pools_set_sync — pool IDs adicionados ou removidos do SET global {tenant}:pools

    Comum:
      errors     — mensagens de erro não-fatais
      duration_ms — tempo total em milissegundos
      dry_run    — True se foi simulação (nenhuma escrita aplicada)
    """
    tenant_id:      str
    created:        list[str]  = field(default_factory=list)
    deleted:        list[str]  = field(default_factory=list)
    drained:        list[str]  = field(default_factory=list)
    updated:        list[str]  = field(default_factory=list)
    renewed:        list[str]  = field(default_factory=list)
    unchanged:      list[str]  = field(default_factory=list)
    pools_written:  list[str]  = field(default_factory=list)
    pools_removed:  list[str]  = field(default_factory=list)
    pools_set_sync: list[str]  = field(default_factory=list)
    errors:         list[str]  = field(default_factory=list)
    duration_ms:    float      = 0.0
    dry_run:        bool       = False

    @property
    def has_changes(self) -> bool:
        return bool(
            self.created or self.deleted or self.drained or self.updated
            or self.pools_written or self.pools_removed or self.pools_set_sync
        )

    def summary(self) -> str:
        tag = "[DRY-RUN] " if self.dry_run else ""
        pool_part = ""
        if self.pools_written or self.pools_removed or self.pools_set_sync:
            pool_part = (
                f" pools_written={len(self.pools_written)}"
                f" pools_removed={len(self.pools_removed)}"
                f" pools_set={len(self.pools_set_sync)}"
            )
        return (
            f"{tag}tenant={self.tenant_id} "
            f"created={len(self.created)} deleted={len(self.deleted)} "
            f"drained={len(self.drained)} updated={len(self.updated)} "
            f"renewed={len(self.renewed)} unchanged={len(self.unchanged)}"
            f"{pool_part} errors={len(self.errors)} ({self.duration_ms:.0f}ms)"
        )


# ─── InstanceBootstrap ────────────────────────────────────────────────────────

class InstanceBootstrap:
    """
    Controller de reconciliação de instâncias de agentes no Redis.

    Uso típico:
        bootstrap = InstanceBootstrap(redis, registry_url, tenant_ids)
        await bootstrap.reconcile()                         # startup
        asyncio.create_task(bootstrap.heartbeat_loop())    # TTL + reconciliação periódica

    Dry-run (sem aplicar nada):
        report = await bootstrap.dry_run("tenant_demo")
        print(report.summary())
    """

    def __init__(
        self,
        redis:        aioredis.Redis,
        registry_url: str,
        tenant_ids:   list[str],
    ) -> None:
        self._redis        = redis
        self._registry_url = registry_url
        self._tenant_ids   = tenant_ids

        # Cache em memória do estado "registered" — usado pelo heartbeat leve
        # instance_id → payload dict
        self._registered: dict[str, dict] = {}

        # Cache em memória dos pool_configs — usado pelo heartbeat para renovar/recriar
        # sem ir ao Registry. Estrutura: tenant_id → pool_id → pool_data dict
        self._pool_configs: dict[str, dict[str, dict]] = {}

        # Flags de controle do heartbeat_loop
        self._needs_refresh          = False
        self._ticks_since_reconcile  = 0   # quantos heartbeats desde a última reconciliação

    # ─── API pública ──────────────────────────────────────────────────────────

    def request_refresh(self) -> None:
        """
        Sinaliza que o próximo ciclo do heartbeat deve executar uma reconciliação
        completa. Chamado quando registry.changed chega via Kafka.
        """
        self._needs_refresh = True

    async def reconcile(
        self,
        http: aiohttp.ClientSession | None = None,
    ) -> list[ReconciliationReport]:
        """
        Executa reconciliação completa para todos os tenants configurados.
        Compara o estado do Registry com o Redis e aplica o diff mínimo.
        Retorna uma lista de ReconciliationReport — um por tenant.
        """
        own_session = http is None
        if own_session:
            http = aiohttp.ClientSession()
        reports = []
        try:
            for tenant_id in self._tenant_ids:
                report = await self._reconcile_tenant(
                    http, tenant_id, dry_run=False
                )
                reports.append(report)
                if report.has_changes or report.errors:
                    logger.info("Reconciliation: %s", report.summary())
                else:
                    logger.debug("Reconciliation: %s", report.summary())
        finally:
            if own_session:
                await http.close()
        return reports

    # Kept for backward compat — delegates to reconcile()
    async def run_once(self, http: aiohttp.ClientSession | None = None) -> None:
        await self.reconcile(http)

    async def dry_run(
        self,
        tenant_id: str,
        http: aiohttp.ClientSession | None = None,
    ) -> ReconciliationReport:
        """
        Simula a reconciliação para um tenant sem aplicar nenhuma escrita no Redis.
        Útil para inspecionar o estado antes de aplicar, ou para auditoria operacional.
        """
        own_session = http is None
        if own_session:
            http = aiohttp.ClientSession()
        try:
            return await self._reconcile_tenant(http, tenant_id, dry_run=True)
        finally:
            if own_session:
                await http.close()

    async def heartbeat_loop(self) -> None:
        """
        Loop infinito de manutenção:

          A cada _HEARTBEAT_INTERVAL_S (15s):
            - Se registry.changed pendente → reconciliação completa
            - Se _RECONCILE_INTERVAL_S passaram → reconciliação periódica (auto-healing)
            - Caso contrário → heartbeat leve (só renova TTL, sem chamar o Registry)

        O heartbeat leve é O(instâncias em memória) e não faz chamadas HTTP.
        A reconciliação completa faz chamadas HTTP ao Registry mas é controlada
        por frequência para não sobrecarregar.
        """
        ticks_per_reconcile = max(1, _RECONCILE_INTERVAL_S // _HEARTBEAT_INTERVAL_S)
        logger.info(
            "Instance controller started — heartbeat=%ds reconcile_every=%ds tenants=%s",
            _HEARTBEAT_INTERVAL_S, _RECONCILE_INTERVAL_S, self._tenant_ids,
        )
        async with aiohttp.ClientSession() as http:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
                try:
                    self._ticks_since_reconcile += 1
                    needs_full = (
                        self._needs_refresh
                        or self._ticks_since_reconcile >= ticks_per_reconcile
                    )

                    if needs_full:
                        if self._needs_refresh:
                            logger.info("registry.changed — triggering reconciliation")
                        else:
                            logger.debug(
                                "Periodic reconciliation triggered (%ds interval)",
                                _RECONCILE_INTERVAL_S,
                            )
                        self._needs_refresh         = False
                        self._ticks_since_reconcile = 0
                        await self.reconcile(http)
                        # Write readiness signal after every triggered reconcile so that
                        # E2E tests (which flush Redis between scenarios) can detect the
                        # reconcile completed. _heartbeat_tick also does this, but the
                        # next tick is 15 s away; writing here makes it immediate.
                        for _tid in self._tenant_ids:
                            try:
                                await self._redis.set(
                                    f"{_tid}:bootstrap:ready", "1", ex=60
                                )
                            except Exception:
                                pass
                    else:
                        await self._heartbeat_tick()

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("Heartbeat error: %s", exc)

    # ─── Core reconciliation ──────────────────────────────────────────────────

    async def _reconcile_tenant(
        self,
        http:     aiohttp.ClientSession,
        tenant_id: str,
        dry_run:  bool = False,
    ) -> ReconciliationReport:
        """
        Reconcilia o estado Redis de um tenant com o Agent Registry.

        Algoritmo:
          1. Lê desired = {instance_id → payload} calculado a partir do Registry
          2. Lê actual  = {instance_id → payload} escaneado do Redis
          3. Diff → to_create, to_delete/to_drain, to_update, to_renew, unchanged
          4. Aplica (a menos que dry_run=True)
          5. Sincroniza pool SET memberships e pool_config keys
        """
        t0 = time.monotonic()
        report = ReconciliationReport(tenant_id=tenant_id, dry_run=dry_run)

        # ── 1. Estado desejado ─────────────────────────────────────────────────
        agent_types = await self._fetch_agent_types(http, tenant_id)
        if not agent_types:
            logger.warning(
                "No agent types in registry for tenant=%s — reconciliation skipped",
                tenant_id,
            )
            report.duration_ms = (time.monotonic() - t0) * 1000
            return report

        # Fetch ALL pools from the Registry once — used for both channel_types
        # lookup on instances AND full pool reconciliation (Section B).
        registry_pools = await self._fetch_all_pools(http, tenant_id)
        registry_pool_map: dict[str, dict] = {
            p["pool_id"]: p for p in registry_pools if "pool_id" in p
        }

        all_pool_ids = _extract_all_pool_ids(agent_types)
        pool_channel_types: dict[str, list[str]] = {
            pid: registry_pool_map.get(pid, {}).get("channel_types", [])
            for pid in all_pool_ids
        }
        desired = _build_desired_state(agent_types, pool_channel_types, tenant_id)

        # ── 2. Estado atual no Redis ───────────────────────────────────────────
        actual = await self._scan_instances_from_redis(tenant_id)

        # ── 3. Diff ────────────────────────────────────────────────────────────
        desired_ids = set(desired)
        actual_ids  = set(actual)

        to_create         = desired_ids - actual_ids
        surplus_ids       = actual_ids - desired_ids
        common_ids        = desired_ids & actual_ids

        # ── 4. Aplicar criações ────────────────────────────────────────────────
        for iid in sorted(to_create):
            if not dry_run:
                await self._write_instance(tenant_id, iid, desired[iid])
                self._registered[iid] = desired[iid]
            report.created.append(iid)

        # ── 5. Aplicar remoções / drains ───────────────────────────────────────
        for iid in sorted(surplus_ids):
            current = actual[iid]
            # Human agents are managed by the Agent Assist UI login flow, not by
            # the bootstrap.  Deleting them would disconnect a logged-in agent.
            # Identify them by source="human_login" written by registerHumanAgent.
            if current.get("source") == "human_login":
                report.unchanged.append(iid)
                continue
            status  = current.get("status", "ready")
            if status in ("busy", "paused"):
                # Sessão ativa — marca como draining; heartbeat limpa depois
                if not dry_run:
                    draining_payload = {**current, "draining": True}
                    await self._redis.set(
                        f"{tenant_id}:instance:{iid}",
                        json.dumps(draining_payload),
                        ex=_INSTANCE_TTL_S,
                    )
                    # Mantém no cache até o heartbeat confirmar a remoção
                    self._registered[iid] = draining_payload
                report.drained.append(iid)
            else:
                # Instância idle — pode remover com segurança
                if not dry_run:
                    await self._remove_instance(tenant_id, iid, current)
                    self._registered.pop(iid, None)
                report.deleted.append(iid)

        # ── 6. Aplicar updates / renewals ─────────────────────────────────────
        for iid in sorted(common_ids):
            current = actual[iid]
            want    = desired[iid]
            status  = current.get("status", "ready")

            # Compara apenas os campos gerenciáveis (ignora status, timestamps, etc.)
            needs_update = _payload_diverged(current, want)

            if needs_update:
                if status in ("busy", "paused"):
                    # Não sobrescreve sessão ativa; agenda update via flag
                    if not dry_run:
                        flagged = {**current, "pending_update": True}
                        await self._redis.expire(
                            f"{tenant_id}:instance:{iid}", _INSTANCE_TTL_S
                        )
                        self._registered[iid] = flagged
                    report.updated.append(f"{iid}(pending)")
                else:
                    # Pode aplicar o update imediatamente
                    if not dry_run:
                        merged = {**want, "status": status, "state": status}
                        await self._write_instance(tenant_id, iid, merged)
                        self._registered[iid] = merged
                    report.updated.append(iid)
            else:
                # Sem mudança de payload — renova TTL
                if not dry_run:
                    await self._redis.expire(
                        f"{tenant_id}:instance:{iid}", _INSTANCE_TTL_S
                    )
                    self._registered[iid] = current
                report.renewed.append(iid)

        # ── 7. Sincroniza pool:*:instances SETs ────────────────────────────────
        await self._sync_pool_sets(tenant_id, desired, actual, dry_run=dry_run)

        # ── 8. Reconcilia pool_config keys (write / update / delete) ───────────
        await self._reconcile_pool_configs(
            http, tenant_id, registry_pools, all_pool_ids, report, dry_run=dry_run
        )

        # ── 9. Reconcilia SET global {tenant}:pools ─────────────────────────────
        await self._reconcile_pools_set(
            tenant_id, list(registry_pool_map.keys()), report, dry_run=dry_run
        )

        report.duration_ms = (time.monotonic() - t0) * 1000
        return report

    # ─── Heartbeat leve ───────────────────────────────────────────────────────

    async def _heartbeat_tick(self) -> None:
        """
        Renova TTL de todas as instâncias em memória sem consultar o Registry.
        Também processa instâncias marcadas como draining ou pending_update,
        e renova a chave de readiness {tenant}:bootstrap:ready (TTL 60s).
        """
        # Renova (ou recria) readiness signal para todos os tenants gerenciados.
        # Usa SET com EX em vez de EXPIRE para que a chave seja recriada caso
        # tenha sido removida (ex.: flushTestData nos testes E2E).
        for tenant_id in self._tenant_ids:
            try:
                await self._redis.set(f"{tenant_id}:bootstrap:ready", "1", ex=60)
            except Exception:
                pass

        # Renova (ou recria) pool_config keys a partir do cache em memória.
        # Usa SET com EX para recriar chaves que foram deletadas (ex.: flushTestData).
        for tenant_id, pool_map in self._pool_configs.items():
            for pool_id, pool_data in pool_map.items():
                try:
                    await self._redis.set(
                        f"{tenant_id}:pool_config:{pool_id}",
                        json.dumps(pool_data),
                        ex=_POOL_CONFIG_TTL_S,
                    )
                except Exception:
                    pass

        for iid, payload in list(self._registered.items()):
            tenant_id = payload.get("tenant_id", "")
            key       = f"{tenant_id}:instance:{iid}"
            try:
                raw = await self._redis.get(key)
                if not raw:
                    # Instância expirou — restaura (pode ter tido gap de TTL)
                    payload_clean = {
                        k: v for k, v in payload.items()
                        if k not in ("draining", "pending_update")
                    }
                    await self._write_instance(tenant_id, iid, payload_clean)
                    continue

                current = json.loads(raw)
                status  = current.get("status", "ready")

                if current.get("draining"):
                    # Instância marcada para remoção — remove se não está mais busy
                    if status not in ("busy", "paused"):
                        await self._remove_instance(tenant_id, iid, current)
                        self._registered.pop(iid, None)
                        logger.info("Drained and removed: %s", iid)
                    else:
                        await self._redis.expire(key, _INSTANCE_TTL_S)
                    continue

                if current.get("pending_update"):
                    # Update pendente — aplica se voltou a ready
                    if status not in ("busy", "paused"):
                        clean = {
                            k: v for k, v in payload.items()
                            if k not in ("draining", "pending_update")
                        }
                        merged = {**clean, "status": "ready", "state": "ready"}
                        await self._write_instance(tenant_id, iid, merged)
                        self._registered[iid] = merged
                        logger.info("Applied pending update to: %s", iid)
                    else:
                        await self._redis.expire(key, _INSTANCE_TTL_S)
                    continue

                # Caminho normal: renova TTL ou restaura como ready
                if status in ("busy", "paused"):
                    await self._redis.expire(key, _INSTANCE_TTL_S)
                else:
                    await self._write_instance(tenant_id, iid, payload)

            except Exception as exc:
                logger.warning("Heartbeat tick failed for %s: %s", iid, exc)

    # ─── Helpers de I/O Redis ─────────────────────────────────────────────────

    async def _write_instance(
        self, tenant_id: str, instance_id: str, payload: dict
    ) -> None:
        """
        Escreve instância no Redis + SADD nos pool SETs correspondentes.

        Safety guard: never overwrite a busy or paused instance with status=ready.
        After a crash, the reconciliation loop may re-classify the instance as
        to_create (desired but absent from its in-memory snapshot) and call
        _write_instance with status=ready — even though the Redis key still exists
        and the instance is handling an active session. The guard detects this and
        applies pending_update instead, preserving the live session.
        """
        instance_key = f"{tenant_id}:instance:{instance_id}"

        # Guard: if the existing Redis key shows a live session, don't overwrite it.
        existing_raw = await self._redis.get(instance_key)
        if existing_raw:
            try:
                existing = json.loads(existing_raw)
                if existing.get("status") in ("busy", "paused"):
                    logger.warning(
                        "Bootstrap: instance %s is %s — marking pending_update "
                        "instead of overwrite to preserve live session",
                        instance_id, existing["status"],
                    )
                    patch = {**existing, "pending_update": True}
                    await self._redis.set(
                        instance_key,
                        json.dumps(patch),
                        ex=_INSTANCE_TTL_S,
                    )
                    return
            except (json.JSONDecodeError, KeyError):
                pass  # Corrupt state — safe to overwrite

        await self._redis.set(
            instance_key,
            json.dumps(payload),
            ex=_INSTANCE_TTL_S,
        )
        for pool_id in payload.get("pools", []):
            await self._redis.sadd(
                f"{tenant_id}:pool:{pool_id}:instances", instance_id
            )

    async def _remove_instance(
        self, tenant_id: str, instance_id: str, payload: dict
    ) -> None:
        """Remove instância do Redis + SREM de todos os pool SETs."""
        await self._redis.delete(f"{tenant_id}:instance:{instance_id}")
        for pool_id in payload.get("pools", []):
            await self._redis.srem(
                f"{tenant_id}:pool:{pool_id}:instances", instance_id
            )
        logger.debug("Removed instance: %s", instance_id)

    async def _scan_instances_from_redis(
        self, tenant_id: str
    ) -> dict[str, dict]:
        """
        Escaneia todas as chaves {tenant_id}:instance:* no Redis.
        Retorna um mapa instance_id → payload.
        """
        pattern = f"{tenant_id}:instance:*"
        result: dict[str, dict] = {}
        prefix  = f"{tenant_id}:instance:"
        cursor  = 0
        while True:
            cursor, keys = await self._redis.scan(cursor, match=pattern, count=100)
            for key in keys:
                key_str = key.decode() if isinstance(key, bytes) else key
                instance_id = key_str[len(prefix):]
                try:
                    raw = await self._redis.get(key_str)
                    if raw:
                        result[instance_id] = json.loads(raw)
                except Exception:
                    pass
            if cursor == 0:
                break
        return result

    async def _sync_pool_sets(
        self,
        tenant_id: str,
        desired:   dict[str, dict],
        actual:    dict[str, dict],
        dry_run:   bool = False,
    ) -> None:
        """
        Garante que cada pool:*:instances SET contém exatamente as instâncias
        desejadas — remove entradas obsoletas, adiciona as que faltam.
        """
        # Mapa pool_id → instâncias desejadas nesse pool
        desired_by_pool: dict[str, set[str]] = {}
        for iid, payload in desired.items():
            for pool_id in payload.get("pools", []):
                desired_by_pool.setdefault(pool_id, set()).add(iid)

        # Para cada pool com instâncias desejadas, reconcilia o SET
        for pool_id, want_set in desired_by_pool.items():
            set_key  = f"{tenant_id}:pool:{pool_id}:instances"
            have_raw = await self._redis.smembers(set_key)
            have_set = {
                m.decode() if isinstance(m, bytes) else m for m in have_raw
            }

            if not dry_run:
                for iid in want_set - have_set:
                    await self._redis.sadd(set_key, iid)
                for iid in have_set - want_set:
                    # Remove unconditionally: membership is per-pool.
                    # An instance moved to a different pool is still in `desired`
                    # (for its new pool), but must be removed from this pool's SET.
                    await self._redis.srem(set_key, iid)

    async def _reconcile_pool_configs(
        self,
        http:           aiohttp.ClientSession,
        tenant_id:      str,
        registry_pools: list[dict],
        active_pool_ids: list[str],
        report:         ReconciliationReport,
        dry_run:        bool = False,
    ) -> None:
        """
        Reconcilia todas as chaves pool_config:{pool_id} no Redis com o estado
        do Registry.

        Operações:
          - Escreve/atualiza pool_config para cada pool ativo no Registry
            (apenas quando o conteúdo diverge ou a chave expirou)
          - Deleta pool_config para pools removidos do Registry
          - Limpa o SET pool:{pool_id}:instances de pools removidos se estiver vazio
        """
        registry_pool_ids = {p["pool_id"] for p in registry_pools if "pool_id" in p}

        # ── A. Escrever / atualizar configs de pools ativos ────────────────────
        for pool_data in registry_pools:
            pool_id    = pool_data.get("pool_id")
            if not pool_id:
                continue
            config_key = f"{tenant_id}:pool_config:{pool_id}"
            try:
                existing_raw = await self._redis.get(config_key)
                if existing_raw:
                    existing = json.loads(existing_raw)
                    if not _pool_config_diverged(existing, pool_data):
                        # Conteúdo idêntico — apenas renova TTL
                        if not dry_run:
                            await self._redis.expire(config_key, _POOL_CONFIG_TTL_S)
                        # Sempre atualiza cache em memória para o heartbeat
                        if tenant_id not in self._pool_configs:
                            self._pool_configs[tenant_id] = {}
                        self._pool_configs[tenant_id][pool_id] = pool_data
                        continue

                # Chave inexistente ou conteúdo divergente — escreve
                if not dry_run:
                    await self._redis.set(
                        config_key, json.dumps(pool_data), ex=_POOL_CONFIG_TTL_S
                    )
                    logger.debug(
                        "Pool config %s: %s",
                        pool_id, "updated" if existing_raw else "written",
                    )
                report.pools_written.append(pool_id)

            except Exception as exc:
                msg = f"pool_config write failed for {pool_id}: {exc}"
                logger.warning(msg)
                report.errors.append(msg)
                continue

            # Atualiza cache em memória — usado pelo heartbeat para renovar/recriar
            if tenant_id not in self._pool_configs:
                self._pool_configs[tenant_id] = {}
            self._pool_configs[tenant_id][pool_id] = pool_data

        # ── B. Remover configs de pools que não existem mais no Registry ───────
        # Escaneia pool_config:* no Redis para encontrar orphans
        pattern = f"{tenant_id}:pool_config:*"
        prefix  = f"{tenant_id}:pool_config:"
        cursor  = 0
        while True:
            cursor, keys = await self._redis.scan(
                cursor, match=pattern, count=100
            )
            for key_raw in keys:
                key = key_raw.decode() if isinstance(key_raw, bytes) else key_raw
                orphan_pool_id = key[len(prefix):]
                if orphan_pool_id in registry_pool_ids:
                    continue

                # Pool removido do Registry — limpa config e SET de instâncias
                if not dry_run:
                    await self._redis.delete(key)
                    # Remove o SET de instâncias do pool apenas se estiver vazio
                    # (instâncias draining ainda podem estar lá; heartbeat as remove)
                    instances_key = f"{tenant_id}:pool:{orphan_pool_id}:instances"
                    remaining = await self._redis.scard(instances_key)
                    if remaining == 0:
                        await self._redis.delete(instances_key)
                        logger.debug(
                            "Removed empty pool instances SET: %s", orphan_pool_id
                        )
                    else:
                        logger.debug(
                            "Pool %s removed from Registry but %d instances still in SET — "
                            "will be cleaned by heartbeat",
                            orphan_pool_id, remaining,
                        )
                    # Evicta do cache em memória
                    if tenant_id in self._pool_configs:
                        self._pool_configs[tenant_id].pop(orphan_pool_id, None)
                report.pools_removed.append(orphan_pool_id)

            if cursor == 0:
                break

    async def _reconcile_pools_set(
        self,
        tenant_id:       str,
        registry_pool_ids: list[str],
        report:          ReconciliationReport,
        dry_run:         bool = False,
    ) -> None:
        """
        Mantém o SET global {tenant_id}:pools sincronizado com os pools
        ativos no Registry. Adiciona novos e remove pools que não existem mais.
        """
        set_key = f"{tenant_id}:pools"
        try:
            have_raw = await self._redis.smembers(set_key)
            have_set = {
                m.decode() if isinstance(m, bytes) else m for m in have_raw
            }
            want_set = set(registry_pool_ids)

            to_add    = want_set - have_set
            to_remove = have_set - want_set

            if not dry_run:
                if to_add:
                    await self._redis.sadd(set_key, *to_add)
                for pid in to_remove:
                    await self._redis.srem(set_key, pid)

            changed = list(to_add) + list(to_remove)
            if changed:
                report.pools_set_sync.extend(sorted(changed))
                logger.debug(
                    "Pools SET synced: +%d -%d", len(to_add), len(to_remove)
                )

        except Exception as exc:
            msg = f"pools SET sync failed: {exc}"
            logger.warning(msg)
            report.errors.append(msg)

    # ─── Helpers de I/O Registry ──────────────────────────────────────────────

    async def _fetch_agent_types(
        self, http: aiohttp.ClientSession, tenant_id: str
    ) -> list[dict]:
        """GET /v1/agent-types — retorna lista de agent types ativos do tenant."""
        url     = f"{self._registry_url}/v1/agent-types"
        headers = {"x-tenant-id": tenant_id}
        try:
            async with http.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    return body.get("agent_types", [])
                logger.warning(
                    "Agent Registry returned HTTP %d for agent-types list", resp.status
                )
        except Exception as exc:
            logger.warning("Could not reach Agent Registry (%s): %s", url, exc)
        return []

    async def _fetch_all_pools(
        self, http: aiohttp.ClientSession, tenant_id: str
    ) -> list[dict]:
        """
        GET /v1/pools — retorna TODOS os pools ativos do tenant em uma única
        chamada. Usado tanto para construir pool_channel_types quanto para a
        reconciliação completa de pool_config keys e do SET global {tenant}:pools.

        Fallback individual: se o endpoint de listagem não existir (HTTP 404/405),
        recupera pools um por um usando GET /v1/pools/{id} para os IDs conhecidos
        pelos agent types já carregados.
        """
        url     = f"{self._registry_url}/v1/pools"
        headers = {"x-tenant-id": tenant_id}
        try:
            async with http.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    # Suporta tanto {"pools": [...]} quanto lista directa
                    if isinstance(body, list):
                        return body
                    return body.get("pools", [])
                # Endpoint de listagem pode não existir em versões mais antigas
                logger.warning(
                    "GET /v1/pools returned HTTP %d — pool reconciliation will "
                    "be limited to pools referenced by agent types",
                    resp.status,
                )
        except Exception as exc:
            logger.warning(
                "Could not reach Agent Registry pool list (%s): %s", url, exc
            )
        return []


# ─── Funções puras (sem I/O) ──────────────────────────────────────────────────

def _extract_all_pool_ids(agent_types: list[dict]) -> list[str]:
    """Coleta todos os pool IDs distintos dos agent types não-humanos."""
    result: list[str] = []
    for at in agent_types:
        if at.get("framework") == "human":
            continue
        for p in at.get("pools", []):
            pid = p["pool_id"] if isinstance(p, dict) else p
            if pid not in result:
                result.append(pid)
    return result


def _build_desired_state(
    agent_types:       list[dict],
    pool_channel_types: dict[str, list[str]],
    tenant_id:         str,
) -> dict[str, dict]:
    """
    Constrói o mapa {instance_id → payload desejado} a partir dos agent types
    e dos channel_types dos pools associados.
    """
    desired: dict[str, dict] = {}
    for at in agent_types:
        if at.get("framework") == "human":
            continue

        agent_type_id   = at["agent_type_id"]
        framework       = at.get("framework", "")
        execution_model = at.get("execution_model", "stateless")
        max_concurrent  = at.get("max_concurrent_sessions", 1)
        at_pools: list[str] = [
            p["pool_id"] if isinstance(p, dict) else p
            for p in at.get("pools", [])
        ]

        # channel_types = união dos channel_types de todos os pools associados
        channel_types: list[str] = []
        for pid in at_pools:
            for ch in pool_channel_types.get(pid, []):
                if ch not in channel_types:
                    channel_types.append(ch)
        if not channel_types:
            channel_types = ["webchat"]

        for n in range(max_concurrent):
            instance_id = f"{agent_type_id}-{n + 1:03d}"
            desired[instance_id] = {
                "instance_id":             instance_id,
                "agent_type_id":           agent_type_id,
                "tenant_id":               tenant_id,
                "framework":               framework,
                "execution_model":         execution_model,
                "status":                  "ready",
                "state":                   "ready",
                "current_sessions":        0,
                "max_concurrent":          max_concurrent,
                "max_concurrent_sessions": max_concurrent,
                "pools":                   at_pools,
                "channel_types":           channel_types,
                "source":                  "bootstrap",
            }
    return desired


def _payload_diverged(current: dict, desired: dict) -> bool:
    """
    Retorna True se os campos gerenciáveis do payload atual divergem do desejado.
    Ignora campos transientes (status, timestamps, draining, pending_update, etc.).
    """
    MANAGED = {
        "agent_type_id", "framework", "execution_model",
        "max_concurrent", "max_concurrent_sessions",
        "pools", "channel_types", "source",
    }
    for key in MANAGED:
        if current.get(key) != desired.get(key):
            return True
    return False


def _pool_config_diverged(existing: dict, desired: dict) -> bool:
    """
    Retorna True se o pool_config cacheado diverge do estado atual do Registry.
    Compara os campos relevantes da configuração de pool; ignora campos transientes
    como updated_at, created_at e campos de auditoria.

    Campos gerenciados (subset que impacta roteamento):
      pool_id, name, channel_types, sla_target_ms, max_queue_size,
      scoring_weights, routing_mode, active, skills
    """
    MANAGED = {
        # Core identity and routing hard-filters
        "pool_id", "name", "channel_types", "active",
        # Queue and SLA parameters
        "sla_target_ms", "max_queue_size",
        # Scoring parameters (impact decide.py / scorer.py)
        "scoring_weights", "routing_mode", "routing_expression",
        "competency_weights", "aging_factor", "breach_factor",
        # Skills and cross-site routing
        "skills", "remote_sites",
    }
    for key in MANAGED:
        if existing.get(key) != desired.get(key):
            return True
    return False
