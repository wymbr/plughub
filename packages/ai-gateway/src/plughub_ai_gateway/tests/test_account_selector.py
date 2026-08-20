"""
test_account_selector.py
Unit tests for AccountSelector — multi-account load balancing and throttle tracking.
All Redis I/O is mocked; no real Redis required.
"""

from __future__ import annotations

import hashlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from plughub_ai_gateway.account_selector import AccountSelector, LLMAccount


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_account(provider: str = "anthropic", api_key: str = "sk-test-key") -> LLMAccount:
    return LLMAccount(provider=provider, api_key=api_key, rpm_limit=60, tpm_limit=100_000)


def _key_id(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def _make_redis(
    mget_return: list | None = None,
    throttled: bool = False,
    rpm: int = 0,
    tpm: int = 0,
) -> MagicMock:
    """Build a minimal mock Redis client.

    pipeline() is a synchronous call that returns an object with sync
    chainable methods (incr, expire, incrby) and one async method (execute).
    """
    redis = MagicMock()
    if mget_return is None:
        mget_return = [
            b"1" if throttled else None,
            str(rpm).encode() if rpm else None,
            str(tpm).encode() if tpm else None,
        ]
    # mget / set / ping are async
    redis.mget = AsyncMock(return_value=mget_return)
    redis.set = AsyncMock()

    # pipeline() is sync and returns a sync-chainable object with async execute
    pipe = MagicMock()
    pipe.incr = MagicMock(return_value=pipe)
    pipe.expire = MagicMock(return_value=pipe)
    pipe.incrby = MagicMock(return_value=pipe)
    pipe.set = MagicMock(return_value=pipe)   # record_outcome grava last_ok/last_err
    pipe.execute = AsyncMock(return_value=[1, True, 1, True])
    redis.pipeline.return_value = pipe
    return redis


# ─── LLMAccount ───────────────────────────────────────────────────────────────

class TestLLMAccount:
    def test_key_id_is_sha256_prefix(self) -> None:
        acc = _make_account(api_key="sk-abc123")
        expected = hashlib.sha256(b"sk-abc123").hexdigest()[:16]
        assert acc.key_id == expected

    def test_provider_key_format(self) -> None:
        acc = _make_account(provider="anthropic", api_key="sk-abc")
        assert acc.provider_key == f"anthropic:{acc.key_id}"

    def test_different_keys_have_different_key_ids(self) -> None:
        a = _make_account(api_key="sk-aaa")
        b = _make_account(api_key="sk-bbb")
        assert a.key_id != b.key_id

    def test_default_limits(self) -> None:
        acc = LLMAccount(provider="anthropic", api_key="sk-test")
        assert acc.rpm_limit == 60
        assert acc.tpm_limit == 100_000

    def test_weight_default(self) -> None:
        acc = LLMAccount(provider="anthropic", api_key="sk-test")
        assert acc.weight == 1


# ─── AccountSelector — pick() ─────────────────────────────────────────────────

class TestAccountSelectorPick:
    @pytest.mark.asyncio
    async def test_pick_returns_none_for_unknown_provider(self) -> None:
        redis = _make_redis()
        selector = AccountSelector(redis, [_make_account(provider="anthropic")])
        result = await selector.pick("openai")
        assert result is None

    @pytest.mark.asyncio
    async def test_pick_returns_none_when_no_accounts(self) -> None:
        redis = _make_redis()
        selector = AccountSelector(redis, [])
        result = await selector.pick("anthropic")
        assert result is None

    @pytest.mark.asyncio
    async def test_single_account_available_returns_provider_key(self) -> None:
        acc = _make_account()
        redis = _make_redis(throttled=False, rpm=0, tpm=0)
        selector = AccountSelector(redis, [acc])
        result = await selector.pick("anthropic")
        assert result == acc.provider_key

    @pytest.mark.asyncio
    async def test_single_account_throttled_returns_none(self) -> None:
        acc = _make_account()
        redis = _make_redis(throttled=True)
        selector = AccountSelector(redis, [acc])
        result = await selector.pick("anthropic")
        assert result is None

    @pytest.mark.asyncio
    async def test_single_account_rpm_at_limit_returns_none(self) -> None:
        acc = _make_account()
        redis = _make_redis(throttled=False, rpm=60, tpm=0)
        selector = AccountSelector(redis, [acc])
        result = await selector.pick("anthropic")
        assert result is None

    @pytest.mark.asyncio
    async def test_single_account_tpm_at_limit_returns_none(self) -> None:
        acc = _make_account()
        redis = _make_redis(throttled=False, rpm=0, tpm=100_000)
        selector = AccountSelector(redis, [acc])
        result = await selector.pick("anthropic")
        assert result is None

    @pytest.mark.asyncio
    async def test_multi_account_returns_least_loaded(self) -> None:
        acc_a = LLMAccount(provider="anthropic", api_key="sk-key-a", rpm_limit=60, tpm_limit=100_000)
        acc_b = LLMAccount(provider="anthropic", api_key="sk-key-b", rpm_limit=60, tpm_limit=100_000)

        redis = AsyncMock()
        # acc_a: rpm=30 (50% util), tpm=0 → util = 0.7*0.5 + 0.3*0 = 0.35
        # acc_b: rpm=10 (17% util), tpm=0 → util = 0.7*0.167 + 0.3*0 = 0.117  ← lower
        def mget_side_effect(*keys):
            k = keys[0] if isinstance(keys[0], str) else keys[0].decode()
            key_a_id = acc_a.key_id
            key_b_id = acc_b.key_id
            if key_a_id in str(keys):
                return [None, b"30", b"0"]   # not throttled, rpm=30
            else:
                return [None, b"10", b"0"]   # not throttled, rpm=10

        # Two calls to _is_available (one per account) + two to _utilization
        rpm_a_calls = 0
        async def mget_mock(*args):
            keys = args
            if len(keys) == 3:
                key = str(keys[0])
                if acc_a.key_id in key:
                    return [None, b"30", b"0"]
                else:
                    return [None, b"10", b"0"]
            if len(keys) == 2:
                key = str(keys[0])
                if acc_a.key_id in key:
                    return [b"30", b"0"]
                else:
                    return [b"10", b"0"]
            return [None, None, None]

        redis.mget = mget_mock
        selector = AccountSelector(redis, [acc_a, acc_b])
        result = await selector.pick("anthropic")
        assert result == acc_b.provider_key   # lower utilization

    @pytest.mark.asyncio
    async def test_multi_account_all_throttled_returns_none(self) -> None:
        acc_a = LLMAccount(provider="anthropic", api_key="sk-key-a")
        acc_b = LLMAccount(provider="anthropic", api_key="sk-key-b")

        redis = AsyncMock()
        redis.mget = AsyncMock(return_value=[b"1", None, None])  # throttled
        selector = AccountSelector(redis, [acc_a, acc_b])
        result = await selector.pick("anthropic")
        assert result is None


# ─── AccountSelector — preferred_config_ids ───────────────────────────────────

class TestPreferredConfigIds:
    @pytest.mark.asyncio
    async def test_preferred_config_id_restricts_to_tagged_account(self) -> None:
        """When preferred_config_ids is set, pick the matching account first."""
        acc_realtime = LLMAccount(
            provider="anthropic", api_key="sk-realtime",
            rpm_limit=60, tpm_limit=100_000, config_id="gcfg_realtime",
        )
        acc_eval = LLMAccount(
            provider="anthropic", api_key="sk-eval",
            rpm_limit=60, tpm_limit=100_000, config_id="gcfg_evaluation",
        )

        redis = AsyncMock()
        redis.mget = AsyncMock(return_value=[None, b"0", b"0"])  # not throttled
        selector = AccountSelector(redis, [acc_realtime, acc_eval])
        result = await selector.pick("anthropic", preferred_config_ids=["gcfg_evaluation"])
        assert result == acc_eval.provider_key

    @pytest.mark.asyncio
    async def test_preferred_config_id_falls_back_when_preferred_throttled(self) -> None:
        """When preferred account is throttled, fall back to the full pool."""
        acc_realtime = LLMAccount(
            provider="anthropic", api_key="sk-realtime",
            rpm_limit=60, tpm_limit=100_000, config_id="gcfg_realtime",
        )
        acc_eval = LLMAccount(
            provider="anthropic", api_key="sk-eval",
            rpm_limit=60, tpm_limit=100_000, config_id="gcfg_evaluation",
        )

        redis = AsyncMock()

        async def mget_mock(*keys):
            """Responde por CHAVE, com o mesmo comprimento que foi pedido.

            O dublê anterior devolvia SEMPRE 3 valores e decidia o throttle olhando
            `str(args)`. Duas coisas quebravam nisso:

            · `AccountSelector` faz DOIS formatos de `mget` — `_is_available` pede
              3 chaves (throttled/rpm/tpm) e `_utilization` pede 2 (rpm/tpm). Três
              valores para duas variáveis estourava `ValueError: too many values to
              unpack`, e o teste reprovava por defeito do DUBLÊ, não do código. Só
              aparecia neste cenário porque é o único com DUAS contas a comparar no
              fallback — com uma só, `_utilization` nem é chamada.
            · `str(args)` mistura todas as chaves numa string, então "esta conta está
              throttled?" virava "alguma conta aparece aqui?" — a mesma classe de
              acoplamento por substring que quebrou os testes da analytics-api hoje.

            Aqui cada chave é respondida por si, e o comprimento acompanha o pedido.
            """
            out = []
            for k in keys:
                if k.endswith(":throttled"):
                    out.append(b"1" if acc_eval.key_id in k else None)
                else:
                    out.append(b"0")     # rpm/tpm zerados = longe do limite
            return out

        redis.mget = mget_mock
        selector = AccountSelector(redis, [acc_realtime, acc_eval])
        # Preferred account (eval) is throttled → should fall back to realtime
        result = await selector.pick("anthropic", preferred_config_ids=["gcfg_evaluation"])
        assert result == acc_realtime.provider_key

    @pytest.mark.asyncio
    async def test_empty_preferred_config_ids_uses_normal_selection(self) -> None:
        """Empty preferred_config_ids = normal rotation (no preference)."""
        acc = _make_account()
        redis = _make_redis(throttled=False, rpm=0, tpm=0)
        selector = AccountSelector(redis, [acc])
        result = await selector.pick("anthropic", preferred_config_ids=[])
        assert result == acc.provider_key


# ─── AccountSelector — mark_throttled() ───────────────────────────────────────

class TestMarkThrottled:
    @pytest.mark.asyncio
    async def test_sets_redis_key_with_ttl(self) -> None:
        acc = _make_account()
        redis = _make_redis()
        selector = AccountSelector(redis, [acc])
        await selector.mark_throttled(acc.provider_key, retry_after_seconds=120)
        expected_key = f"ai_gw:anthropic:{acc.key_id}:throttled"
        redis.set.assert_called_once_with(expected_key, "1", ex=120)

    @pytest.mark.asyncio
    async def test_default_retry_after(self) -> None:
        acc = _make_account()
        redis = _make_redis()
        selector = AccountSelector(redis, [acc])
        await selector.mark_throttled(acc.provider_key)
        redis.set.assert_called_once_with(
            f"ai_gw:anthropic:{acc.key_id}:throttled", "1", ex=60
        )


# ─── AccountSelector — record_usage() ────────────────────────────────────────

class TestRecordUsage:
    @pytest.mark.asyncio
    async def test_increments_rpm_always(self) -> None:
        acc = _make_account()
        redis = _make_redis()
        pipe = redis.pipeline.return_value
        selector = AccountSelector(redis, [acc])
        await selector.record_usage(acc.provider_key, tokens=0)
        # DOIS incr, e o segundo é de propósito: `record_usage` passou a ser o
        # ponto único que também registra o desfecho da credencial (o contador
        # de sucessos, testemunha do contador de erros). Era `assert_called_once`
        # até 2026-08-23; afrouxar para `assert_called` esconderia a diferença,
        # então o teste afirma QUAIS chaves foram incrementadas.
        incremented = [c.args[0] for c in pipe.incr.call_args_list]
        assert f"ai_gw:anthropic:{acc.key_id}:rpm" in incremented
        assert any(k.startswith(f"ai_gw:anthropic:{acc.key_id}:ok:") for k in incremented)
        assert len(incremented) == 2
        pipe.expire.assert_called()

    @pytest.mark.asyncio
    async def test_records_success_outcome(self) -> None:
        acc = _make_account()
        redis = _make_redis()
        pipe = redis.pipeline.return_value
        selector = AccountSelector(redis, [acc])
        await selector.record_usage(acc.provider_key, tokens=10)
        set_keys = [c.args[0] for c in pipe.set.call_args_list]
        assert f"ai_gw:anthropic:{acc.key_id}:last_ok" in set_keys

    @pytest.mark.asyncio
    async def test_increments_tpm_when_tokens_positive(self) -> None:
        acc = _make_account()
        redis = _make_redis()
        pipe = redis.pipeline.return_value
        selector = AccountSelector(redis, [acc])
        await selector.record_usage(acc.provider_key, tokens=500)
        pipe.incrby.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_tpm_when_tokens_zero(self) -> None:
        acc = _make_account()
        redis = _make_redis()
        pipe = redis.pipeline.return_value
        selector = AccountSelector(redis, [acc])
        await selector.record_usage(acc.provider_key, tokens=0)
        pipe.incrby.assert_not_called()


# ─── AccountSelector — credential_summary() ──────────────────────────────────
#
# Estes testes julgam a metade que o probe do demo NÃO julga. O `/v1/health` só é
# honesto se `unknown` nunca virar `ok` e se um 401 for distinguido de um 429, e
# nenhuma das duas coisas se prova contra um ambiente cuja credencial funciona.

def _make_credential_redis(
    last_ok: int | None,
    last_err: str | None,
    as_bytes: bool = True,
    throttled: bool = False,
) -> MagicMock:
    """
    Devolve os valores como BYTES por padrão, de propósito.

    O cliente de produção usa `decode_responses=True` (session.py) e entrega `str`,
    então o caminho vivo nunca exerce o ramo de bytes. Foi justamente por isso que
    `str(bytes)` — que corrompe sem levantar — passou despercebido até este teste
    reprovar. Manter a fixture em bytes fixa a robustez: se alguém trocar
    `_as_text()` por `str()` de novo, três testes ficam vermelhos na hora.
    """
    def enc(v: str):
        return v.encode() if as_bytes else v

    redis = MagicMock()
    redis.mget = AsyncMock(return_value=[
        enc(str(last_ok)) if last_ok else None,
        enc(last_err) if last_err else None,
        enc("1") if throttled else None,
    ])
    return redis


class TestCredentialSummary:
    @pytest.mark.asyncio
    async def test_no_outcome_is_unknown_never_ok(self) -> None:
        """A regra inteira em uma linha: sem evidência não existe conta saudável."""
        selector = AccountSelector(_make_credential_redis(None, None), [_make_account()])
        entry = (await selector.credential_summary())[0]
        assert entry["credentials"] == "unknown"

    @pytest.mark.asyncio
    async def test_auth_failure_is_invalid(self) -> None:
        redis = _make_credential_redis(None, "1750000000|status_401|API key is invalid")
        selector = AccountSelector(redis, [_make_account()])
        entry = (await selector.credential_summary())[0]
        assert entry["credentials"] == "invalid"
        assert entry["last_error_code"] == "status_401"
        assert "invalid" in entry["last_error"]

    @pytest.mark.asyncio
    async def test_transient_failure_is_error_not_invalid(self) -> None:
        """429 não é credencial ruim — se virasse `invalid`, um pico de tráfego
        reprovaria o healthcheck e o sinal perderia todo o valor."""
        redis = _make_credential_redis(None, "1750000000|rate_limit|429")
        selector = AccountSelector(redis, [_make_account()])
        assert (await selector.credential_summary())[0]["credentials"] == "error"

    @pytest.mark.asyncio
    async def test_success_after_failure_is_ok(self) -> None:
        redis = _make_credential_redis(1_750_000_100, "1750000000|status_401|old")
        selector = AccountSelector(redis, [_make_account()])
        assert (await selector.credential_summary())[0]["credentials"] == "ok"

    @pytest.mark.asyncio
    async def test_failure_after_success_wins(self) -> None:
        """Ordem importa: a chave que FUNCIONOU e depois foi revogada é inválida."""
        redis = _make_credential_redis(1_750_000_000, "1750000100|status_401|revoked")
        selector = AccountSelector(redis, [_make_account()])
        assert (await selector.credential_summary())[0]["credentials"] == "invalid"

    @pytest.mark.asyncio
    async def test_decoded_str_values_behave_identically(self) -> None:
        """O cliente de produção entrega `str`, o mock entrega `bytes`. As duas
        formas TÊM de dar o mesmo veredicto — se divergirem, o ambiente e a suíte
        param de julgar a mesma coisa e nenhum dos dois avisa."""
        for as_bytes in (True, False):
            redis = _make_credential_redis(
                None, "1750000000|status_401|API key is invalid", as_bytes=as_bytes,
            )
            selector = AccountSelector(redis, [_make_account()])
            entry = (await selector.credential_summary())[0]
            assert entry["credentials"] == "invalid", f"as_bytes={as_bytes}"
            assert entry["last_error_code"] == "status_401", f"as_bytes={as_bytes}"

    @pytest.mark.asyncio
    async def test_throttled_is_orthogonal_to_credentials(self) -> None:
        """Credencial boa + conta throttled = os DOIS fatos, não um só. Se o health
        lesse só `credentials`, todas as contas throttled dariam ok/200 enquanto o
        `pick()` devolve None — verde sobre capacidade zero."""
        redis = _make_credential_redis(1_750_000_000, None, throttled=True)
        entry = (await AccountSelector(redis, [_make_account()]).credential_summary())[0]
        assert entry["credentials"] == "ok"
        assert entry["throttled"] is True

    @pytest.mark.asyncio
    async def test_not_throttled_by_default(self) -> None:
        redis = _make_credential_redis(1_750_000_000, None)
        entry = (await AccountSelector(redis, [_make_account()]).credential_summary())[0]
        assert entry["throttled"] is False

    @pytest.mark.asyncio
    async def test_redis_unreachable_degrades_to_unknown(self) -> None:
        redis = MagicMock()
        redis.mget = AsyncMock(side_effect=RuntimeError("redis down"))
        selector = AccountSelector(redis, [_make_account()])
        assert (await selector.credential_summary())[0]["credentials"] == "unknown"

    @pytest.mark.asyncio
    async def test_no_accounts_returns_empty(self) -> None:
        selector = AccountSelector(_make_credential_redis(None, None), [])
        assert await selector.credential_summary() == []


# ─── AccountSelector — health_summary() ──────────────────────────────────────

class TestHealthSummary:
    @pytest.mark.asyncio
    async def test_returns_account_health_info(self) -> None:
        acc = _make_account()
        redis = _make_redis(throttled=False, rpm=15, tpm=5000)
        selector = AccountSelector(redis, [acc])
        summary = await selector.health_summary()
        assert "anthropic" in summary
        entries = summary["anthropic"]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["key_id"] == acc.key_id
        assert entry["throttled"] is False
        assert entry["rpm_current"] == 15
        assert entry["rpm_limit"] == 60
        assert entry["tpm_current"] == 5000
        assert entry["tpm_limit"] == 100_000

    @pytest.mark.asyncio
    async def test_throttled_account_shows_true(self) -> None:
        acc = _make_account()
        redis = _make_redis(throttled=True)
        selector = AccountSelector(redis, [acc])
        summary = await selector.health_summary()
        assert summary["anthropic"][0]["throttled"] is True

    @pytest.mark.asyncio
    async def test_empty_accounts_returns_empty_summary(self) -> None:
        redis = _make_redis()
        selector = AccountSelector(redis, [])
        summary = await selector.health_summary()
        assert summary == {}


# ─── AccountSelector — providers_for() ───────────────────────────────────────

class TestProvidersFor:
    def test_returns_all_provider_keys_for_provider(self) -> None:
        acc_a = LLMAccount(provider="anthropic", api_key="sk-key-a")
        acc_b = LLMAccount(provider="anthropic", api_key="sk-key-b")
        redis = _make_redis()
        selector = AccountSelector(redis, [acc_a, acc_b])
        keys = selector.providers_for("anthropic")
        assert len(keys) == 2
        assert acc_a.provider_key in keys
        assert acc_b.provider_key in keys

    def test_returns_empty_for_unknown_provider(self) -> None:
        redis = _make_redis()
        selector = AccountSelector(redis, [_make_account()])
        assert selector.providers_for("openai") == []


# ─── Settings helpers ─────────────────────────────────────────────────────────

class TestSettingsKeyParsing:
    def test_single_key(self) -> None:
        from plughub_ai_gateway.config import Settings
        s = Settings(anthropic_api_key="sk-single")
        assert s.get_anthropic_keys() == ["sk-single"]

    def test_comma_separated_keys_override_single(self) -> None:
        from plughub_ai_gateway.config import Settings
        s = Settings(anthropic_api_key="sk-old", anthropic_api_keys="sk-a,sk-b,sk-c")
        assert s.get_anthropic_keys() == ["sk-a", "sk-b", "sk-c"]

    def test_empty_key_returns_empty_list(self) -> None:
        from plughub_ai_gateway.config import Settings
        s = Settings(anthropic_api_key="", anthropic_api_keys="")
        assert s.get_anthropic_keys() == []

    def test_whitespace_stripped(self) -> None:
        from plughub_ai_gateway.config import Settings
        s = Settings(anthropic_api_keys="  sk-a , sk-b  ")
        assert s.get_anthropic_keys() == ["sk-a", "sk-b"]

    def test_openai_keys_parsing(self) -> None:
        from plughub_ai_gateway.config import Settings
        s = Settings(openai_api_keys="sk-oa,sk-ob")
        assert s.get_openai_keys() == ["sk-oa", "sk-ob"]

    def test_evaluation_profile_in_model_profiles(self) -> None:
        from plughub_ai_gateway.config import Settings
        s = Settings(anthropic_api_key="sk-test")
        profiles = s.model_profiles
        assert "evaluation" in profiles
        assert profiles["evaluation"].provider == "anthropic"

    def test_anthropic_config_ids_parsed_parallel_to_keys(self) -> None:
        from plughub_ai_gateway.config import Settings
        s = Settings(
            anthropic_api_keys="sk-a,sk-b,sk-c",
            anthropic_config_ids="gcfg_realtime,gcfg_evaluation,",
        )
        ids = s.get_anthropic_config_ids()
        assert ids == ["gcfg_realtime", "gcfg_evaluation", ""]

    def test_anthropic_config_ids_padded_when_shorter_than_keys(self) -> None:
        from plughub_ai_gateway.config import Settings
        s = Settings(
            anthropic_api_keys="sk-a,sk-b,sk-c",
            anthropic_config_ids="gcfg_only_one",
        )
        ids = s.get_anthropic_config_ids()
        assert len(ids) == 3
        assert ids[0] == "gcfg_only_one"
        assert ids[1] == ""
        assert ids[2] == ""

    def test_anthropic_config_ids_empty_when_not_set(self) -> None:
        from plughub_ai_gateway.config import Settings
        s = Settings(anthropic_api_keys="sk-a,sk-b")
        ids = s.get_anthropic_config_ids()
        assert ids == ["", ""]
