"""
routing_config.py
Local cache for Config API routing namespace settings.

Fetches config from the Config API at startup and caches it in memory.
Invalidated via config.changed Kafka events (namespace == "routing").
After invalidation, a background reload fetches fresh values from the API.

Defaults mirror the seeds in packages/config-api/src/plughub_config_api/seed.py
so the routing-engine works correctly even when the Config API is unreachable.

Esta correspondência é CONTRATO, não comentário de cortesia. Em 2026-08-03 ela
era verificavelmente falsa para `score_weights` (as duas pontas tinham chaves
diferentes) e enganosa para `snapshot_ttl_s` (ambos os lados diziam 120 e o
código gravava 3600). As duas chaves foram REMOVIDAS daqui e do seed, medidas
antes — ver o comentário longo em `seed.py` § routing. Docstring que afirma uma
correspondência inexistente é a mesma classe de defeito do `registry.py:82`
(heartbeat + auto-release que não existiam): custa caro porque quem lê para de
conferir.

Ao acrescentar chave aqui: ou ela existe no seed E tem leitor, ou não entra.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("plughub.routing.routing_config")

# Defaults matching Config API seed — routing namespace
_DEFAULTS: dict[str, Any] = {
    "claim_lease_s":            180,   # Frente 1 (pull): TTL da lease do claim
    # Fase C (ADR requeue, D3): janela da reserva criada por QUEDA DE TRANSPORTE em
    # fila pull POOLED (aprovação e afins). O item volta reservado ao dono anterior
    # e transborda para o pool inteiro depois destes segundos.
    # NÃO existe chave equivalente para `-int`: fila interna é AUTHOR-BOUND e não
    # transborda em tempo nenhum (ver `Router._drop_reserve_window_s`). Uma
    # `drop_reserve_window_internal_s` chegou a ser semeada em 2026-08-04 e foi
    # removida no mesmo dia — era o erro de categoria que a migration
    # `20260730000000_pool_internal_queue` nomeia, com cara de parâmetro.
    "drop_reserve_window_default_s":   30,
    # ⚠️ SEM LEITOR (medido 2026-08-24, D14.1). `routing_config.get("sla_default_ms")`
    # não aparece em lugar nenhum fora dos testes — a chave é semeada, cacheada,
    # testada e **exibida ao operador** na tela do namespace `routing`, e não faz
    # nada. Botão que promete efeito e não tem é pior que default duplicado.
    #
    # O alvo de espera efetivo vem do POOL (`sla_target_ms`); quando ausente, do
    # `SLA_TARGET_MS_FALLBACK` do `models.py`, que é o mesmo número por coincidência
    # histórica, não por referência. **Não conectar esta chave sem decidir antes qual
    # deve vencer** — hoje um operador que a edite não muda comportamento nenhum.
    # Remoção registrada no `TODO.md`; cuidado: `smoke_config_routing_orphan_keys.sh`
    # a usa como CANÁRIO de "namespace intacto".
    "sla_default_ms":           480_000,
    "estimated_wait_factor":    0.7,
    "congestion_sla_factor":    1.5,
    "performance_score_weight": 0.0,
    # `snapshot_ttl_s` e `score_weights` saíram em 2026-08-03 (sem leitor).
    # Peso de score é campo do POOL — `pool.aging_factor`/`pool.breach_factor`
    # em `models.py`, lidos pelo `scorer.py`. Não realocar para cá.
    # Mensagens de sistema viradas ao cliente (render v2, queue-attended-model).
    # Tenant sobrescreve via Config API namespace `routing` no idioma desejado;
    # defaults em pt-BR. As mensagens da fila ATENDIDA são do skill-flow (YAML).
    "msg_queue_waiting":    "Aguardando agente disponível. Por favor, aguarde...",
    "msg_outage_rejection": "Não há atendentes disponíveis no momento. Por favor, tente novamente mais tarde.",
    "msg_queue_timeout":    "Tempo máximo de espera atingido. Por favor, tente novamente mais tarde.",
    "msg_no_resource":      "Não há recurso disponível para continuar o atendimento. Por favor, tente novamente mais tarde.",
    # Fila de sistema (system-queue.md, Fase A) — proteções operacionais.
    # queue_max_total: teto TOTAL do buffer grátis (sessões em fila muda,
    #   isentas de C). Hard limit: Config fora ⇒ este default, nunca ilimitado.
    # queue_max_wait_by_channel: teto de espera muda por canal (s); 0 = canal
    #   não aceita fila muda (vai direto a outage — recomendado p/ voz: dead
    #   air segura trunk). Canais ausentes ⇒ queue_max_wait_default_s (1800).
    "queue_max_total": 100,
    "queue_max_wait_by_channel": {
        "voice":    300,
        "webrtc":   300,
        "webchat":  1800,
        "whatsapp": 14_400,
    },
    "msg_queue_full": "Nossa fila de espera está cheia no momento. Por favor, tente novamente mais tarde.",
}


class RoutingConfigCache:
    """
    In-memory cache of Config API routing namespace.

    Thread-safe for asyncio (single-threaded event loop).
    Uses httpx.AsyncClient for non-blocking HTTP.

    Usage:
        cache = RoutingConfigCache()
        await cache.reload(config_api_url, http_client)    # startup
        value = cache.get("performance_score_weight", 0.0) # per routing call
        cache.invalidate()                                  # on config.changed
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._loaded_at: float = 0.0
        self._invalidated: bool = True   # start invalid — forces first reload
        # Fix (2026-06-05): GET /config/{namespace} EXIGE ?tenant_id= — sem ele
        # o Config API responde 422 e o reload nunca funcionou (mascarado pelo
        # erro de conexão enquanto o env apontava p/ localhost). "__global__"
        # resolve os defaults da instalação; main.py configura o tenant real.
        self._tenant_id: str = "__global__"

    def configure_tenant(self, tenant_id: str) -> None:
        if tenant_id:
            self._tenant_id = tenant_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """
        Returns value from cache, falling back to _DEFAULTS, then `default`.
        Safe to call synchronously from the routing hot path.
        """
        if key in self._data:
            return self._data[key]
        return _DEFAULTS.get(key, default)

    def invalidate(self) -> None:
        """
        Marks cache as stale.  Called by ConfigChangedHandler on
        config.changed events with namespace == "routing".
        Does NOT clear _data — existing values remain available until
        a reload completes so routing continues during the refresh.
        """
        self._invalidated = True
        logger.debug("RoutingConfigCache invalidated")

    @property
    def is_stale(self) -> bool:
        return self._invalidated

    async def reload(
        self,
        config_api_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """
        Fetches GET {config_api_url}/config/routing and populates the cache.
        Falls back silently to defaults on any error so the routing-engine
        remains operational when the Config API is temporarily unreachable.
        """
        url = f"{config_api_url.rstrip('/')}/config/routing"
        try:
            resp = await http_client.get(
                url, params={"tenant_id": self._tenant_id}, timeout=5.0
            )
            resp.raise_for_status()
            body = resp.json()
            # Config API returns { "entries": { key: { "value": ..., ... } } }
            # or a flat { key: value } dict depending on the endpoint used.
            entries = body.get("entries") or body
            new_data: dict[str, Any] = {}
            for k, v in entries.items():
                # Unwrap ConfigEntry envelope if present
                if isinstance(v, dict) and "value" in v:
                    new_data[k] = v["value"]
                else:
                    new_data[k] = v
            self._data = new_data
            self._loaded_at = time.monotonic()
            self._invalidated = False
            logger.info(
                "RoutingConfigCache reloaded: %d keys from %s",
                len(new_data), url,
            )
        except Exception as exc:
            # Degraded mode: keep whatever was cached (or defaults) and re-mark
            # as stale so the next config.changed event will trigger another reload.
            self._invalidated = True
            logger.warning(
                "RoutingConfigCache reload failed (%s) — using cached/default values",
                exc,
            )


# Module-level singleton — imported by kafka_listener and router
routing_config = RoutingConfigCache()
