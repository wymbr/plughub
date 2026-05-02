"""
main.py
Entrypoint do Usage Aggregator.

Sobe dois serviços concorrentemente:
  1. Kafka consumer  — processa usage.events e usage.cycle_reset
  2. HTTP admin API  — POST /admin/cycle-reset para trigger manual

Configuração via variáveis de ambiente.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

logger = logging.getLogger("plughub.usage_aggregator")

KAFKA_BROKERS = os.getenv("KAFKA_BROKERS", "kafka:9092")
REDIS_URL     = os.getenv("REDIS_URL",     "redis://redis:6379")
DATABASE_URL  = os.getenv("DATABASE_URL",  "postgresql://plughub:plughub@postgres:5432/plughub")
ADMIN_TOKEN   = os.getenv("USAGE_ADMIN_TOKEN", "")   # vazio = sem autenticação (dev only)
HTTP_PORT     = int(os.getenv("USAGE_HTTP_PORT", "3950"))


# ─── FastAPI admin server ─────────────────────────────────────────────────────

def _build_app(redis_url: str) -> object:
    """
    Constrói a FastAPI app do servidor admin.
    Importado tardiamente para não exigir FastAPI em testes unitários.
    """
    import redis.asyncio as aioredis
    from fastapi import FastAPI, HTTPException, Header
    from pydantic import BaseModel

    from .cycle_reset import CycleResetter

    _redis: aioredis.Redis | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[type-arg]
        nonlocal _redis
        _redis = aioredis.from_url(redis_url, decode_responses=True)
        yield
        if _redis:
            await _redis.aclose()

    app = FastAPI(
        title="Usage Aggregator Admin API",
        version="1.0.0",
        lifespan=lifespan,
    )

    class CycleResetRequest(BaseModel):
        tenant_id:   str = "*"            # "*" = todos os tenants
        cycle_start: str | None = None    # ISO-8601; None = now()

    def _check_auth(x_admin_token: str | None) -> None:
        if ADMIN_TOKEN and x_admin_token != ADMIN_TOKEN:
            raise HTTPException(status_code=401, detail="Invalid admin token")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "service": "usage-aggregator-admin"}

    @app.post("/admin/cycle-reset")
    async def cycle_reset(
        body:            CycleResetRequest,
        x_admin_token:   str | None = Header(default=None),
    ) -> dict:
        """
        Reseta os contadores de uso de um tenant (ou todos) para iniciar um novo ciclo.

        Body:
          - tenant_id:   tenant específico ou "*" para todos (padrão: "*")
          - cycle_start: ISO-8601 do início do novo ciclo (padrão: agora em UTC)

        Requer header X-Admin-Token quando USAGE_ADMIN_TOKEN estiver configurado.
        """
        _check_auth(x_admin_token)
        if _redis is None:
            raise HTTPException(status_code=503, detail="Redis not available")

        resetter = CycleResetter(redis_client=_redis)
        report   = await resetter.reset(
            tenant_id=body.tenant_id,
            cycle_start=body.cycle_start,
        )

        status_code = 200 if report.ok() else 207
        return {
            "status":           "ok" if report.ok() else "partial",
            "tenant_ids":        report.tenant_ids,
            "counters_deleted":  report.counters_deleted,
            "cycle_starts_set":  report.cycle_starts_set,
            "new_cycle_start":   report.new_cycle_start,
            "errors":            report.errors,
        }

    return app


# ─── Entrypoint ───────────────────────────────────────────────────────────────

async def _run_all() -> None:
    """Roda Kafka consumer e HTTP admin server concorrentemente."""
    import uvicorn
    from .consumer import run_consumer

    app = _build_app(redis_url=REDIS_URL)

    config   = uvicorn.Config(app, host="0.0.0.0", port=HTTP_PORT, log_level="warning")
    server   = uvicorn.Server(config)

    kafka_task = asyncio.create_task(
        run_consumer(
            kafka_brokers=KAFKA_BROKERS,
            redis_url=REDIS_URL,
            database_url=DATABASE_URL,
        ),
        name="kafka-consumer",
    )
    http_task  = asyncio.create_task(server.serve(), name="http-admin")

    logger.info(
        "Usage Aggregator started — HTTP admin on :%d, Kafka brokers=%s",
        HTTP_PORT, KAFKA_BROKERS,
    )

    # Aguarda qualquer uma das tasks terminar (shutdown sinal ou erro)
    done, pending = await asyncio.wait(
        {kafka_task, http_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    # Re-raise se houve erro
    for task in done:
        if task.exception():
            raise task.exception()  # type: ignore[misc]


def main() -> None:
    asyncio.run(_run_all())


if __name__ == "__main__":
    main()
