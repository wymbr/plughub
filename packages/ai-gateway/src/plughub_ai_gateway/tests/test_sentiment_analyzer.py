"""
test_sentiment_analyzer.py
Unit tests da MEDIÇÃO de sentimento (`sentiment_analyzer`), distinta da PUBLICAÇÃO
(`sentiment_emitter`, testada à parte).

A regra que estes testes existem para fixar: **nenhum caminho de falha produz 0.0.**
Zero é um ponto legítimo da escala (cliente neutro); usá-lo como default fez a
plataforma inteira parecer medida-e-neutra durante meses. Falhou ⇒ não escreve.

O provider é mockado com a forma REAL de `LLMResponse` (`.content`, sem `.text`),
pela mesma razão que a fixture do copiloto foi corrigida: um mock mais permissivo
que a coisa mockada é um teste que não pode reprovar.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ..providers.base import LLMResponse
from ..sentiment_analyzer import _parse_score, analyze_and_emit_sentiment

TENANT  = "tenant_demo"
SESSION = "sess-123"


def make_redis(pool_id: str | None = "retencao_humano") -> MagicMock:
    r = MagicMock()
    r.hget    = AsyncMock(return_value=pool_id)
    r.hgetall = AsyncMock(return_value={})
    r.hset    = AsyncMock(return_value=None)
    r.expire  = AsyncMock(return_value=None)
    return r


def make_provider(content: str) -> MagicMock:
    p = MagicMock()
    p.call = AsyncMock(return_value=LLMResponse(
        content=content, model_used="haiku", raw={}, stop_reason="end_turn",
    ))
    return p


# ── _parse_score ─────────────────────────────────────────────────────────────

class TestParseScore:
    def test_reads_plain_json(self):
        assert _parse_score('{"sentiment_score": -0.7}') == -0.7

    def test_reads_markdown_fenced_json(self):
        assert _parse_score('```json\n{"sentiment_score": 0.5}\n```') == 0.5

    def test_clamps_out_of_range(self):
        assert _parse_score('{"sentiment_score": -4.2}') == -1.0
        assert _parse_score('{"sentiment_score": 9.9}')  == 1.0

    def test_zero_is_a_real_value_not_a_failure(self):
        """0.0 é NEUTRO e deve sobreviver como medição — é justamente por ser
        indistinguível do default antigo que ele precisa de teste próprio."""
        assert _parse_score('{"sentiment_score": 0}') == 0.0

    def test_truncated_json_falls_back_to_number(self):
        assert _parse_score('{"sentiment_score": -0.3') == -0.3

    def test_unreadable_returns_none_never_zero(self):
        for text in ("", "desculpe, não entendi", "{}", '{"outro": 1}'):
            assert _parse_score(text) is None, f"texto={text!r} devolveu score"

    def test_out_of_scale_bare_number_is_not_accepted(self):
        """Um número fora de [-1,1] solto no texto não é score — é outra coisa
        (contagem, ano, id). Aceitá-lo produziria medição fabricada."""
        assert _parse_score("o cliente mencionou 2024 vezes") is None

    def test_number_from_another_field_is_not_a_score(self):
        """Regressão de 2026-08-23: o fallback numérico pescava o primeiro número de
        qualquer texto, então `{"outro": 1}` — JSON válido SEM o campo — virava 1.0,
        'cliente encantado' fabricado a partir de dado alheio. O fallback agora só
        procura DEPOIS da chave `sentiment_score`."""
        assert _parse_score('{"outro": 1}') is None
        assert _parse_score('{"confidence": 0.9, "intent": "billing"}') is None
        # …e o caso que o fallback existe para servir continua funcionando:
        assert _parse_score('{"sentiment_score": -0.3') == -0.3


# ── analyze_and_emit_sentiment ───────────────────────────────────────────────

class TestAnalyzeAndEmit:
    @pytest.mark.asyncio
    async def test_writes_context_store_on_success(self):
        redis = make_redis()
        await analyze_and_emit_sentiment(
            redis=redis, provider=make_provider('{"sentiment_score": -0.6}'),
            producer=None, tenant_id=TENANT, session_id=SESSION,
            customer_utterance="Isso é um absurdo!", model_id="haiku",
        )
        redis.hset.assert_called()
        key = redis.hset.call_args.args[0]
        assert key == f"{TENANT}:ctx:{SESSION}"

    @pytest.mark.asyncio
    async def test_model_failure_writes_nothing(self):
        """O caso que mais importa: falha do modelo não pode virar 'cliente neutro'."""
        redis = make_redis()
        provider = MagicMock()
        provider.call = AsyncMock(side_effect=RuntimeError("401 invalid api key"))
        await analyze_and_emit_sentiment(
            redis=redis, provider=provider, producer=None,
            tenant_id=TENANT, session_id=SESSION,
            customer_utterance="Estou irritado", model_id="haiku",
        )
        redis.hset.assert_not_called()

    @pytest.mark.asyncio
    async def test_unreadable_response_writes_nothing(self):
        redis = make_redis()
        await analyze_and_emit_sentiment(
            redis=redis, provider=make_provider("não sei responder"),
            producer=None, tenant_id=TENANT, session_id=SESSION,
            customer_utterance="Oi", model_id="haiku",
        )
        redis.hset.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_tenant_is_refused(self):
        """Escrever sob tenant vazio é pior que não escrever: a chave existe, ninguém
        a encontra, e ela passa por 'há sentimento no sistema'."""
        redis = make_redis()
        await analyze_and_emit_sentiment(
            redis=redis, provider=make_provider('{"sentiment_score": 0.9}'),
            producer=None, tenant_id="", session_id=SESSION,
            customer_utterance="Obrigado!", model_id="haiku",
        )
        redis.hset.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_provider_writes_nothing(self):
        redis = make_redis()
        await analyze_and_emit_sentiment(
            redis=redis, provider=None, producer=None,
            tenant_id=TENANT, session_id=SESSION,
            customer_utterance="Oi", model_id="haiku",
        )
        redis.hset.assert_not_called()

    @pytest.mark.asyncio
    async def test_blank_utterance_never_calls_model(self):
        redis    = make_redis()
        provider = make_provider('{"sentiment_score": 0.1}')
        await analyze_and_emit_sentiment(
            redis=redis, provider=provider, producer=None,
            tenant_id=TENANT, session_id=SESSION,
            customer_utterance="   ", model_id="haiku",
        )
        provider.call.assert_not_called()
        redis.hset.assert_not_called()

    @pytest.mark.asyncio
    async def test_call_respects_provider_signature(self):
        redis    = make_redis()
        provider = make_provider('{"sentiment_score": 0.0}')
        await analyze_and_emit_sentiment(
            redis=redis, provider=provider, producer=None,
            tenant_id=TENANT, session_id=SESSION,
            customer_utterance="tudo certo", model_id="haiku",
        )
        kwargs = provider.call.call_args.kwargs
        assert "system" not in kwargs
        assert [m["role"] for m in kwargs["messages"]] == ["system", "user"]

    @pytest.mark.asyncio
    async def test_missing_pool_id_still_writes_under_unknown(self):
        """Pool ausente degrada para 'unknown' — mas o sentimento é gravado. Perder a
        medição por falta de um rótulo de agregação seria trocar dado por metadado."""
        redis = make_redis(pool_id=None)
        await analyze_and_emit_sentiment(
            redis=redis, provider=make_provider('{"sentiment_score": -0.2}'),
            producer=None, tenant_id=TENANT, session_id=SESSION,
            customer_utterance="demorou muito", model_id="haiku",
        )
        redis.hset.assert_called()
