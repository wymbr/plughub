"""
sip_trunk_watch.py — todo número `voice` cadastrado tem tronco SIP no SFU que o aceite (VOZ-41).

O tronco e a regra de despacho vivem no Redis do SFU, que NÃO persiste (`--save ""`): só existem
enquanto o job `sip-seed` os recriar a cada subida. Medido em 2026-09-21: a stack subiu, o seed
achou `infra/sip` vazio, disse "nada a semear" e saiu 0 — e daí em diante o serviço SIP descartou
TODA chamada (motivo `flood`), sem 4xx ao chamador e sem uma linha no gateway. Nada ficou vermelho;
quem achou foi um probe rodado horas depois.

O seed passou a recusar diretório vazio, mas o SFU pode perder o estado sem que o seed rode de novo.
Esta conferência pergunta ao SFU, a cada `interval_s`, a única coisa que importa: **cada número
`voice` cadastrado no registro tem um tronco de entrada que o aceite?** Tronco sem `numbers` aceita
qualquer número. Sem número cadastrado, não há o que cobrir e nada é dito.

Os três desfechos têm cara própria no log, e nenhum é silêncio:
  * coberto  → INFO na transição (inclusive a volta de um estado ruim);
  * DESCOBERTO → ERROR nomeando os números e o que fazer, repetido a cada hora enquanto durar;
  * não conferido (SFU ou registro fora) → WARNING com o motivo — "não sei" nunca vira "ok".
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger("plughub.channel-gateway.sip-trunk-watch")

INTERVAL_S = 300.0
REPEAT_ERROR_EVERY = 12          # ticks — com o intervalo padrão, uma vez por hora


@dataclass(frozen=True)
class Coverage:
    numbers:   tuple[str, ...]           # números `voice` cadastrados
    trunks:    int                       # troncos de entrada no SFU
    uncovered: tuple[str, ...]           # números sem tronco que os aceite

    @property
    def ok(self) -> bool:
        return not self.uncovered


def coverage(numbers: list[str], trunks: list[tuple[str, list[str]]]) -> Coverage:
    """Pura. Tronco com `numbers` vazio aceita qualquer número (semântica do SFU)."""
    aceita_tudo = any(not nums for _, nums in trunks)
    aceitos = {n for _, nums in trunks for n in nums}
    faltam = [] if aceita_tudo else [n for n in numbers if n not in aceitos]
    return Coverage(tuple(numbers), len(trunks), tuple(faltam))


async def voice_numbers(registry_url: str, tenant_id: str, service_token: str = "") -> list[str]:
    """Os `identifier` dos endpoints `voice` ATIVOS do tenant. Erro propaga."""
    headers = {"x-tenant-id": tenant_id}
    if service_token:
        headers["X-Service-Token"] = service_token
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{registry_url.rstrip('/')}/v1/channel-endpoints",
                        params={"channel": "voice", "active": "true"}, headers=headers)
    r.raise_for_status()
    return sorted({str(e.get("identifier") or "") for e in r.json().get("endpoints", [])} - {""})


class SipTrunkWatch:
    """Guarda o último estado para dizer as TRANSIÇÕES, e repetir o ERROR sem inundar o log."""

    def __init__(self, list_trunks: Callable[[], Awaitable[list[tuple[str, list[str]]]]],
                 list_numbers: Callable[[], Awaitable[list[str]]]) -> None:
        self._list_trunks = list_trunks
        self._list_numbers = list_numbers
        self._last: str | None = None          # "ok" | "uncovered" | "unknown"
        self._repeat = 0

    async def tick(self) -> Coverage | None:
        try:
            numbers = await self._list_numbers()
        except Exception as exc:  # noqa: BLE001 — dito abaixo
            return self._unknown(f"registro de endpoints inalcançável ({type(exc).__name__}: {exc})")
        if not numbers:
            self._transition("ok", "nenhum número `voice` cadastrado — nada a cobrir")
            return Coverage((), 0, ())
        try:
            trunks = await self._list_trunks()
        except Exception as exc:  # noqa: BLE001
            return self._unknown(f"SFU não listou os troncos SIP ({type(exc).__name__}: {exc})")
        cov = coverage(numbers, trunks)
        if cov.ok:
            self._transition("ok", f"{len(cov.numbers)} número(s) `voice` coberto(s) por "
                                   f"{cov.trunks} tronco(s) SIP")
        else:
            self._uncovered(cov)
        return cov

    def _transition(self, state: str, msg: str) -> None:
        if self._last != state:
            logger.info("sip-trunk-watch: %s%s", msg,
                        " (restaurado)" if self._last in ("uncovered", "unknown") else "")
        self._last, self._repeat = state, 0

    def _uncovered(self, cov: Coverage) -> None:
        if self._last != "uncovered" or self._repeat >= REPEAT_ERROR_EVERY:
            logger.error(
                "sip-trunk-watch: %d número(s) `voice` SEM tronco SIP no SFU: %s — o serviço SIP "
                "descarta essas chamadas em silêncio (motivo `flood`, sem 4xx e sem linha aqui). "
                "Troncos no SFU: %d. O Redis do SFU não persiste; recrie com o job `sip-seed` "
                "(`docker compose ... up -d --force-recreate sip-seed`) e confira o log dele.",
                len(cov.uncovered), ", ".join(cov.uncovered), cov.trunks)
            self._repeat = 0
        self._last = "uncovered"
        self._repeat += 1

    def _unknown(self, motivo: str) -> None:
        if self._last != "unknown":
            logger.warning("sip-trunk-watch: cobertura SIP NÃO conferida — %s", motivo)
        self._last = "unknown"
        return None


def trunk_lister(get_provider: Callable[[], Any]):
    """O provider é lido a cada tick (o adapter pode nascer depois da task); ausente = não conferido."""
    async def _trunks():
        prov = get_provider()
        if prov is None:
            raise RuntimeError("plano de mídia indisponível (provider do SFU não construído)")
        return await prov.list_inbound_trunks()
    return _trunks


async def run_sip_trunk_watch(get_provider: Callable[[], Any], registry_url: str, tenant_id: str,
                              service_token: str = "", interval_s: float = INTERVAL_S) -> None:
    """Task de boot. Sem provider (canal WebRTC fechado) não há SFU a perguntar — e isso já está
    dito pelo `provider_unavailable`; aqui só se espera ele existir."""
    watch = SipTrunkWatch(trunk_lister(get_provider),
                          lambda: voice_numbers(registry_url, tenant_id, service_token))
    while True:
        await watch.tick()
        await asyncio.sleep(interval_s)
