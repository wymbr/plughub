"""
store.py
ConfigStore — public interface for reading and writing platform configuration.

Combines the DB layer (asyncpg) with the cache layer (Redis) to provide:
  - Fast reads via Redis cache (TTL 60s)
  - Reliable writes via PostgreSQL with immediate cache invalidation
  - Two-level resolution: tenant-specific wins over global (__global__)

Usage by platform modules:
  value = await store.get("tenant_telco", "sentiment", "thresholds")
  # → {"satisfied": [0.3, 1.0], "neutral": [-0.3, 0.3], ...}

  all_ns = await store.list_namespace("tenant_telco", "routing")
  # → {"claim_lease_s": 180, "sla_default_ms": 480000, ...}
  #   (o exemplo citava `snapshot_ttl_s`, removida em 2026-08-03 por não ter
  #    leitor — exemplo de docstring envelhece calado e vira a única prova de
  #    que um campo existe)
"""
from __future__ import annotations

import logging
from typing import Any

import asyncpg

from .cache import MISS, ConfigCache
from .db import (
    GLOBAL,
    db_delete,
    db_get,
    db_get_raw,
    db_key_provenance,
    db_list_all,
    db_list_namespace,
    db_list_namespace_entries,
    db_set,
    db_tenants_overriding,
    ensure_schema,
)

logger = logging.getLogger("plughub.config.store")


class ConfigStore:
    """
    Thread-safe, async-first configuration store.

    All reads use a read-through cache (Redis TTL 60s).
    All writes invalidate the affected cache entries immediately.
    """

    def __init__(self, pool: asyncpg.Pool, cache: ConfigCache) -> None:
        self._pool  = pool
        self._cache = cache

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def setup(self) -> None:
        """Ensures DB schema exists. Call once at application startup."""
        await ensure_schema(self._pool)

    # ── read API ──────────────────────────────────────────────────────────────

    async def get(self, tenant_id: str, namespace: str, key: str) -> Any | None:
        """
        Resolved lookup: tenant-specific value → global default → None.
        Reads from Redis cache on hit; falls back to DB and re-populates cache.
        """
        cached = await self._cache.get(tenant_id, namespace, key)
        if cached is not MISS:
            return cached

        value = await db_get(self._pool, tenant_id, namespace, key)
        # Cache even None values (to avoid repeated DB misses for unknown keys)
        await self._cache.set(tenant_id, namespace, key, value)
        return value

    async def get_or_default(
        self,
        tenant_id: str,
        namespace: str,
        key: str,
        default: Any,
    ) -> Any:
        """Like get(), but returns `default` when no value exists."""
        value = await self.get(tenant_id, namespace, key)
        return value if value is not None else default

    async def list_namespace(self, tenant_id: str, namespace: str) -> dict[str, Any]:
        """
        All resolved keys in a namespace.
        Returns {key: resolved_value} with tenant-specific values overriding globals.
        """
        cached = await self._cache.get_namespace(tenant_id, namespace)
        if cached is not MISS:
            return cached

        data = await db_list_namespace(self._pool, tenant_id, namespace)
        await self._cache.set_namespace(tenant_id, namespace, data)
        return data

    async def list_all(self, tenant_id: str) -> dict[str, dict[str, Any]]:
        """
        All resolved config for a tenant, grouped by namespace.
        Not cached — intended for admin/diagnostic use only.
        """
        return await db_list_all(self._pool, tenant_id)

    async def provenance(self, tenant_id: str, namespace: str) -> dict[str, dict]:
        """Por key: qual escopo RESPONDE, e se o outro existe e diverge (ALW-06).

        ⚠️ **Não passa pelo cache, de propósito.** O cache serve leitura de runtime,
        onde o valor resolvido é o que importa. Aqui a pergunta é de AUTORIA — *"o
        que eu vou sombrear se salvar?"* — e uma resposta velha recriaria exatamente
        o silêncio que este relatório existe para remover: quem acabou de criar um
        override veria a tela dizer que não há nenhum.

        `diverges` compara pela forma canônica de `config_drift.canonical`, a MESMA
        que o relatório de seed usa. Duas formas de decidir "é igual?" acabariam
        divergindo, e aí a permissiva é a que vale.
        """
        from .config_drift import canonical

        bruto = await db_key_provenance(self._pool, tenant_id, namespace)
        out: dict[str, dict] = {}
        for key, lados in sorted(bruto.items()):
            tem_global = "global" in lados
            tem_tenant = "tenant" in lados
            diverge = None
            if tem_global and tem_tenant:
                diverge = canonical(lados["global"]) != canonical(lados["tenant"])
            out[key] = {
                "effective_scope": "tenant" if tem_tenant else "global",
                "global_present":  tem_global,
                "tenant_present":  tem_tenant,
                "diverges":        diverge,
            }
        return out

    async def tenants_overriding(self, namespace: str, key: str) -> list[str]:
        """Tenants para quem uma escrita no `__global__` desta key não tem efeito."""
        return await db_tenants_overriding(self._pool, namespace, key)

    async def list_namespace_raw(
        self, tenant_id: str, namespace: str
    ) -> list[dict]:
        """
        Raw (non-resolved) entries explicitly set for (tenant_id, namespace).
        Used by the admin API to show what is overriding the global default.
        """
        return await db_list_namespace_entries(self._pool, tenant_id, namespace)

    async def get_entry(
        self, tenant_id: str, namespace: str, key: str
    ) -> dict | None:
        """
        Returns the raw entry dict (with metadata) for an exact (tenant, ns, key).
        No fallback resolution. Returns None if not explicitly set.
        """
        return await db_get_raw(self._pool, tenant_id, namespace, key)

    # ── write API ─────────────────────────────────────────────────────────────

    async def set(
        self,
        tenant_id: str | None,
        namespace: str,
        key: str,
        value: Any,
        description: str = "",
    ) -> None:
        """
        Upsert a config value.
        tenant_id=None sets the global platform default (stored as '__global__').
        tenant_id="tenant_telco" sets a tenant-specific override.
        """
        t_id = tenant_id or GLOBAL
        await db_set(self._pool, t_id, namespace, key, value, description)
        await self._cache.invalidate(t_id, namespace, key)
        logger.info("config.set tenant=%s ns=%s key=%s", t_id, namespace, key)

    async def delete(
        self,
        tenant_id: str | None,
        namespace: str,
        key: str,
    ) -> bool:
        """
        Removes a config entry. Returns True if it existed.
        Deleting a tenant override restores the global default for that tenant.
        Deleting the global entry leaves tenants without a fallback (value returns None).
        """
        t_id = tenant_id or GLOBAL
        deleted = await db_delete(self._pool, t_id, namespace, key)
        if deleted:
            await self._cache.invalidate(t_id, namespace, key)
            logger.info("config.delete tenant=%s ns=%s key=%s", t_id, namespace, key)
        return deleted
