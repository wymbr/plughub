# -*- coding: utf-8 -*-
"""O funil de escrita Python — ALW-02 passo 3. Par de `writeContextTag` (TS).

── O contrato é ESTREITO, e o estreitamento é medido ────────────────────────────

Este funil escreve **no hash da SESSÃO, e só nele**. O gêmeo TS também roteia `journey.*`
para o hash do processo (raiz canônica via union-find sobre `{t}:journey:aliases`, TTL 30 d);
esta metade **recusa** a tag de escopo não-sessão em vez de a rotear.

Não é omissão, é escolha com número atrás:

  · **19 dos 20 sítios Python escrevem tags FIXAS e conhecidas**, todas de sessão
    (`core.contact.*`, `core.survey.*`, `core.queue.*`, `core.pool.*`, `core.sentiment.*`,
    `core.copilot.*`, `segment.*`, `core.process.outcome`);
  · **um único sítio escreve tag ARBITRÁRIA** — o `set_context` do `mention_command`, no
    bridge —, e o censo dos YAML achou **uma** declaração viva: `session.copilot.mode`.
    Escopo de sessão. A exposição a journey vindo do Python é **zero hoje**.

Portar o union-find para cá seria mirror de I/O para uma população vazia, e mirror de I/O
é justamente o que o gate de paridade **não** consegue comparar.

**Por que RECUSAR e não cair no hash da sessão:** cair ali perderia a tag em 4 h sem erro em
lugar nenhum — o defeito exato que o `writeContextTag` do TS existe para impedir, e que a
ALW-02 acabou de consertar no `bpm.ts` (a OUTRA casa do mesmo `mention_command`). Ausência
com log se diagnostica; hash errado que expira, não. O caminho para quem precisar é a tool
`context_set` do mcp-server, que roteia.

── O que ele faz, então ─────────────────────────────────────────────────────────

Carimba o `atributo` (D9.6) e grava. O escritor não declara tipo nenhum — não tem o que
errar —, e o dado guardado fica autodescritivo para o snapshot durável e para export LGPD.

`atributo` AUSENTE numa entrada significa **não passou pelo funil**, e é assim que se mede se
o choke point tem furo.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Mapping

from . import resolve_context_store, stamp_context_entry
from .loader import get_context_map

__all__ = ["ContextScopeRefused", "write_context_tags"]

logger = logging.getLogger(__name__)


class ContextScopeRefused(Exception):
    """Tag de escopo não-sessão oferecida ao funil Python. Ver o cabeçalho do módulo."""


async def write_context_tags(
    redis: Any,
    tenant_id: str,
    session_id: str,
    tags: Mapping[str, Any],
    *,
    fetch_json: Callable[[str], Awaitable[Any]],
    source: str,
    confidence: float = 1.0,
    visibility: Any = "agents_only",
    updated_at: str,
    ttl_s: int | None = None,
) -> dict[str, Any]:
    """Grava N tags no hash da sessão, cada uma CARIMBADA.

    Recebe `tags` como mapa **valor cru → tag**, e monta a entrada aqui dentro: é o mesmo
    motivo pelo qual o gêmeo TS passou a receber o objeto em vez do JSON já serializado —
    com o chamador montando o JSON, ele podia serializar por fora e passar ao largo do
    carimbo, mudo. *Remover a alternativa custa menos que lembrar de não usá-la.*

    `redis` e `fetch_json` são injetados (duck-typing): o pacote não pode ter dependências,
    e os cinco serviços não usam o mesmo cliente HTTP — `orchestrator-bridge` usa `aiohttp`,
    os outros `httpx`.

    Devolve `{"written": [tag…], "fallback": bool, "atributos": {tag: carimbo}}`.
    Levanta `ContextScopeRefused` se alguma tag não for de escopo sessão — **antes** de
    escrever qualquer uma, para não deixar a chamada meio aplicada.
    """
    fora = [t for t in tags if resolve_context_store(t) != "session"]
    if fora:
        raise ContextScopeRefused(
            f"tags de escopo nao-sessao oferecidas ao funil Python: {sorted(fora)} — "
            f"esta metade so escreve no hash da sessao (ver o cabecalho de writer.py); "
            f"use a tool `context_set` do mcp-server, que roteia"
        )
    if not tags:
        return {"written": [], "fallback": False, "atributos": {}}

    index, fallback = await get_context_map(tenant_id, fetch_json)

    mapping: dict[str, str] = {}
    atributos: dict[str, Any] = {}
    for tag, value in tags.items():
        carimbada = stamp_context_entry(
            {
                "value":      value,
                "confidence": confidence,
                "source":     source,
                "visibility": visibility,
                "updated_at": updated_at,
            },
            tag, index, fallback,
        )
        mapping[tag] = json.dumps(carimbada)
        atributos[tag] = carimbada["atributo"]

    await redis.hset(f"{tenant_id}:ctx:{session_id}", mapping=mapping)
    if ttl_s is not None:
        await redis.expire(f"{tenant_id}:ctx:{session_id}", ttl_s)

    return {"written": list(mapping), "fallback": fallback, "atributos": atributos}
