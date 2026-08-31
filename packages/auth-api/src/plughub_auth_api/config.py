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
    # AUT-12 (2026-08-31): `roles` era o UNICO campo do admin semeado fora da config —
    # e-mail, senha e nome ja eram configuraveis. Nao era decisao, era residuo: quem
    # instala podia trocar a identidade do primeiro usuario, mas nao a capacidade dele.
    #
    # Default passou de `admin,developer` para `admin` SO (decisao do dono). Medido antes
    # de trocar: ZERO campos do catalogo concedem a `developer` sem conceder tambem a
    # `admin`, e os dois gates de papel que restam na UI (Dashboards, AgentFlow Deploy)
    # aceitam `admin`. Ou seja, nada se perde — e `developer` numa instalacao de producao
    # era grant que ninguem pediu.
    #
    # String separada por virgula, nao lista: e a convencao de env do repositorio
    # (`PLUGHUB_ANTHROPIC_API_KEYS=sk-1,sk-2`). Pydantic leria `list[str]` como JSON, o
    # que faria `PLUGHUB_AUTH_SEED_ADMIN_ROLES=admin` estourar em vez de funcionar.
    seed_admin_roles: str = "admin"
    seed_tenant_id: str = "tenant_demo"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


# Papeis validos — espelha o `Role` de `models.py`. Importar de la criaria ciclo
# (models -> config), entao a conferencia acontece no consumidor, com a lista a mao.
_ROLES_VALIDOS = frozenset({"operator", "supervisor", "admin", "developer", "business"})


def seed_admin_roles() -> list[str]:
    """Papeis do admin semeado, conferidos.

    ⚠️ RECUSA ALTO em papel desconhecido, e a razao e concreta: `create_user` aplica o
    PRESET de `module_config` por papel. Um papel que nao existe no catalogo nao casa com
    preset nenhum, entao o admin nasceria com `module_config` VAZIO — e sob o portao
    grant-first (2026-08-27) isso significa **admin cego**, sem menu, numa instalacao
    nova. O erro apareceria como "a plataforma nao abre", a quilometros da causa.

    Vazio tambem recusa: um seed sem papel produz o mesmo admin cego.
    """
    bruto = [r.strip() for r in get_settings().seed_admin_roles.split(",")]
    papeis = [r for r in bruto if r]
    if not papeis:
        raise ValueError(
            "PLUGHUB_AUTH_SEED_ADMIN_ROLES esta vazio — o admin semeado nasceria sem "
            "preset de modulo e, sob o portao grant-first, sem menu nenhum."
        )
    invalidos = [r for r in papeis if r not in _ROLES_VALIDOS]
    if invalidos:
        raise ValueError(
            f"PLUGHUB_AUTH_SEED_ADMIN_ROLES traz papel desconhecido: {invalidos}. "
            f"Validos: {sorted(_ROLES_VALIDOS)}. Papel fora do catalogo nao casa com "
            f"preset algum e o admin semeado nasceria cego."
        )
    return papeis
