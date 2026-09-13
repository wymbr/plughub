"""
identity/region.py — o país padrão do telefone, por tenant (IDN-14, 2026-09-13).

Um número digitado sem código do país (`11 99999-0001`) só vira E.164 se alguém
disser DE QUE PAÍS ele é. Essa resposta é config de negócio do tenant — config-api,
namespace `identity`, chave `default_phone_region` (ISO 3166-1 alfa-2, ex.: `BR`) —,
nunca uma constante no código.

Duas peças:

  - `PhoneRegionConfig`: cache por tenant da chave no config-api, invalidado pelo
    `config.changed` do namespace `identity`. Sem valor (404) ou com o config-api
    fora do ar, a resposta é `None` — e o aviso diz O QUE deixa de valer: telefone
    sem código do país passa a ser RECUSADO. Nunca se adivinha o país.
  - `anchor_hash`: a ÚNICA função que transforma (tenant, âncora) em hash. Índice e
    OTP a usam; se cada um resolvesse a região por conta própria, o desafio e o
    cadastro podiam discordar do hash da mesma âncora sem ficar vermelho.
"""
from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable, Union

import httpx
import phonenumbers

from .normalize import hash_anchor

logger = logging.getLogger("plughub.channel-gateway.identity.region")

NAMESPACE = "identity"
KEY = "default_phone_region"

# Fonte da região: um valor fixo (testes, probes) ou um resolvedor por tenant.
PhoneRegion = Union[str, None, Callable[[str], Awaitable["str | None"]]]


class PhoneRegionConfig:
    """Resolve `identity.default_phone_region` por tenant, com cache.

    Uma resposta definitiva (valor, ou 404 = não configurado) fica em cache até o
    `config.changed`; uma falha de leitura é re-tentada depois de `retry_s`, e
    enquanto isso vale o último valor bom, se houver.
    """

    def __init__(self, config_api_url: str, retry_s: float = 30.0) -> None:
        self._url = config_api_url.rstrip("/")
        self._retry_s = retry_s
        # tenant → (valor, definitivo, instante)
        self._cache: dict[str, tuple[str | None, bool, float]] = {}

    def invalidate(self, tenant_id: str | None = None) -> None:
        # O config-api publica a mudança do default GLOBAL com tenant_id="__global__":
        # ela vale para todo tenant sem override, então limpa tudo — apagar só a
        # entrada "__global__" deixaria cada tenant com o país antigo até o boot.
        if tenant_id and tenant_id != "__global__":
            self._cache.pop(tenant_id, None)
        else:
            self._cache.clear()

    async def __call__(self, tenant_id: str) -> str | None:
        ent = self._cache.get(tenant_id)
        agora = time.monotonic()
        if ent and (ent[1] or agora - ent[2] < self._retry_s):
            return ent[0]
        anterior = ent[0] if ent else None
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._url}/config/{NAMESPACE}/{KEY}",
                                        params={"tenant_id": tenant_id})
        except Exception as exc:
            logger.warning(
                "identity: nao consegui ler %s.%s do config-api (%s) — %s",
                NAMESPACE, KEY, exc,
                "segue o ultimo valor (%s)" % anterior if anterior
                else "telefone SEM codigo do pais sera RECUSADO ate a leitura voltar",
            )
            self._cache[tenant_id] = (anterior, False, agora)
            return anterior
        if resp.status_code == 404:
            logger.warning(
                "identity: %s.%s nao configurado para tenant=%s — telefone SEM codigo do "
                "pais sera RECUSADO (IDN-14)", NAMESPACE, KEY, tenant_id,
            )
            self._cache[tenant_id] = (None, True, agora)
            return None
        if resp.status_code != 200:
            logger.warning("identity: config-api respondeu HTTP %d para %s.%s — %s",
                           resp.status_code, NAMESPACE, KEY,
                           "segue o ultimo valor" if anterior else "telefone sem DDI sera recusado")
            self._cache[tenant_id] = (anterior, False, agora)
            return anterior
        valor = resp.json().get("value")
        regiao = valor.strip().upper() if isinstance(valor, str) else None
        if regiao not in phonenumbers.SUPPORTED_REGIONS:
            logger.error(
                "identity: %s.%s=%r nao e uma regiao ISO 3166 conhecida (tenant=%s) — "
                "telefone SEM codigo do pais sera RECUSADO", NAMESPACE, KEY, valor, tenant_id,
            )
            regiao = None
        self._cache[tenant_id] = (regiao, True, agora)
        return regiao


async def resolve_region(source: PhoneRegion, tenant_id: str) -> str | None:
    if callable(source):
        return await source(tenant_id)
    return source


async def anchor_hash(salt: str, source: PhoneRegion, tenant_id: str, kind: str, value: str) -> str:
    """Hash da âncora para o tenant. Levanta ValueError se a âncora é inválida.

    A região só é consultada para telefone — para os outros kinds ela não entra.
    A recusa de telefone por falta de região LOGA: o chamador (o resolve) descarta
    âncora inválida sem dizer nada, e esta é a única casa que sabe o porquê.
    """
    regiao = await resolve_region(source, tenant_id) if kind == "phone" else None
    try:
        return hash_anchor(salt, kind, value, regiao)
    except ValueError as exc:
        if kind == "phone":
            logger.warning("identity: ancora phone recusada tenant=%s regiao=%s — %s",
                           tenant_id, regiao, exc)
        raise
