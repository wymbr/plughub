"""
speech_config.py — os parâmetros de segmentação da fala, por tenant, do config-api (VOZ-21).

Primeiro passo da recalibragem de STT por instalação (`TODO.md` § *Recalibragem de STT*, ADR
`adr-voice-media-plane.md` V13): limiar de energia, silêncio que fecha a fala, lacuna sem quadro,
fala mínima, fala máxima e o VAD do serviço eram constantes no construtor do provedor — não havia
o que recalibrar, e config de tuning em código é o que a casa proíbe.

Namespace `webrtc` (o canal é o recorte: a perna SIP terá os números dela, G.711 a 8 kHz). Cada
chave tem faixa; o config-api não valida valor, então quem lê valida:

  ausente          → default de código, AVISO nomeando a chave
  tipo/faixa ruim  → default de código, ERRO nomeando a chave e o valor
  config-api fora  → último valor bom se houver, senão o default — AVISO dizendo que a config do
                     tenant NÃO está valendo

A procedência de cada valor (`tenant`, `global`, `default: <motivo>`) acompanha o resultado e vai
ao log quando a chamada abre o fluxo de STT. `collect.voice.end_silence_ms`/`max_speech_s` de um
menu continuam vencendo enquanto a coleta espera (VOZ-18, `SpeechTuning`).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from .adapters.voice_provider import SpeechSegmentation

logger = logging.getLogger("plughub.channel-gateway.speech_config")

NAMESPACE = "webrtc"


@dataclass(frozen=True)
class _Param:
    key:   str
    field: str
    lo:    float | None = None
    hi:    float | None = None


# a ordem e os nomes são o contrato com o seed do config-api (`seed.py`, namespace `webrtc`)
PARAMS: tuple[_Param, ...] = (
    _Param("stt_energy_threshold", "energy_threshold", 50, 5000),
    _Param("stt_end_silence_ms",   "end_silence_ms",   100, 5000),
    _Param("stt_gap_ms",           "gap_ms",           100, 5000),
    _Param("stt_min_speech_ms",    "min_speech_ms",    50, 2000),
    _Param("stt_max_speech_ms",    "max_speech_ms",    1000, 60000),
    _Param("stt_vad_filter",       "vad_filter"),
)


def _valida(p: _Param, valor: object) -> object | None:
    """O valor convertido, ou None se não serve."""
    if p.field == "vad_filter":
        return valor if isinstance(valor, bool) else None
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    if valor != valor or not (p.lo <= valor <= p.hi):          # NaN ou fora da faixa
        return None
    return float(valor) if p.field == "energy_threshold" else int(valor)


def resolve(entries: dict, scopes: dict[str, str] | None, tenant_id: str) -> SpeechSegmentation:
    """Monta a segmentação a partir do namespace resolvido (`GET /config/webrtc`) e dos escopos
    (`GET /config/webrtc/_provenance`); cada chave ausente ou inválida cai no default, dito."""
    base = SpeechSegmentation()
    valores: dict[str, object] = {}
    proc: dict[str, str] = {}
    ausentes: list[str] = []
    for p in PARAMS:
        if p.key not in entries:
            ausentes.append(p.key)
            proc[p.field] = "default: ausente no config-api"
            continue
        v = _valida(p, entries[p.key])
        if v is None:
            logger.error(
                "speech_config: %s.%s=%r invalido para tenant=%s (%s) — vale o default %r",
                NAMESPACE, p.key, entries[p.key], tenant_id,
                "booleano" if p.field == "vad_filter" else f"numero entre {p.lo:g} e {p.hi:g}",
                getattr(base, p.field),
            )
            proc[p.field] = "default: valor invalido"
            continue
        valores[p.field] = v
        proc[p.field] = (scopes or {}).get(p.key, "config")
    if ausentes:
        logger.warning("speech_config: %s nao configurado(s) em %s para tenant=%s — vale o default de codigo",
                       ", ".join(ausentes), NAMESPACE, tenant_id)
    return SpeechSegmentation(**valores, provenance=proc)


class SpeechSegmentationConfig:
    """`SpeechSegmentation` por tenant, com cache. Leitura boa fica até o `config.changed` do
    namespace; falha é re-tentada depois de `retry_s`, valendo enquanto isso o último valor bom."""

    def __init__(self, config_api_url: str, retry_s: float = 30.0) -> None:
        self._url = config_api_url.rstrip("/")
        self._retry_s = retry_s
        # tenant → (segmentação, definitiva, instante, veio de uma leitura BOA)
        self._cache: dict[str, tuple[SpeechSegmentation, bool, float, bool]] = {}

    def invalidate(self, tenant_id: str | None = None) -> None:
        """Vence a entrada sem apagá-la: se a releitura falhar, o último valor BOM continua valendo
        — apagar faria uma mudança na tela seguida de config-api fora cair no default de código.
        Mudança do default GLOBAL chega com tenant_id="__global__" e vale para todos."""
        alvos = [tenant_id] if tenant_id and tenant_id != "__global__" else list(self._cache)
        vencidas = 0
        for t in alvos:
            if t in self._cache:
                seg, _, _, bom = self._cache[t]
                self._cache[t] = (seg, False, float("-inf"), bom)
                vencidas += 1
        # o log mora AQUI, não no chamador: um log no consumidor do evento afirmaria a invalidação
        # mesmo sem ela (medido 2026-09-17, mutação LM8)
        logger.info("speech_config: segmentacao da fala invalidada (tenant=%s, %d entrada(s) em cache)",
                    tenant_id or "__global__", vencidas)

    async def __call__(self, tenant_id: str) -> SpeechSegmentation:
        ent = self._cache.get(tenant_id)
        agora = time.monotonic()
        if ent and (ent[1] or agora - ent[2] < self._retry_s):
            return ent[0]
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._url}/config/{NAMESPACE}", params={"tenant_id": tenant_id})
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                entries = resp.json().get("entries") or {}
                scopes: dict[str, str] | None = None
                try:
                    pr = await client.get(f"{self._url}/config/{NAMESPACE}/_provenance",
                                          params={"tenant_id": tenant_id})
                    if pr.status_code == 200:
                        scopes = {k: v.get("effective_scope", "config")
                                  for k, v in (pr.json().get("keys") or {}).items()}
                except Exception as exc:  # noqa: BLE001 — só o rótulo da procedência se perde, e é dito
                    logger.info("speech_config: procedencia de %s indisponivel (%s) — valores valem, "
                                "rotulados 'config'", NAMESPACE, exc)
        except Exception as exc:  # noqa: BLE001 — degradação dita abaixo
            anterior = ent[0] if ent and ent[3] else None
            logger.warning(
                "speech_config: nao consegui ler %s do config-api (%s) — %s", NAMESPACE, exc,
                "segue o ultimo valor lido" if anterior
                else "a segmentacao da fala configurada para o tenant NAO vale; default de codigo",
            )
            seg = anterior or SpeechSegmentation(
                provenance={p.field: "default: config-api indisponivel" for p in PARAMS})
            self._cache[tenant_id] = (seg, False, agora, anterior is not None)
            return seg
        seg = resolve(entries, scopes, tenant_id)
        self._cache[tenant_id] = (seg, True, agora, True)
        return seg
