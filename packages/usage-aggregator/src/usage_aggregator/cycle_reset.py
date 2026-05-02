"""
cycle_reset.py
CycleResetter — reset mensal dos contadores de uso no Redis.

Responsabilidades:
  1. Recebe um sinal de reset (via Kafka topic `usage.cycle_reset` ou trigger HTTP admin)
  2. Apaga todas as chaves {tenant_id}:usage:current:* (contadores do ciclo)
  3. Apaga {tenant_id}:usage:cycle_start (início do ciclo corrente)
  4. Grava {tenant_id}:usage:cycle_start com o timestamp de início do novo ciclo
  5. Retorna um Relatorio com as chaves deletadas e o novo cycle_start

Idempotência: rodar reset duas vezes no mesmo ciclo apenas zera os contadores
(já zerados na segunda execução) e atualiza o cycle_start.

Escopo:
  - tenant_id = "*" (wildcard) → reset de TODOS os tenants (uso administrativo).
  - tenant_id específico       → reset apenas desse tenant.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import redis.asyncio as aioredis

logger = logging.getLogger("plughub.usage_aggregator.cycle_reset")

# TTL padrão para o novo cycle_start (45 dias — igual ao dos contadores)
CYCLE_START_TTL = 45 * 24 * 3600


@dataclass
class ResetReport:
    tenant_ids:      list[str]
    counters_deleted: int
    cycle_starts_set: int
    new_cycle_start:  str
    errors:           list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        status = "OK" if self.ok() else f"ERRORS={len(self.errors)}"
        return (
            f"CycleReset {status} tenants={self.tenant_ids} "
            f"counters_deleted={self.counters_deleted} "
            f"cycle_starts_set={self.cycle_starts_set} "
            f"new_cycle_start={self.new_cycle_start}"
        )


class CycleResetter:
    """
    Executa o reset de contadores de uso para um ou todos os tenants.

    Uso:
        resetter = CycleResetter(redis_client)
        report = await resetter.reset(tenant_id="tenant_demo")
        # ou: report = await resetter.reset(tenant_id="*")  # todos os tenants
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client

    async def reset(self, tenant_id: str, cycle_start: str | None = None) -> ResetReport:
        """
        Reseta os contadores de uso para o tenant indicado.

        Args:
            tenant_id:   ID do tenant ou "*" para todos os tenants.
            cycle_start: ISO-8601 do início do novo ciclo. Padrão: agora (UTC).

        Returns:
            ResetReport com estatísticas da operação.
        """
        new_cycle_start = cycle_start or datetime.now(timezone.utc).isoformat()
        counters_deleted = 0
        cycle_starts_set = 0
        errors: list[str] = []

        if tenant_id == "*":
            tenant_ids = await self._discover_tenants()
        else:
            tenant_ids = [tenant_id]

        for tid in tenant_ids:
            try:
                deleted, set_ok = await self._reset_tenant(tid, new_cycle_start)
                counters_deleted += deleted
                if set_ok:
                    cycle_starts_set += 1
            except Exception as exc:
                msg = f"tenant={tid} error={exc}"
                logger.error("CycleReset failed for %s", msg)
                errors.append(msg)

        report = ResetReport(
            tenant_ids=tenant_ids,
            counters_deleted=counters_deleted,
            cycle_starts_set=cycle_starts_set,
            new_cycle_start=new_cycle_start,
            errors=errors,
        )
        logger.info(report.summary())
        return report

    # ─── Internal helpers ─────────────────────────────────────────────────────

    async def _reset_tenant(self, tenant_id: str, new_cycle_start: str) -> tuple[int, bool]:
        """
        Reseta os contadores de um tenant específico.
        Returns: (number_of_counters_deleted, cycle_start_set_ok)
        """
        counter_pattern  = f"{tenant_id}:usage:current:*"
        cycle_start_key  = f"{tenant_id}:usage:cycle_start"

        # 1. Encontra todas as chaves de contadores do tenant
        counter_keys: list[str] = []
        async for key in self._redis.scan_iter(counter_pattern):
            counter_keys.append(key)

        # 2. Deleta contadores (atomicamente em pipeline)
        deleted = 0
        if counter_keys:
            async with self._redis.pipeline(transaction=False) as pipe:
                for key in counter_keys:
                    pipe.delete(key)
                results = await pipe.execute()
            deleted = sum(1 for r in results if r)

        # 3. Deleta o cycle_start antigo e escreve o novo
        await self._redis.delete(cycle_start_key)
        await self._redis.set(cycle_start_key, new_cycle_start, ex=CYCLE_START_TTL)

        logger.info(
            "Tenant %s reset: deleted %d counters, new cycle_start=%s",
            tenant_id, deleted, new_cycle_start,
        )
        return deleted, True

    async def _discover_tenants(self) -> list[str]:
        """
        Descobre tenants activos pela existência de chaves `*:usage:cycle_start`.
        Fallback seguro: se não houver chaves, retorna lista vazia.
        """
        tenants: set[str] = set()
        async for key in self._redis.scan_iter("*:usage:cycle_start"):
            # key format: "{tenant_id}:usage:cycle_start"
            parts = key.split(":")
            if len(parts) >= 3:
                tenant = parts[0]
                tenants.add(tenant)
        return sorted(tenants)
