# -*- coding: utf-8 -*-
"""Carregador do mapa — a metade com I/O. Espelha `getContextMap` do mcp-server.

── Por que o TRANSPORTE é injetado ──────────────────────────────────────────────

Duas razões, e a segunda é medição:

1. **O pacote não pode ter dependências.** É requisito do gate de paridade
   (`infra/test/probe_context_stamp_parity.sh`), que roda as duas metades lado a lado — um
   cliente HTTP aqui dentro tornaria a metade Python incomparável com a TS.
2. **Os cinco serviços não usam o mesmo cliente.** Medido em 2026-09-02:
   `orchestrator-bridge` usa `aiohttp`; `channel-gateway`, `routing-engine`, `ai-gateway` e
   `evaluation-api` usam `httpx`. Fixar um deles obrigaria o bridge a carregar um segundo
   cliente HTTP por causa de uma leitura de config.

Então a divisão é: **a lib é dona do que não pode ser copiado cinco vezes** (a URL, o
recorte do corpo, a validação, o cache, o fallback e o AVISO); **o serviço é dono do
transporte**, que ele já tem.

── O fallback é o motivo de `default_map.py` existir ────────────────────────────

Se esta metade não tivesse mapa embutido, uma queda do config-api faria o Python carimbar
`{origem: "unknown", fallback: true}` onde o TS carimba
`{tipo: "cpf_br", origem: "canonical", fallback: true}`. Pior que qualquer das duas
sozinha: `unknown` é a população que a V4 conta, e o ruído de indisponibilidade cairia
dentro do número que autoriza inverter o default.

── E o aviso NOMEIA o que deixa de valer ────────────────────────────────────────

Não é `logger.warning("using default values")`. Essa frase genérica existiu no bridge e
ninguém a leu por meses (`CLAUDE.md` § Configuration). Aqui o texto diz **quais** fatos
deixam de ser verdadeiros enquanto o fallback durar.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Awaitable, Callable, Mapping

from . import ContextTagIndex, build_context_tag_index
from .default_map import DEFAULT_CONTEXT_MAP

__all__ = [
    "CONTEXT_MAP_CACHE_TTL_S",
    "config_api_base",
    "context_map_url",
    "get_context_map",
    "get_masking_catalog",
    "invalidate_context_map_cache",
    "set_context_map_fetcher",
]

logger = logging.getLogger(__name__)

#: Mesmo TTL do gêmeo TS. Vive aqui, e não capturado no import de cada serviço, porque
#: TTL de recurso compartilhado com dois donos acaba valendo o do escritor mais frequente
#: — foi assim que o conserto de `{t}:pool_config:{p}` ficou desfeito por meses.
CONTEXT_MAP_CACHE_TTL_S = 60.0

#: tenant → (index, fallback, expira_em)
_cache: dict[str, tuple[ContextTagIndex, bool, float]] = {}

#: tenant → (catalogo, expira_em). Cache SEPARADO do mapa, sobre a MESMA URL — o
#: `/config/masking` devolve o namespace inteiro, então em tese caberia um cache só.
#: Ficaram dois porque o do mapa tem semântica de FALLBACK (mapa embutido) que o
#: catálogo não tem, e fundir os dois faria o fallback de um decidir pelo outro. O custo
#: é uma requisição a mais por tenant por minuto.
_cache_catalogo: dict[str, tuple[dict[str, Any], float]] = {}

#: Transporte registrado pelo serviço no boot. Existe porque os escritores do ContextStore
#: são fire-and-forget e não têm o cliente HTTP à mão — enfiá-lo por dez assinaturas seria
#: pior que um registro único, e um default de stdlib (`urllib`) poria I/O BLOQUEANTE num
#: caminho async quente.
_fetcher: Callable[[str], Awaitable[Any]] | None = None


def set_context_map_fetcher(fetch_json: Callable[[str], Awaitable[Any]] | None) -> None:
    """Registra o transporte, uma vez, no boot do serviço.

    Não registrar não quebra nada: `get_context_map` cai no mapa embutido e AVISA nomeando
    a causa. Bounded e diagnosticável — mas é degradação, não regime normal.
    """
    global _fetcher
    _fetcher = fetch_json


#: Índice do mapa EMBUTIDO, construído uma vez. Se este `build` levantar, o pacote está
#: quebrado na origem e é melhor descobrir no import do que numa queda do config-api.
_FALLBACK_INDEX: ContextTagIndex = build_context_tag_index(DEFAULT_CONTEXT_MAP)


#: Os DOIS nomes que o compose usa para o mesmo fato. Medido em 2026-09-02:
#: `PLUGHUB_CONFIG_API_URL` em routing-engine, ai-gateway e channel-gateway;
#: `CONFIG_API_URL` no orchestrator-bridge; NENHUM na evaluation-api.
#:
#: Ler só um deles fez **quatro dos cinco** caírem no default `localhost:3600` — que dentro
#: do container é o próprio container —, e com isso TODO carimbo escrito desde a ALW-02
#: saiu com `atributo.fallback: true`. Ficou invisível porque o mapa tem fallback embutido:
#: as escritas funcionavam, com o tipo do CÓDIGO em vez do tipo do TENANT.
#:
#: ⚠️ Ler os dois é remendo, e está declarado como tal: a dívida é o compose ter duas
#: convenções (a mesma do `JWT_SECRET` × `PLUGHUB_JWT_SECRET` da CAP-12). O específico
#: vence o genérico.
_ENV_NAMES = ("PLUGHUB_CONFIG_API_URL", "CONFIG_API_URL")


def config_api_base() -> str:
    """Base do config-api. Default `:3600` — e o número importa: o `CLAUDE.md` registra um
    caso em que o default hardcoded apontava para `:3500` (analytics-api) e o namespace
    inteiro ficou inerte, degradando para "usa o default" sem nada ficar vermelho."""
    for nome in _ENV_NAMES:
        v = os.environ.get(nome)
        if v:
            return v.rstrip("/")
    return "http://localhost:3600"


def context_map_url(tenant_id: str) -> str:
    """⚠️ `?tenant_id=` é OBRIGATÓRIO — sem ele o config-api devolve **422**, e essa foi
    uma das três causas empilhadas que deixaram o namespace `session` do bridge inerte."""
    from urllib.parse import quote
    return f"{config_api_base()}/config/masking?tenant_id={quote(tenant_id, safe='')}"


def invalidate_context_map_cache(tenant_id: str | None = None) -> None:
    """Descarta o cache. Sem argumento, descarta tudo — é o que o consumidor de
    `config.changed` deve chamar quando não souber o tenant afetado."""
    if tenant_id is None:
        _cache.clear()
        _cache_catalogo.clear()
    else:
        _cache.pop(tenant_id, None)
        _cache_catalogo.pop(tenant_id, None)


async def get_masking_catalog(
    tenant_id: str,
    fetch_json: Callable[[str], Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    """Catálogo de tipos (`masking.types`), indexado por id. Cache de 60 s.

    Devolve `{}` quando não consegue carregar — e o `{}` é HONESTO, não um default: quem
    consome (hoje o preview de pendência) trata tipo ausente como `full`, isto é, mascara
    tudo. Um catálogo vazio faz o consumidor esconder, nunca revelar.

    ⚠️ Não há mapa embutido de fallback aqui, ao contrário de `get_context_map`. É
    deliberado: o catálogo declara POLÍTICA (o que cada tipo esconde), e um espelho
    embutido que envelhecesse aplicaria política velha achando que é a do tenant. Melhor
    esconder por não saber do que revelar por lembrar errado.
    """
    agora = time.monotonic()
    em_cache = _cache_catalogo.get(tenant_id)
    if em_cache is not None and em_cache[1] > agora:
        return em_cache[0]

    catalogo: dict[str, Any] = {}
    transporte = fetch_json or _fetcher
    try:
        if transporte is None:
            raise RuntimeError(
                "nenhum transporte registrado — chame set_context_map_fetcher() no boot"
            )
        body = await transporte(context_map_url(tenant_id))
        if not isinstance(body, Mapping):
            raise ValueError("corpo da resposta não é objeto")
        entries = body.get("entries")
        if not isinstance(entries, Mapping):
            entries = body
        raw = entries.get("types")
        if isinstance(raw, Mapping) and "value" in raw:
            raw = raw["value"]
        tipos = (raw or {}).get("types") if isinstance(raw, Mapping) else None
        if not isinstance(tipos, list):
            raise ValueError("chave masking.types ausente ou sem lista `types`")
        catalogo = {t["id"]: t for t in tipos if isinstance(t, Mapping) and t.get("id")}
        if not catalogo:
            raise ValueError("catálogo vazio")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[masking-catalog] tenant=%s catálogo INDISPONÍVEL (%s). Deixa de valer: quem "
            "resolve máscara por tipo passa a tratar TODO tipo como desconhecido, e o "
            "desfecho conservador é esconder — o preview de pendência sai vazio. Não há "
            "espelho embutido de propósito: política velha aplicada como se fosse a do "
            "tenant é pior que ausência declarada.",
            tenant_id, exc,
        )

    _cache_catalogo[tenant_id] = (catalogo, agora + CONTEXT_MAP_CACHE_TTL_S)
    return catalogo


def _extract(body: Any) -> Any:
    """Recorta `entries.context_map.value` do corpo. Mesmo recorte do gêmeo TS: a rota
    devolve `{entries: {chave: {value: ...}}}`, mas tolera tanto o envelope quanto o valor
    direto, porque as duas formas já apareceram."""
    if not isinstance(body, Mapping):
        raise ValueError("corpo da resposta não é objeto")
    entries = body.get("entries")
    if not isinstance(entries, Mapping):
        entries = body
    raw = entries.get("context_map")
    if isinstance(raw, Mapping) and "value" in raw:
        raw = raw["value"]
    if raw is None:
        raise ValueError("chave masking.context_map ausente")
    return raw


async def get_context_map(
    tenant_id: str,
    fetch_json: Callable[[str], Awaitable[Any]] | None = None,
) -> tuple[ContextTagIndex, bool]:
    """Índice do mapa do tenant, com cache de 60 s. Devolve `(index, fallback)`.

    `fetch_json` recebe a URL e devolve o corpo JÁ desserializado; qualquer falha dele
    (rede, status, timeout) deve levantar — é o que dispara o fallback. **Este carregador
    nunca levanta**: uma escrita no ContextStore não pode ficar refém do config-api.

    ⚠️ O `build_context_tag_index` é ESTRITO e levanta em mapa malformado. É de propósito, e
    é o que faz esta metade se comportar como o `safeParse` do Zod no lado TS: mapa torto
    não vira índice pela metade, vira fallback declarado. Tolerar aqui carimbaria a partir
    de meio mapa afirmando `fallback: false`.
    """
    agora = time.monotonic()
    em_cache = _cache.get(tenant_id)
    if em_cache is not None and em_cache[2] > agora:
        return em_cache[0], em_cache[1]

    index, fallback = _FALLBACK_INDEX, True
    transporte = fetch_json or _fetcher
    try:
        if transporte is None:
            raise RuntimeError(
                "nenhum transporte registrado — chame set_context_map_fetcher() no boot"
            )
        index = build_context_tag_index(_extract(await transporte(context_map_url(tenant_id))))
        fallback = False
    except Exception as exc:  # noqa: BLE001 — qualquer falha cai no mesmo desfecho
        logger.warning(
            "[context-map] tenant=%s usando o mapa EMBUTIDO (%s). Deixa de valer: "
            "(1) o CARIMBO do atributo sai deste mapa, então toda entrada escrita nesta "
            "janela leva `atributo.fallback: true` — o tipo gravado é o que o código "
            "trouxe, não o que o tenant declarou; (2) alias e campos declarados SÓ na "
            "config do tenant não são reconhecidos e saem como `unknown`, que é a "
            "população que a V4 conta. Nenhuma máscara muda: o carimbo não é lido por "
            "política hoje.",
            tenant_id, exc,
        )

    _cache[tenant_id] = (index, fallback, agora + CONTEXT_MAP_CACHE_TTL_S)
    return index, fallback
