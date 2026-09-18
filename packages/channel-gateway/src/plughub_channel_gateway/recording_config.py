"""
recording_config.py — o que a GRAVAÇÃO de chamada lê do config-api (VOZ-06).

Duas chaves, cada uma na casa do fato que ela é:

  webrtc.recording_notice                 o AVISO dito ao cliente antes de gravar (texto do
                                          tenant, editável na aba WebRTC)
  storage.call_recording_retention_days   por quantos dias a gravação fica (ADR voice-media-plane
                                          V5: retenção é política POR CLASSE de artefato, uma
                                          entrada por classe no namespace `storage`)

Lido a cada gravação que começa, sem cache: começar a gravar é raro, e um valor velho aqui ou
avisa o cliente com o texto errado ou guarda a voz dele pelo prazo errado. O config-api não valida
valor, então quem lê valida — e toda degradação diz o que deixou de valer:

  ausente          → default de código, AVISO nomeando a chave
  tipo/faixa ruim  → default de código, ERRO nomeando a chave e o valor
  config-api fora  → default de código, AVISO dizendo que a config do tenant NÃO vale
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("plughub.channel-gateway.recording_config")

NOTICE_NS,    NOTICE_KEY    = "webrtc",  "recording_notice"
RETENTION_NS, RETENTION_KEY = "storage", "call_recording_retention_days"

NOTICE_MAX_CHARS = 1000
RETENTION_MIN, RETENTION_MAX = 1, 3650

# A retenção de fábrica é a decisão do dono já registrada na VOZ-06: 30 dias (LGPD, minimização).
DEFAULT_RETENTION_DAYS = 30


@dataclass(frozen=True)
class RecordingPolicy:
    notice:         str
    retention_days: int
    provenance:     dict[str, str] = field(default_factory=dict)


def _notice(entries: dict, default: str) -> tuple[str, str]:
    if NOTICE_KEY not in entries:
        logger.warning("recording_config: %s.%s ausente — vale o aviso de codigo", NOTICE_NS, NOTICE_KEY)
        return default, "default: ausente"
    v = entries[NOTICE_KEY]
    if not isinstance(v, str) or not v.strip() or len(v) > NOTICE_MAX_CHARS:
        logger.error("recording_config: %s.%s invalido (%r) — texto nao vazio de ate %d caracteres; "
                     "vale o aviso de codigo", NOTICE_NS, NOTICE_KEY, v, NOTICE_MAX_CHARS)
        return default, "default: invalido"
    return v.strip(), "config"


def _retention(entries: dict) -> tuple[int, str]:
    if RETENTION_KEY not in entries:
        logger.warning("recording_config: %s.%s ausente — a gravacao fica %d dias (default de codigo)",
                       RETENTION_NS, RETENTION_KEY, DEFAULT_RETENTION_DAYS)
        return DEFAULT_RETENTION_DAYS, "default: ausente"
    v = entries[RETENTION_KEY]
    if isinstance(v, bool) or not isinstance(v, int) or not RETENTION_MIN <= v <= RETENTION_MAX:
        logger.error("recording_config: %s.%s invalido (%r) — inteiro entre %d e %d; a gravacao fica "
                     "%d dias (default de codigo)", RETENTION_NS, RETENTION_KEY, v, RETENTION_MIN,
                     RETENTION_MAX, DEFAULT_RETENTION_DAYS)
        return DEFAULT_RETENTION_DAYS, "default: invalido"
    return v, "config"


async def _entries(client: httpx.AsyncClient, url: str, ns: str, tenant_id: str) -> dict:
    resp = await client.get(f"{url}/config/{ns}", params={"tenant_id": tenant_id})
    if resp.status_code != 200:
        raise RuntimeError(f"GET /config/{ns} -> HTTP {resp.status_code}")
    return resp.json().get("entries") or {}


async def resolve(config_api_url: str, tenant_id: str, default_notice: str) -> RecordingPolicy:
    url = config_api_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            web = await _entries(client, url, NOTICE_NS, tenant_id)
            sto = await _entries(client, url, RETENTION_NS, tenant_id)
    except Exception as exc:  # noqa: BLE001 — degradação dita abaixo
        logger.warning("recording_config: config-api ilegivel (%s) — o aviso e a retencao do tenant NAO "
                       "valem nesta gravacao: aviso de codigo e %d dias", exc, DEFAULT_RETENTION_DAYS)
        return RecordingPolicy(default_notice, DEFAULT_RETENTION_DAYS,
                               {"notice": "default: config-api indisponivel",
                                "retention_days": "default: config-api indisponivel"})
    notice, pn = _notice(web, default_notice)
    dias, pr = _retention(sto)
    return RecordingPolicy(notice, dias, {"notice": pn, "retention_days": pr})
