"""
main.py
Entrypoint da auth-api.

Sobe FastAPI com lifespan que gerencia o pool asyncpg e faz seed do admin inicial.
Porta padrão: 3200.
"""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
from contextlib import asynccontextmanager
from typing import Any

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

# ─── Caminho canônico de infra/modules.yaml ───────────────────────────────────
# Em desenvolvimento: packages/auth-api/src/plughub_auth_api/main.py
#   → parents[4] = raiz do monorepo → infra/modules.yaml
# Em container (WORKDIR /app): /app/src/plughub_auth_api/main.py
#   → apenas parents[0..3] disponíveis → IndexError sem a guarda abaixo.
# Env var PLUGHUB_AUTH_MODULES_YAML tem prioridade (montado via volume no compose).
_THIS_FILE = pathlib.Path(__file__).resolve()
_MODULES_YAML_OVERRIDE = os.environ.get("PLUGHUB_AUTH_MODULES_YAML", "")
if _MODULES_YAML_OVERRIDE:
    _MODULES_YAML = pathlib.Path(_MODULES_YAML_OVERRIDE)
else:
    try:
        _REPO_ROOT = _THIS_FILE.parents[4]   # dev: plughub/
        _MODULES_YAML = _REPO_ROOT / "infra" / "modules.yaml"
    except IndexError:
        # Fallback para containers onde o WORKDIR é /app (profundidade menor)
        _MODULES_YAML = pathlib.Path("/infra/modules.yaml")


async def _register_platform_modules(pool: asyncpg.Pool) -> None:
    """
    Lê infra/modules.yaml e faz upsert de cada módulo na tabela
    auth.module_registry (tenant_id = NULL = módulo de plataforma).
    Idempotente — safe para ser chamado em cada restart.
    """
    try:
        import yaml  # pyyaml — instalado como dependência
    except ImportError:
        logger.warning(
            "PyYAML não encontrado — módulos de plataforma não serão registrados. "
            "Instale pyyaml para habilitar o registro automático de módulos."
        )
        return

    if not _MODULES_YAML.exists():
        logger.warning(
            "infra/modules.yaml não encontrado em %s — pulando registro de módulos.",
            _MODULES_YAML,
        )
        return

    with open(_MODULES_YAML, "r", encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh) or {}

    modules: list[dict[str, Any]] = data.get("modules", [])
    if not modules:
        logger.warning("infra/modules.yaml não contém nenhum módulo declarado.")
        return

    registered = 0
    errors = 0
    for mod in modules:
        module_id: str = mod.get("module_id", "")
        if not module_id:
            logger.warning("Módulo sem module_id em modules.yaml — ignorado: %s", mod)
            continue
        try:
            await db_mod.upsert_module(
                pool,
                module_id=module_id,
                label=mod.get("label", module_id),
                icon=mod.get("icon", "📦"),
                nav_path=mod.get("nav_path", ""),
                schema=mod.get("permission_schema", {}),
                tenant_id=None,   # NULL = módulo de plataforma (built-in)
                active=mod.get("active", True),
            )
            registered += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("Erro ao registrar módulo '%s': %s", module_id, exc)
            errors += 1

    logger.info(
        "Módulos de plataforma: %d registrados, %d erros (source: %s)",
        registered, errors, _MODULES_YAML,
    )


async def _create_pool_with_retry(
    dsn: str, *, min_size: int, max_size: int, retries: int = 10, delay: float = 2.0
) -> asyncpg.Pool:
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)
        except Exception as exc:
            last_exc = exc
            wait = delay * attempt
            logger.warning(
                "asyncpg pool creation failed (attempt %d/%d) — retrying in %.1fs: %s",
                attempt, retries, wait, exc,
            )
            await asyncio.sleep(wait)
    raise RuntimeError(f"Could not connect to PostgreSQL after {retries} attempts") from last_exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    logger.info("auth-api starting — connecting to PostgreSQL …")
    pool = await _create_pool_with_retry(settings.database_url, min_size=2, max_size=10)
    app.state.pool = pool

    for attempt in range(1, 11):
        try:
            await db_mod.ensure_schema(pool)
            await ensure_permissions_schema(pool)
            break
        except Exception as exc:
            if attempt == 10:
                logger.error("Schema setup failed after 10 attempts: %s", exc)
                raise
            wait = 2.0 * attempt
            logger.warning("Schema setup attempt %d/10 failed — retrying in %.1fs: %s", attempt, wait, exc)
            await asyncio.sleep(wait)

    # Registra módulos de plataforma a partir de infra/modules.yaml (idempotente)
    await _register_platform_modules(pool)

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
