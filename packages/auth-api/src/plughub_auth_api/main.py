"""
main.py
Entrypoint da auth-api.

Sobe FastAPI com lifespan que gerencia o pool asyncpg e faz seed do admin inicial.
Porta padrão: 3200.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import asyncpg
import uvicorn
from fastapi import FastAPI

from . import db as db_mod
from .config import get_settings
from .password import hash_password
from .permissions import ensure_permissions_schema
from .router import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("plughub.auth_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    logger.info("auth-api starting — connecting to PostgreSQL …")
    pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
    app.state.pool = pool

    await db_mod.ensure_schema(pool)
    await ensure_permissions_schema(pool)

    # Seed do usuário admin padrão (idempotente)
    await db_mod.seed_admin_if_absent(
        pool,
        tenant_id=settings.seed_tenant_id,
        email=settings.seed_admin_email,
        password_hash=hash_password(settings.seed_admin_password),
        name=settings.seed_admin_name,
    )

    logger.info("auth-api ready — port %d", settings.port)
    yield

    await pool.close()
    logger.info("auth-api shutdown complete")


def build_app() -> FastAPI:
    app = FastAPI(
        title="PlugHub Auth API",
        version="1.0.0",
        description="User management, JWT issuance, session lifecycle",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "auth-api"}

    app.include_router(router)
    return app


def main() -> None:
    settings = get_settings()
    app = build_app()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
