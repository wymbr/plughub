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



def _avisar_nao_cadastradas(
    tenant_id: str,
    session_id: str,
    atributos: dict,
    mapa_e_fallback: bool,
) -> None:
    """Metade RUNTIME da D9.1: nunca rejeita, mas **loga nomeando**.

    ── Por que `unknown` e `fallback` NAO podem sair na mesma mensagem ──────────

    `origem: "unknown"` significa *"esta tag nao esta no mapa"*. Mas se o mapa em uso e o
    **fallback embutido** — porque o config-api nao respondeu —, entao `unknown` nao e
    evidencia de cadastro faltando: e evidencia de que o mapa nao carregou, e a tag pode
    estar perfeitamente cadastrada no mapa vivo.

    Mandar as duas pela mesma frase manda alguem cadastrar o que ja existe. Medido em
    2026-09-02 na forma exata: o censo lia o mapa SEMENTE e publicava 18 nao-declaradas
    onde a resposta era 2. Aqui a assimetria e a mesma, e a mensagem a separa.

    `dynamic` NAO entra: `agent.*`/`segment.*` sao familias declaradas como abertas, e
    avisar sobre elas ensinaria a ignorar o aviso.
    """
    nao_cadastradas = sorted(
        t for t, a in atributos.items()
        if isinstance(a, dict) and a.get("origem") == "unknown"
    )
    if not nao_cadastradas:
        return
    if mapa_e_fallback:
        logger.warning(
            "[ctx-writer] tenant=%s session=%s: %d tag(s) sem correspondencia no mapa, "
            "MAS o mapa em uso e o FALLBACK EMBUTIDO — o config-api nao respondeu. NAO "
            "conclua que falta cadastro: estas tags podem estar no mapa vivo. Conserte o "
            "transporte antes de cadastrar qualquer coisa. Tags: %s",
            tenant_id, session_id, len(nao_cadastradas), nao_cadastradas,
        )
        return
    logger.warning(
        "[ctx-writer] tenant=%s session=%s: %d tag(s) NAO CADASTRADAS no mapa do "
        "ContextStore foram gravadas mesmo assim (o runtime nunca rejeita — D9.1). Elas "
        "resolvem para o mais RESTRITIVO na leitura, entao um operador legitimo pode nao "
        "ver o valor. Cadastre em /config/context-map. Tags: %s",
        tenant_id, session_id, len(nao_cadastradas), nao_cadastradas,
    )

async def write_context_tags(
    redis: Any,
    tenant_id: str,
    session_id: str,
    tags: Mapping[str, Any],
    *,
    fetch_json: Callable[[str], Awaitable[Any]] | None = None,
    source: str,
    confidence: float = 1.0,
    visibility: Any = "agents_only",
    updated_at: str,
    ttl_s: int | None = None,
    ttl_nx: bool = False,
    on_foreign_scope: str = "raise",
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

    `on_foreign_scope` decide o que fazer com tag de escopo não-sessão, e as **duas**
    posturas existem porque há duas espécies de chamador:

      · `"raise"` (default) — os 19 sítios que escrevem tags FIXAS e conhecidas. Se uma
        delas virar `journey.*`, é bug de quem escreveu o código, e a falha tem de ser alta.
        A verificação roda sobre o conjunto INTEIRO antes do primeiro `hset`: chamada meio
        aplicada é pior que recusada.
      · `"warn"` — o sítio que escreve tag ARBITRÁRIA (corpo de webhook, `context` de
        `delegate`/`collect`). Ali a tag vem de fora, recusar mudaria o comportamento de
        chamador externo, e hoje ela É gravada no hash da sessão. Este ramo preserva isso e
        **diz que está errado** em vez de fingir que não. Dívida: ALW-03.
    """
    fora = [t for t in tags if resolve_context_store(t) != "session"]
    if fora and on_foreign_scope == "raise":
        raise ContextScopeRefused(
            f"tags de escopo nao-sessao oferecidas ao funil Python: {sorted(fora)} — "
            f"esta metade so escreve no hash da sessao (ver o cabecalho de writer.py); "
            f"use a tool `context_set` do mcp-server, que roteia"
        )
    if fora:
        logger.warning(
            "[ctx-writer] tenant=%s session=%s: tags de escopo NAO-SESSAO gravadas no "
            "hash da sessao mesmo assim: %s. O valor fica legivel, mas no lugar errado — "
            "uma tag `journey.*` pertence ao hash do PROCESSO (TTL 30d) e sera perdida "
            "quando a sessao expirar. Este ramo existe para NAO mudar o comportamento de "
            "chamador externo (corpo de webhook e arbitrario); ver ALW-03.",
            tenant_id, session_id, sorted(fora),
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

    _avisar_nao_cadastradas(tenant_id, session_id, atributos, fallback)

    await redis.hset(f"{tenant_id}:ctx:{session_id}", mapping=mapping)
    if ttl_s is not None:
        # `nx=True` = nunca ENCURTAR um TTL que outro componente já pôs. O routing-engine
        # depende disso (reconexão não pode reiniciar a vida da sessão), e é semântica de
        # política, não detalhe — por isso viaja no funil em vez de o chamador repetir.
        if ttl_nx:
            await redis.expire(f"{tenant_id}:ctx:{session_id}", ttl_s, nx=True)
        else:
            await redis.expire(f"{tenant_id}:ctx:{session_id}", ttl_s)

    return {"written": list(mapping), "fallback": fallback, "atributos": atributos}
