"""
permissions.py
DDL e CRUD de `auth.permission_templates` — o PRESET de cadastro de usuário.

── O que este módulo deixou de ser (2026-08-30) ──────────────────────────────

Até aqui ele também mantinha `auth.platform_permissions`: uma matriz plana
`{module, action, scope_type, scope_id}` por usuário, com CRUD, um resolvedor
(`resolve_permissions`), um derivador de escopo (`get_accessible_pools_for_module`)
e um `apply_template` que MATERIALIZAVA o template naquela tabela.

Tudo isso saiu, e o motivo não é estético — é que a decisão real vive noutro lugar.
Quem responde *"esta pessoa pode?"* é `auth.users.module_config`, lido pelo
verificador canônico `plughub_authz`. A matriz paralela tinha:

  · **zero linhas** nas duas tabelas (medido em 2026-08-30, base viva);
  · **zero consumidores de produção** — nenhuma UI e nenhum serviço chamava
    `/auth/permissions/*` nem `/templates/{id}/apply`; os únicos chamadores eram
    os testes;
  · e **duas leituras para o mesmo objeto**: a tela de Acesso usa
    `template.config` como preset copiado no cliente, enquanto o `apply`
    escrevia numa tabela que ninguém consultava.

Duas respostas para *"quais permissões esta pessoa tem?"* significam que a mais
permissiva vale — o mesmo modo de falha que a V2b removeu do leitor legado de
masking, e que o arco do ABAC TOTAL removeu do menu. Manter um endpoint que
PARECE conceder permissão e não concede é pior que não tê-lo.

⚠️ **Ficou um resíduo físico, de propósito.** A tabela `auth.platform_permissions`
(e a coluna legada `permissions` de `permission_templates`) continuam existindo em
bases já criadas: este pacote usa DDL idempotente, não migração versionada, então
não há caminho para DROP sem escrever um. São órfãs — nada as cria em instalação
nova, nada as lê, e estão vazias. Dropá-las é item de migração, não efeito
colateral de uma remoção de código.

── O que FICA, e por quê ─────────────────────────────────────────────────────

`permission_templates` tem consumidor VIVO: a tela de Acesso
(`platform-ui/.../AccessPage.tsx`) lista, cria, edita e apaga templates, e usa
`config` — `{role, module_config, accessible_pools, max_concurrent_sessions}` —
para PRÉ-PREENCHER o formulário de usuário. É cópia no cliente: não há vínculo
vivo nem propagação, e é assim de propósito.

⚠️ São DOIS mecanismos de preset escrevendo `module_config`, e isso é aceito:
`role_defaults` (servidor, automático no `create_user`) e o template (cliente,
cópia manual). Não competem — gatilhos diferentes —, mas só o primeiro é o
DEFAULT. Um terceiro seria demais.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import asyncpg

logger = logging.getLogger("plughub.auth_api.permissions")


# ─── DDL ──────────────────────────────────────────────────────────────────────

DDL_TEMPLATES = """
CREATE TABLE IF NOT EXISTS auth.permission_templates (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT        NOT NULL,
    name        TEXT        NOT NULL,
    description TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
)
"""


async def ensure_permissions_schema(pool: asyncpg.Pool) -> None:
    """
    Cria/atualiza `auth.permission_templates`. O nome da função é mantido porque
    `main.py` a chama no boot; o que ela garante encolheu junto com o módulo.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(DDL_TEMPLATES)
            # Fase 1 (presets copy-on-create): o template guarda um SNAPSHOT rico do
            # cadastro de usuário (role + module_config ABAC + accessible_pools +
            # max_concurrent_sessions), não a matriz plana {module,action} legada.
            await conn.execute(
                "ALTER TABLE auth.permission_templates "
                "ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}'"
            )
    logger.info("permission_templates schema ensured")


# ─── permission_templates CRUD ────────────────────────────────────────────────

async def create_template(
    pool: asyncpg.Pool,
    tenant_id: str,
    name: str,
    description: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    # `config` = preset rico {role, module_config, accessible_pools,
    # max_concurrent_sessions}.
    row = await pool.fetchrow(
        """
        INSERT INTO auth.permission_templates (tenant_id, name, description, config)
        VALUES ($1, $2, $3, $4::jsonb)
        RETURNING *
        """,
        tenant_id, name, description, json.dumps(config),
    )
    return dict(row)


async def get_template(pool: asyncpg.Pool, template_id: str) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        "SELECT * FROM auth.permission_templates WHERE id = $1",
        uuid.UUID(template_id),
    )
    return dict(row) if row else None


async def list_templates(
    pool: asyncpg.Pool,
    tenant_id: str,
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "SELECT * FROM auth.permission_templates WHERE tenant_id = $1 ORDER BY name",
        tenant_id,
    )
    return [dict(r) for r in rows]


async def update_template(
    pool: asyncpg.Pool,
    template_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    sets = []
    params: list[Any] = []
    i = 1

    if name is not None:
        sets.append(f"name = ${i}"); params.append(name); i += 1
    if description is not None:
        sets.append(f"description = ${i}"); params.append(description); i += 1
    if config is not None:
        sets.append(f"config = ${i}::jsonb"); params.append(json.dumps(config)); i += 1

    if not sets:
        return await get_template(pool, template_id)

    sets.append("updated_at = now()")
    params.append(uuid.UUID(template_id))

    row = await pool.fetchrow(
        f"UPDATE auth.permission_templates SET {', '.join(sets)} WHERE id = ${i} RETURNING *",
        *params,
    )
    return dict(row) if row else None


async def delete_template(pool: asyncpg.Pool, template_id: str) -> bool:
    result = await pool.execute(
        "DELETE FROM auth.permission_templates WHERE id = $1",
        uuid.UUID(template_id),
    )
    return result.endswith("1")
