"""
instance_bootstrap.py
Configuration-driven agent instance registration.

Princípio: billing é por instância configurada → o Agent Registry é a fonte de
verdade. Ao iniciar, o Bridge lê todos os AgentTypes ativos e registra no Redis
exatamente as instâncias configuradas (max_concurrent_sessions slots por tipo).

Fluxo:
  startup → _bootstrap_tenant(tenant_id)
    → GET /v1/agent-types  (todos ativos do tenant)
    → para cada AgentType com framework != "human":
        para n in range(max_concurrent_sessions):
          instance_id = f"{agent_type_id}-{n+1:03d}"
          escreve {tenant}:instance:{instance_id} com status=ready, TTL=30s
          adiciona ao {tenant}:pool:{pool}:instances
    → para cada Pool:
        escreve {tenant}:pool_config:{pool_id}

  heartbeat loop → a cada HEARTBEAT_INTERVAL_S (15s):
    → re-escreve todas as instâncias ready (renova TTL)
    → re-sincroniza se registry.changed chegou no Kafka

  registry.changed → invalida cache e re-executa bootstrap

Notas:
  - Human agents NÃO são bootstrapped — login é user-initiated (Agent Assist UI).
  - Instâncias busy mantêm TTL via agent_busy no mcp-server; o heartbeat não
    sobrescreve instâncias busy.
  - Idempotente: re-executar não cria duplicatas.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import aiohttp
import redis.asyncio as aioredis

logger = logging.getLogger("plughub.orchestrator-bridge.bootstrap")

# TTL das instâncias no Redis — deve ser maior que o TTL do Routing Engine (30s)
# Heartbeat renova a cada HEARTBEAT_INTERVAL_S, garantindo que TTL nunca expire
_INSTANCE_TTL_S   = 35
_HEARTBEAT_INTERVAL_S = 15
_POOL_CONFIG_TTL_S    = 86400   # 24h


class InstanceBootstrap:
    """
    Registra e mantém instâncias de agentes IA no Redis a partir do Agent Registry.

    Uso:
        bootstrap = InstanceBootstrap(redis, registry_url, tenant_ids)
        await bootstrap.run_once()          # startup inicial
        asyncio.create_task(bootstrap.heartbeat_loop())   # mantém TTL vivo
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
        # instance_id → payload dict — mantido em memória para heartbeat eficiente
        self._registered: dict[str, dict] = {}
        self._needs_refresh = False

    def request_refresh(self) -> None:
        """Sinaliza que o heartbeat deve re-executar bootstrap (registry.changed)."""
        self._needs_refresh = True

    async def run_once(self, http: aiohttp.ClientSession | None = None) -> None:
        """Bootstrapa todas as instâncias configuradas. Idempotente."""
        own_session = http is None
        if own_session:
            http = aiohttp.ClientSession()
        try:
            for tenant_id in self._tenant_ids:
                await self._bootstrap_tenant(http, tenant_id)
        finally:
            if own_session:
                await http.close()

    async def heartbeat_loop(self) -> None:
        """
        Loop infinito que renova o TTL das instâncias registered a cada 15s.
        Também re-executa bootstrap quando registry.changed chega.
        """
        logger.info(
            "Instance heartbeat loop started — interval=%ds tenants=%s",
            _HEARTBEAT_INTERVAL_S, self._tenant_ids,
        )
        async with aiohttp.ClientSession() as http:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
                try:
                    if self._needs_refresh:
                        self._needs_refresh = False
                        logger.info("registry.changed received — re-bootstrapping instances")
                        await self.run_once(http)
                    else:
                        await self._heartbeat_tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("Heartbeat tick error: %s", exc)

    # ─── Private ─────────────────────────────────────────────────────────────────

    async def _bootstrap_tenant(
        self, http: aiohttp.ClientSession, tenant_id: str
    ) -> None:
        """Registra todas as instâncias ativas do tenant no Redis."""
        agent_types = await self._fetch_agent_types(http, tenant_id)
        if not agent_types:
            logger.warning(
                "No agent types found in registry for tenant=%s — bootstrap skipped", tenant_id
            )
            return

        pools_seen: set[str] = set()

        for at in agent_types:
            framework = at.get("framework", "")
            if framework == "human":
                # Human agents log in themselves via Agent Assist UI — skip
                continue

            agent_type_id      = at["agent_type_id"]
            max_concurrent     = at.get("max_concurrent_sessions", 1)
            execution_model    = at.get("execution_model", "stateless")
            at_pools: list[str] = [
                p["pool_id"] if isinstance(p, dict) else p
                for p in at.get("pools", [])
            ]

            for n in range(max_concurrent):
                instance_id = f"{agent_type_id}-{n + 1:03d}"
                payload = {
                    "instance_id":           instance_id,
                    "agent_type_id":         agent_type_id,
                    "tenant_id":             tenant_id,
                    "framework":             framework,
                    "execution_model":       execution_model,
                    "status":                "ready",
                    "state":                 "ready",
                    "current_sessions":      0,
                    "max_concurrent":        max_concurrent,
                    "max_concurrent_sessions": max_concurrent,
                    "pools":                 at_pools,
                    "channel_types":         at.get("channel_types", ["webchat"]),
                    "registered_at":         datetime.now(timezone.utc).isoformat(),
                    "source":                "bootstrap",
                }

                # Não sobrescreve instâncias busy — Routing Engine controla esse estado
                existing_raw = await self._redis.get(
                    f"{tenant_id}:instance:{instance_id}"
                )
                if existing_raw:
                    try:
                        existing = json.loads(existing_raw)
                        if existing.get("status") in ("busy", "paused"):
                            # Renova TTL sem alterar estado
                            await self._redis.expire(
                                f"{tenant_id}:instance:{instance_id}", _INSTANCE_TTL_S
                            )
                            self._registered[instance_id] = existing
                            continue
                    except Exception:
                        pass

                await self._redis.set(
                    f"{tenant_id}:instance:{instance_id}",
                    json.dumps(payload),
                    ex=_INSTANCE_TTL_S,
                )
                for pool_id in at_pools:
                    await self._redis.sadd(f"{tenant_id}:pool:{pool_id}:instances", instance_id)
                    pools_seen.add((tenant_id, pool_id))

                self._registered[instance_id] = payload
                logger.debug(
                    "Bootstrapped instance %s → pools=%s", instance_id, at_pools
                )

            # Pool config (pool_config_key) — necessário para Routing Engine calcular score
            await self._bootstrap_pool_configs(http, tenant_id, at_pools)

        registered_count = sum(
            1 for p in self._registered.values()
            if p.get("tenant_id") == tenant_id
        )
        logger.info(
            "Bootstrap complete tenant=%s — %d instances ready", tenant_id, registered_count
        )

    async def _bootstrap_pool_configs(
        self,
        http:      aiohttp.ClientSession,
        tenant_id: str,
        pool_ids:  list[str],
    ) -> None:
        """Escreve pool_config no Redis para cada pool que ainda não estiver cacheado."""
        for pool_id in pool_ids:
            config_key = f"{tenant_id}:pool_config:{pool_id}"
            if await self._redis.exists(config_key):
                continue  # já cacheado — evita chamada desnecessária

            url = f"{self._registry_url}/v1/pools/{pool_id}"
            headers = {"x-tenant-id": tenant_id}
            try:
                async with http.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        pool_data = await resp.json()
                        await self._redis.set(
                            config_key, json.dumps(pool_data), ex=_POOL_CONFIG_TTL_S
                        )
                        logger.debug("Pool config cached: %s", pool_id)
            except Exception as exc:
                logger.warning("Could not fetch pool config %s: %s", pool_id, exc)

    async def _heartbeat_tick(self) -> None:
        """Renova TTL de todas as instâncias registered sem re-consultar o Registry."""
        for instance_id, payload in list(self._registered.items()):
            tenant_id = payload.get("tenant_id", "")
            key = f"{tenant_id}:instance:{instance_id}"
            try:
                # Lê estado atual — se busy, apenas renova TTL; se expirou, restaura
                raw = await self._redis.get(key)
                if raw:
                    current = json.loads(raw)
                    if current.get("status") in ("busy", "paused"):
                        await self._redis.expire(key, _INSTANCE_TTL_S)
                        continue
                # Restaura instância como ready (estava expirada ou idle)
                await self._redis.set(key, json.dumps(payload), ex=_INSTANCE_TTL_S)
                for pool_id in payload.get("pools", []):
                    await self._redis.sadd(
                        f"{tenant_id}:pool:{pool_id}:instances", instance_id
                    )
            except Exception as exc:
                logger.warning("Heartbeat failed for %s: %s", instance_id, exc)

    async def _fetch_agent_types(
        self, http: aiohttp.ClientSession, tenant_id: str
    ) -> list[dict]:
        """GET /v1/agent-types — retorna lista de agent types ativos do tenant."""
        url = f"{self._registry_url}/v1/agent-types"
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
