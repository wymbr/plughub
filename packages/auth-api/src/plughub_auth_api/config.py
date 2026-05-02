"""
config.py
Settings carregadas de variáveis de ambiente (prefixo PLUGHUB_AUTH_).
"""
from __future__ import annotations

import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PLUGHUB_AUTH_",
        env_file=".env",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql://plughub:plughub@postgres:5432/plughub"

    # JWT
    jwt_secret: str = "changeme_auth_jwt_secret_at_least_32_chars"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60           # 1 hora
    refresh_token_expire_days: int = 7              # 7 dias

    # HTTP server
    port: int = 3200
    host: str = "0.0.0.0"

    # Admin bootstrap
    admin_token: str = ""                           # vazio = sem auth (dev only)
    # Seed: cria este usuário admin na primeira inicialização se não existir
    seed_admin_email: str = "admin@plughub.local"
    seed_admin_password: str = "changeme_admin"
    seed_admin_name: str = "Admin"
    seed_tenant_id: str = "tenant_demo"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
