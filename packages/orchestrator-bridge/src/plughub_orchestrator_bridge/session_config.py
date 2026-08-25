"""
session_config.py
Local cache for Config API session namespace settings.

Fetches config from the Config API at startup and caches in memory.
Invalidated via config.changed Kafka events (namespace == "session").
After invalidation, a background reload fetches fresh values from the API.

Defaults mirror the seeds in packages/config-api/src/plughub_config_api/seed.py
so the orchestrator-bridge works correctly even when the Config API is unreachable.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp

logger = logging.getLogger("plughub.orchestrator-bridge.session_config")

# Defaults matching Config API seed — session namespace
_DEFAULTS: dict[str, Any] = {
    "orchestrator_session_ttl_s":   14_400,   # 4h — session Redis keys in bridge
    "transcript_ttl_s":             14_400,   # 4h — conversation-writer Redis buffer
    "replayer_hydration_ttl_s":      3_600,   # 1h — stream hydration for evaluation
    "replay_context_ttl_s":          3_600,   # 1h — ReplayContext key TTL
    # 24h — TTL de `{t}:pool_config:{p}`. GANHOU LEITOR em 2026-08-25
    # (`instance_bootstrap._pool_config_ttl_s`); até então era semeada, cacheada
    # e sem uso — o 4º órfão de config do repositório. Subiu de 3 600 para
    # 86 400 porque a expiração da chave apaga os pools do routing inteiro
    # (`get_candidate_pools` → lista vazia), e não é o TTL que limpa pool
    # removido: `_reconcile_pool_configs` o DELETA explicitamente.
    "pool_config_ttl_s":            86_400,
    "sentiment_live_ttl_s":            300,   # 5m — live sentiment in Redis
    # `queue_default_agent_type_id` / `queue_default_skill_id` REMOVIDAS
    # (2026-08-24, defeito 2). Falavam o vocabulário PRÉ-SLOT: desde 2026-07-13
    # produção é o snapshot do slot `current` do POOL, então nem agent_type nem
    # skill resolvem flow — o "default de tenant" não tinha como funcionar mesmo
    # preenchido. Medido antes de remover: as duas vazias, zero usuários em 36
    # pools, e a tela prometendo "Empty = tenant default" o tempo todo.
    # Tratamento de fila agora tem UM endereço: `pool.queue_config.pool_id`.
    # Não reintroduzir default de tenant sem um campo de POOL.
}


class SessionConfigCache:
    """
    In-memory cache of Config API session namespace.

    Thread-safe for asyncio (single-threaded event loop).
    Uses aiohttp.ClientSession for non-blocking HTTP (same client as bridge).

    Usage:
        cache = SessionConfigCache()
        await cache.reload(config_api_url, http_client)         # startup
        ttl = cache.get("orchestrator_session_ttl_s", 14400)   # per Redis call
        cache.invalidate()                                       # on config.changed
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._loaded_at: float = 0.0
        self._invalidated: bool = True   # start invalid — forces first reload
        # ⚠️ `GET /config/{namespace}` EXIGE `?tenant_id=` (Query obrigatório em
        # `config-api/router.py:174`). Sem ele a resposta é **422**, o reload cai
        # no ramo de warning e o cache fica nos _DEFAULTS PARA SEMPRE — isto é,
        # a tela do namespace `session` promete efeito e não tem.
        #
        # É o MESMO defeito que o routing-engine teve e consertou em 2026-06-05
        # (ver `routing_config.py:107`); aqui ele sobreviveu porque o modo de
        # falha é ficar no default, que quase sempre parece certo. Medido em
        # 2026-08-25 ao dar leitor à chave `pool_config_ttl_s`.
        #
        # "__global__" resolve os defaults da instalação; main.py configura o
        # tenant real no boot.
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
        Safe to call synchronously from the event-handling hot path.
        """
        if key in self._data:
            return self._data[key]
        return _DEFAULTS.get(key, default)

    def invalidate(self) -> None:
        """
        Marks cache as stale. Called by _handle_config_changed on
        config.changed events with namespace == "session".
        Does NOT clear _data — existing values remain available until
        a reload completes so the bridge continues operating during refresh.
        """
        self._invalidated = True
        logger.debug("SessionConfigCache invalidated")

    @property
    def is_stale(self) -> bool:
        return self._invalidated

    async def reload(
        self,
        config_api_url: str,
        http: aiohttp.ClientSession,
    ) -> None:
        """
        Fetches GET {config_api_url}/config/session and populates the cache.
        Falls back silently to defaults on any error so the bridge
        remains operational when the Config API is temporarily unreachable.
        """
        url = f"{config_api_url.rstrip('/')}/config/session"
        try:
            async with http.get(
                url,
                params={"tenant_id": self._tenant_id},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    body = await resp.json()
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
                        "SessionConfigCache reloaded: %d keys from %s",
                        len(new_data), url,
                    )
                else:
                    self._invalidated = True
                    self._warn_degraded(f"HTTP {resp.status}", url)
        except Exception as exc:
            # Degraded mode: keep whatever was cached (or defaults) and re-mark
            # as stale so the next config.changed event triggers another reload.
            self._invalidated = True
            self._warn_degraded(str(exc), url)

    def _warn_degraded(self, cause: str, url: str) -> None:
        """Degradação NUNCA é silenciosa — e "usando defaults" não é o mesmo que
        dizer O QUE passou a ser default.

        Esta linha existe porque a anterior não bastou: ela avisava havia meses
        (`Cannot connect to host localhost:3500`) e ninguém leu, porque não dizia
        que a consequência era o namespace `session` INTEIRO — seis TTLs
        editáveis na tela — deixar de valer. Ver `main.py` § CONFIG_API_URL.
        """
        logger.warning(
            "SessionConfigCache reload FALHOU (%s) em %s — o namespace `session` "
            "NÃO foi lido; estas chaves passam a vir do código e a tela do "
            "Config API não tem efeito sobre elas: %s",
            cause, url, ", ".join(sorted(_DEFAULTS)),
        )


# Module-level singleton — imported by main.py
session_config = SessionConfigCache()
