"""
config.py
Settings for the PlugHub Dialog API.
All values have defaults suitable for local/visual development.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLUGHUB_DIALOG_", case_sensitive=False)

    # HTTP
    host:    str = "0.0.0.0"
    port:    int = 3760
    workers: int = 1

    # PostgreSQL (uses the shared plughub DB, schema=dialog)
    database_url: str = "postgresql://plughub:plughub@postgres:5432/plughub"

    # Porta de SISTEMA (seeds): lê e escreve. Vazio FECHA esta porta — até a AUT-62
    # (2026-09-29) desligava o portão inteiro. Ver `router._caller`.
    admin_token: str = ""

    # AUT-62 — porta de RUNTIME, só de leitura (mcp-server `form_get`/survey/segment e
    # channel-gateway survey web/pin/collect). Vazio NÃO libera: só fecha a porta.
    service_token: str = ""

    # Mesmo segredo HS256 da auth-api — valida o Bearer do caminho ABAC.
    jwt_secret:  str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
