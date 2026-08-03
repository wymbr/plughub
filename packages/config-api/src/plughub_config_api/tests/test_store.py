"""
test_store.py
Unit tests for ConfigStore (store.py) + ConfigCache (cache.py) + seed (seed.py).

Strategy:
  - asyncpg pool is mocked: pool.fetchrow / pool.fetch / pool.fetchval / pool.execute
    return controlled values without a real DB connection.
  - Redis client is mocked: get/set/delete/scan return controlled values.
  - ConfigStore is instantiated directly with mock pool + mock cache.

Test classes:
  TestConfigCache       — cache read/write/invalidation, MISS sentinel
  TestConfigStoreGet    — cache hit, cache miss→DB, two-level resolution
  TestConfigStoreSet    — upsert + cache invalidation
  TestConfigStoreDelete — delete + cache invalidation
  TestConfigStoreList   — list_namespace, list_all
  TestSeedData          — seed entries are well-formed and cover all namespaces
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from ..cache import MISS, ConfigCache
from ..db    import GLOBAL
from ..seed  import _SEED
from ..store import ConfigStore


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_redis(
    get_return=None,
    scan_return=None,
) -> MagicMock:
    r = MagicMock()
    r.get    = AsyncMock(return_value=get_return)
    r.set    = AsyncMock(return_value=True)
    r.delete = AsyncMock(return_value=1)
    r.scan   = AsyncMock(return_value=(0, scan_return or []))
    r.ping   = AsyncMock(return_value=True)
    return r


def _make_pool(
    fetchrow_return=None,
    fetch_return=None,
    execute_return="DELETE 1",
) -> MagicMock:
    pool = MagicMock()
    pool.fetchrow  = AsyncMock(return_value=fetchrow_return)
    pool.fetch     = AsyncMock(return_value=fetch_return or [])
    pool.fetchval  = AsyncMock(return_value=1)
    pool.execute   = AsyncMock(return_value=execute_return)
    # acquire() as context manager (used by ensure_schema)
    conn = AsyncMock()
    conn.execute = AsyncMock()
    pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=False),
    ))
    return pool


def _row(value: object) -> MagicMock:
    """Simulates an asyncpg.Record with a 'value' column (JSON string)."""
    r = MagicMock()
    r.__getitem__ = lambda self, k: json.dumps(value) if k == "value" else k
    return r


def _full_row(key: str, value: object, description: str = "", ns: str = "ns") -> MagicMock:
    """Simulates an asyncpg.Record for list queries (key + value + description + updated_at)."""
    r = MagicMock()
    ts = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)
    mapping = {
        "key":         key,
        "value":       json.dumps(value),
        "namespace":   ns,
        "description": description,
        "updated_at":  ts,
    }
    r.__getitem__ = lambda self, k: mapping[k]
    return r


TENANT = "tenant_telco"


# ── TestConfigCache ───────────────────────────────────────────────────────────

class TestConfigCache:
    async def test_get_miss_returns_miss_sentinel(self):
        cache = ConfigCache(_make_redis(get_return=None))
        result = await cache.get(TENANT, "sentiment", "thresholds")
        assert result is MISS

    async def test_get_hit_returns_parsed_value(self):
        cached = {"satisfied": [0.3, 1.0]}
        cache  = ConfigCache(_make_redis(get_return=json.dumps(cached)))
        result = await cache.get(TENANT, "sentiment", "thresholds")
        assert result == cached

    async def test_get_hit_null_value_returns_none_not_miss(self):
        """None is a valid cached value (means key doesn't exist in DB)."""
        cache  = ConfigCache(_make_redis(get_return=json.dumps(None)))
        result = await cache.get(TENANT, "sentiment", "unknown_key")
        assert result is None
        assert result is not MISS

    async def test_set_calls_redis_set_with_ttl(self):
        redis = _make_redis()
        cache = ConfigCache(redis, ttl=60)
        await cache.set(TENANT, "sentiment", "thresholds", {"k": 1})
        redis.set.assert_called_once()
        args = redis.set.call_args
        assert args.kwargs.get("ex") == 60 or (len(args.args) > 2 and 60 in args.args)

    async def test_invalidate_tenant_key_deletes_cache(self):
        redis = _make_redis()
        cache = ConfigCache(redis)
        await cache.invalidate(TENANT, "sentiment", "thresholds")
        redis.delete.assert_called_once()

    async def test_invalidate_global_also_scans_tenant_variants(self):
        redis = _make_redis()
        cache = ConfigCache(redis)
        await cache.invalidate(GLOBAL, "sentiment", "thresholds")
        # scan should be called to find and delete tenant variants
        redis.scan.assert_called()

    async def test_redis_error_in_get_returns_miss(self):
        redis = _make_redis()
        redis.get = AsyncMock(side_effect=Exception("redis down"))
        cache  = ConfigCache(redis)
        result = await cache.get(TENANT, "ns", "k")
        assert result is MISS


# ── TestConfigStoreGet ────────────────────────────────────────────────────────

class TestConfigStoreGet:
    def _store(self, pool, redis_get=None):
        redis = _make_redis(get_return=redis_get)
        cache = ConfigCache(redis, ttl=60)
        return ConfigStore(pool, cache), redis

    async def test_cache_hit_skips_db(self):
        val    = {"satisfied": [0.3, 1.0]}
        pool   = _make_pool()
        store, _ = self._store(pool, redis_get=json.dumps(val))
        result = await store.get(TENANT, "sentiment", "thresholds")
        assert result == val
        pool.fetchrow.assert_not_called()

    async def test_cache_miss_queries_db_and_returns_value(self):
        val  = 300
        pool = _make_pool(fetchrow_return=_row(val))
        store, redis = self._store(pool, redis_get=None)
        result = await store.get(TENANT, "sentiment", "live_ttl_s")
        assert result == val
        pool.fetchrow.assert_called_once()
        redis.set.assert_called_once()   # populated cache after DB hit

    async def test_cache_miss_db_miss_returns_none(self):
        pool = _make_pool(fetchrow_return=None)
        store, _ = self._store(pool, redis_get=None)
        result = await store.get(TENANT, "sentiment", "nonexistent")
        assert result is None

    async def test_get_or_default_returns_default_on_none(self):
        pool = _make_pool(fetchrow_return=None)
        store, _ = self._store(pool, redis_get=None)
        result = await store.get_or_default(TENANT, "quota", "max_concurrent_sessions", 50)
        assert result == 50

    async def test_get_or_default_returns_value_when_exists(self):
        pool = _make_pool(fetchrow_return=_row(200))
        store, _ = self._store(pool, redis_get=None)
        result = await store.get_or_default(TENANT, "quota", "max_concurrent_sessions", 50)
        assert result == 200


# ── TestConfigStoreSet ────────────────────────────────────────────────────────

class TestConfigStoreSet:
    async def test_set_tenant_specific_calls_db_and_invalidates_cache(self):
        pool  = _make_pool()
        redis = _make_redis()
        store = ConfigStore(pool, ConfigCache(redis))
        await store.set(TENANT, "sentiment", "live_ttl_s", 600, "custom TTL")
        pool.execute.assert_called_once()
        redis.delete.assert_called_once()

    async def test_set_global_stores_with_global_sentinel(self):
        pool  = _make_pool()
        redis = _make_redis()
        store = ConfigStore(pool, ConfigCache(redis))
        await store.set(None, "routing", "snapshot_ttl_s", 90)
        args = pool.execute.call_args[0]
        sql_call = args[0]

        # Asserção sobre ESTRUTURA, não sobre serialização (corrigido 2026-08-03).
        # A versão anterior exigia a substring `"INSERT INTO platform_config"` e passou
        # a reprovar quando a tabela foi qualificada com o schema
        # (`INSERT INTO public.platform_config`, `db.py:166` — feito de propósito, para
        # que o schema `$user` do usuário `plughub` não sombreie `public`). O código
        # ficou MAIS correto e o teste ficou vermelho: é o padrão que já apareceu em
        # `test_pools_occupancy_bucket`, `test_account_selector` e `test_router` do
        # calendar — a asserção casa com uma foto do texto, não com o comportamento.
        assert "platform_config" in sql_call
        assert "ON CONFLICT" in sql_call        # é upsert, não insert cego

        # POSIÇÃO, não pertinência: `GLOBAL in args` passaria se o sentinela aparecesse
        # em QUALQUER argumento — inclusive dentro do texto do SQL, onde ele de fato
        # aparece no DDL (`DEFAULT '__global__'`). O contrato é que `tenant_id=None`
        # vira o sentinela no 1º parâmetro ($1).
        assert args[1] == GLOBAL
        assert args[2] == "routing"
        assert args[3] == "snapshot_ttl_s"

    async def test_set_triggers_scan_invalidation_for_global(self):
        """Writing global → scan should be called to invalidate tenant variants."""
        pool  = _make_pool()
        redis = _make_redis()
        store = ConfigStore(pool, ConfigCache(redis))
        await store.set(None, "sentiment", "thresholds", {"new": "value"})
        redis.scan.assert_called()


# ── TestConfigStoreDelete ─────────────────────────────────────────────────────

class TestConfigStoreDelete:
    async def test_delete_existing_returns_true(self):
        pool  = _make_pool(execute_return="DELETE 1")
        redis = _make_redis()
        store = ConfigStore(pool, ConfigCache(redis))
        result = await store.delete(TENANT, "sentiment", "live_ttl_s")
        assert result is True
        redis.delete.assert_called_once()

    async def test_delete_nonexistent_returns_false(self):
        pool  = _make_pool(execute_return="DELETE 0")
        redis = _make_redis()
        store = ConfigStore(pool, ConfigCache(redis))
        result = await store.delete(TENANT, "sentiment", "nonexistent")
        assert result is False
        redis.delete.assert_not_called()   # no cache invalidation needed


# ── TestConfigStoreList ───────────────────────────────────────────────────────

class TestConfigStoreList:
    async def test_list_namespace_returns_resolved_dict(self):
        rows = [
            _full_row("snapshot_ttl_s", 120),
            _full_row("sla_default_ms", 480000),
        ]
        pool  = _make_pool(fetch_return=rows)
        redis = _make_redis(get_return=None)  # namespace cache miss
        store = ConfigStore(pool, ConfigCache(redis))
        result = await store.list_namespace(TENANT, "routing")
        assert result["snapshot_ttl_s"] == 120
        assert result["sla_default_ms"] == 480000

    async def test_list_namespace_cache_hit_skips_db(self):
        ns_data = {"snapshot_ttl_s": 120}
        pool    = _make_pool()
        redis   = _make_redis(get_return=json.dumps(ns_data))
        store   = ConfigStore(pool, ConfigCache(redis))
        result  = await store.list_namespace(TENANT, "routing")
        assert result == ns_data
        pool.fetch.assert_not_called()

    async def test_list_all_returns_grouped_by_namespace(self):
        rows = [
            _full_row("thresholds", {"satisfied": [0.3, 1.0]}, ns="sentiment"),
            _full_row("live_ttl_s", 300, ns="sentiment"),
            _full_row("snapshot_ttl_s", 120, ns="routing"),
        ]
        # Patch to return different ns in the row
        def _row_ns(key, value, ns):
            r = MagicMock()
            r.__getitem__ = lambda self, k: {
                "key":       key,
                "value":     json.dumps(value),
                "namespace": ns,
            }[k]
            return r

        pool  = _make_pool(fetch_return=rows)
        redis = _make_redis()
        store = ConfigStore(pool, ConfigCache(redis))
        result = await store.list_all(TENANT)
        # list_all parses namespace from each row
        pool.fetch.assert_called_once()


# ── TestSeedData ──────────────────────────────────────────────────────────────

class TestSeedData:
    def test_all_entries_have_required_fields(self):
        for entry in _SEED:
            assert len(entry) == 4, f"Entry {entry[:2]} must have (ns, key, value, description)"
            ns, key, value, desc = entry
            assert isinstance(ns, str) and ns, f"namespace must be non-empty string"
            assert isinstance(key, str) and key, f"key must be non-empty string"
            assert isinstance(desc, str), f"description must be a string"

    def test_all_required_namespaces_present(self):
        namespaces = {entry[0] for entry in _SEED}
        required   = {"sentiment", "routing", "session", "consumer", "dashboard",
                      "webchat", "masking"}
        assert required.issubset(namespaces), \
            f"Missing namespaces: {required - namespaces}"

    def test_quota_is_NOT_seeded(self):
        """`quota` saiu da lista de obrigatórios — e a ausência virou asserção.

        Decisão de produto documentada (CLAUDE.md § Pricing Module): *"Quota limits
        written to Redis on plan activation — **not seeded by Config API**"*. O teste
        antigo exigia o namespace `quota` no `_SEED` e reprovava desde que ele foi
        removido — cobrando um contrato revogado.

        Apagar a linha teria bastado para o verde, e teria deixado o lugar vazio: nada
        impediria alguém de re-semear `quota` aqui e criar uma SEGUNDA fonte de verdade
        para limite de plano, silenciosamente (invariante *one source per domain*).
        Invertida, a asserção passa a defender a decisão em vez de a contradizer —
        mesmo movimento aplicado em `test_human_instance_identity.py` em 2026-08-02.
        """
        namespaces = {entry[0] for entry in _SEED}
        assert "quota" not in namespaces, (
            "quota voltou ao seed do Config API. Limite de plano é escrito no Redis "
            "pela ativação de plano (pricing-api); semear aqui cria segunda fonte."
        )

    def test_sentiment_thresholds_are_valid(self):
        thresholds = next(
            v for ns, k, v, _ in _SEED
            if ns == "sentiment" and k == "thresholds"
        )
        assert set(thresholds.keys()) == {"satisfied", "neutral", "frustrated", "angry"}
        for cat, rng in thresholds.items():
            assert len(rng) == 2, f"{cat} must have [low, high]"
            assert rng[0] < rng[1], f"{cat} range must be ascending"

    def test_no_duplicate_namespace_key_combinations(self):
        seen = set()
        for ns, key, _, _ in _SEED:
            combo = (ns, key)
            assert combo not in seen, f"Duplicate seed entry: {ns}.{key}"
            seen.add(combo)

    def test_numeric_values_are_non_negative(self):
        """Nenhum default numérico é negativo.

        Era `> 0` e reprovava em DOIS defaults legítimos e documentados:
        `routing.performance_score_weight = 0.0` (*"0.0 = pure competency match
        (default — backward-compatible)"*, Arc 7d) e `pricing.reserve_markup_pct = 0.0`
        (*"default 0%"*). O teste afirmava um invariante que a plataforma nunca teve —
        zero é um valor de negócio válido para peso e para markup.
        """
        for ns, key, value, _ in _SEED:
            # booleans are int subclass in Python — skip them
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                assert value >= 0, f"{ns}.{key} = {value} must not be negative"

    def test_duration_defaults_are_strictly_positive(self):
        """A parte do `> 0` que ainda vale — e agora sabe a quê se aplica.

        Afrouxar tudo para `>= 0` compraria o verde perdendo o que o teste defendia:
        um TTL, janela ou intervalo com valor 0 não é "desligado", é **expiração
        imediata** ou laço apertado — degradação que não levanta erro em lugar nenhum
        (`live_ttl_s = 0` faria o sentimento sumir do Redis sem uma linha de log).
        Peso e percentual aceitam zero; duração, não.
        """
        for ns, key, value, _ in _SEED:
            if not key.endswith(("_s", "_ms", "_days", "_hours", "_min")):
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                assert value > 0, f"{ns}.{key} = {value}: duração/TTL não pode ser 0"

    async def test_seed_function_calls_set_for_each_entry(self):
        pool  = _make_pool(fetchrow_return=None)  # get_entry returns None → not exists
        redis = _make_redis()
        store = ConfigStore(pool, ConfigCache(redis))
        result = await __import__(
            "plughub_config_api.seed", fromlist=["seed"]
        ).seed(store, overwrite=False)
        assert result["inserted"] == len(_SEED)
        assert result["skipped"]  == 0

    async def test_seed_skips_existing_entries_when_no_overwrite(self):
        # get_entry returns a non-None value → all entries already exist
        existing = {"key": "k", "value": 1, "description": "", "updated_at": "2026-01-01T00:00:00"}
        pool  = _make_pool(fetchrow_return=_full_row("k", 1))
        redis = _make_redis()
        store = ConfigStore(pool, ConfigCache(redis))
        result = await __import__(
            "plughub_config_api.seed", fromlist=["seed"]
        ).seed(store, overwrite=False)
        assert result["skipped"]  == len(_SEED)
        assert result["inserted"] == 0
