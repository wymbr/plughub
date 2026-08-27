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

    # Portao de ESCRITA (dual: admin-token de sistema OU Bearer + ABAC
    # `config.dialog_forms`). Vazio DESABILITA o portao — postura preservada, mas
    # agora LOGADA em WARNING pelo `plughub_authz.enforce_write`.
    #
    # Leituras (list/get, e o `form_get` do mcp-server + o survey web do
    # channel-gateway) seguem ABERTAS de proposito: sao chamadores de runtime sem
    # credencial, e o conteudo e masked-by-construction (nenhum valor de PII no
    # store). O campo ABAC governa quem EDITA o formulario, nao quem o renderiza.
    admin_token: str = ""

    # Mesmo segredo HS256 da auth-api — valida o Bearer do caminho ABAC.
    jwt_secret:  str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
