"""
test_usage_emission.py — T1 do ADR de relatórios.

POR QUE ESTE ARQUIVO EXISTE
---------------------------
A T1 ligou a emissão de `usage.events` nos quatro caminhos vivos do gateway. Antes
de escrevê-lo, a suíte tinha 168 testes verdes — e **duas mutações sobreviveram a
todos**: apagar a emissão do `reason` e a do `sentiment` não pintou nada de vermelho.
Um verde que não pode reprovar compra confiança sem dar nada, e num produtor de
CUSTO isso é caro: o defeito não aparece na tela, aparece na fatura.

O que estes testes travam, por ordem de importância:

  1. cada caminho vivo EMITE, com o `source` certo;
  2. **as testemunhas negativas** — o que NÃO pode gerar linha. Um produtor que
     emite demais é tão errado quanto um que emite de menos, e o segundo caso só é
     descoberto por quem conta a população que *não* deveria ter linha
     (lição do `first_queued_ms`, que carimbava espera de 0 ms em todo contato
     roteado direto);
  3. o token é emitido MESMO quando o uso do resultado falha — a chamada foi paga.

⚠️ POR QUE A ESPERA NÃO É `asyncio.sleep(0)` — corrigido em 2026-08-30
---------------------------------------------------------------------
A primeira versão esperava a emissão com um `await asyncio.sleep(0)`, um yield só.
Isso é **adivinhar quantas suspensões a corrotina tem por dentro**, e a conta estava
errada: medido, `sources()` só enche a partir de **2** yields, e os DOIS eventos
(input e output) só a partir de **5**. Resultado — dois testes vermelhos com o
produto CERTO, e a leitura óbvia (*"a emissão não acontece"*) apontando para uma
regressão que não existia.

Pior que o vermelho: `test_sentiment_emite_com_source_sentiment` passava **por
acidente**, porque aquele caminho tem um `await resolve_session_pool_id` DEPOIS do
agendamento, e é ele que dava os turnos. Teste que passa pelo motivo errado é a
família que este repositório cataloga — ele teria continuado verde se a emissão
fosse removida e o `await` ficasse.

A espera passa a ser `drain_llm_token_emissions()`, que aguarda as tasks REAIS. O
set que a sustenta existe no produto por outra razão (referência forte contra GC de
task) — a determinismo do teste é consequência, não motivo.
"""
from __future__ import annotations

import asyncio
import json
import pytest

from ..usage_emitter import drain_llm_token_emissions, emit_llm_tokens


# ── Dublês ────────────────────────────────────────────────────────────────────

class FakeProducer:
    """Captura o que seria publicado. `send` é coroutine, como no aiokafka."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send(self, topic: str, value: bytes = b"", **_kw) -> None:
        self.sent.append((topic, json.loads(value.decode("utf-8"))))

    # Atalhos de leitura — SEMPRE filtrados por tópico.
    #
    # O caminho de sentimento publica em DOIS tópicos com o mesmo produtor
    # (`usage.events` e `sentiment.updated`), e os eventos têm formas diferentes.
    # A primeira versão deste dublê lia tudo e estourou `KeyError: 'metadata'` no
    # evento alheio — filtrar aqui é o que faz o teste medir o que ele diz medir.
    def events(self, dimension: str | None = None) -> list[dict]:
        out = [e for t, e in self.sent if t == "usage.events"]
        return [e for e in out if dimension is None or e["dimension"] == dimension]

    def sources(self) -> set[str]:
        return {e["metadata"].get("source") for e in self.events()}

    def usage_sent(self) -> list[dict]:
        return self.events()


class FakeResponse:
    def __init__(self, content: str = "", model: str = "m-1",
                 usage: dict | None = None) -> None:
        self.content = content
        self.model_used = model
        self.raw = {"usage": usage if usage is not None else
                    {"input_tokens": 10, "output_tokens": 3}}


class FakeProvider:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.calls = 0

    async def call(self, **_kw) -> FakeResponse:
        self.calls += 1
        return self._response


# ── 1. O emissor: contrato e guardas ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_emite_dois_eventos_input_e_output():
    p = FakeProducer()
    await emit_llm_tokens(
        producer=p, tenant_id="t1", session_id="s1", model_id="m",
        agent_type_id=None, input_tokens=100, output_tokens=20, source="reason",
    )
    assert len(p.events("llm_tokens_input")) == 1
    assert len(p.events("llm_tokens_output")) == 1
    assert p.events("llm_tokens_input")[0]["quantity"] == 100
    assert p.events("llm_tokens_output")[0]["quantity"] == 20
    assert p.sources() == {"reason"}


@pytest.mark.asyncio
async def test_source_e_obrigatorio():
    """Sem default: um caminho novo não consegue emitir sem se identificar."""
    p = FakeProducer()
    with pytest.raises(TypeError):
        await emit_llm_tokens(                       # type: ignore[call-arg]
            producer=p, tenant_id="t1", session_id="s1", model_id="m",
            agent_type_id=None, input_tokens=1, output_tokens=1,
        )


# ── TESTEMUNHAS NEGATIVAS — o que NÃO pode gerar linha ───────────────────────

@pytest.mark.asyncio
async def test_tenant_vazio_nao_emite():
    """
    `ReasonRequest.tenant_id` tem default `""`. Um evento sem tenant não é
    atribuível a ninguém: infla o total e não aparece em linha nenhuma.
    """
    p = FakeProducer()
    await emit_llm_tokens(
        producer=p, tenant_id="", session_id="s1", model_id="m",
        agent_type_id=None, input_tokens=10, output_tokens=2, source="reason",
    )
    assert p.usage_sent() == []


@pytest.mark.asyncio
async def test_zero_tokens_nao_emite():
    """Resposta em cache ou erro: nada foi gasto, nada deve ser contado."""
    p = FakeProducer()
    await emit_llm_tokens(
        producer=p, tenant_id="t1", session_id="s1", model_id="m",
        agent_type_id=None, input_tokens=0, output_tokens=0, source="reason",
    )
    assert p.usage_sent() == []


@pytest.mark.asyncio
async def test_apenas_output_emite_um_evento_so():
    p = FakeProducer()
    await emit_llm_tokens(
        producer=p, tenant_id="t1", session_id="s1", model_id="m",
        agent_type_id=None, input_tokens=0, output_tokens=7, source="reason",
    )
    assert len(p.usage_sent()) == 1
    assert p.events("llm_tokens_output")[0]["quantity"] == 7


@pytest.mark.asyncio
async def test_sem_produtor_nao_levanta():
    """Sem Kafka o gateway segue respondendo — a ausência é logada, não fatal."""
    await emit_llm_tokens(
        producer=None, tenant_id="t1", session_id="s1", model_id="m",
        agent_type_id=None, input_tokens=5, output_tokens=1, source="reason",
    )


@pytest.mark.asyncio
async def test_send_travado_nao_trava_o_chamador():
    """
    `producer.send` NÃO levanta com broker fora do ar — ele BLOQUEIA. Sem o
    `wait_for` interno, um broker morto viraria latência no caminho de LLM.
    """
    class HangingProducer:
        async def send(self, *_a, **_kw):
            await asyncio.sleep(3600)

    from .. import usage_emitter
    original, usage_emitter._SEND_TIMEOUT_S = usage_emitter._SEND_TIMEOUT_S, 0.05
    try:
        await asyncio.wait_for(
            emit_llm_tokens(
                producer=HangingProducer(), tenant_id="t1", session_id="s1",
                model_id="m", agent_type_id=None, input_tokens=1, output_tokens=1,
                source="reason",
            ),
            timeout=5.0,   # se o wait_for interno não existir, estoura aqui
        )
    finally:
        usage_emitter._SEND_TIMEOUT_S = original


# ── 1b. O agendador guarda REFERENCIA FORTE ──────────────────────────────────
#
# Sem estas duas asserções o `_IN_FLIGHT` é mecanismo que ninguém mede: alguém
# "simplifica" `schedule_llm_tokens` de volta para um `ensure_future` solto, os
# doze testes acima seguem verdes (porque `drain_...` teria um set sempre vazio e
# retornaria na hora), e a única evidência do defeito volta a ser a fatura.

@pytest.mark.asyncio
async def test_agendador_guarda_referencia_forte_ate_terminar():
    from .. import usage_emitter

    p = FakeProducer()
    task = usage_emitter.schedule_llm_tokens(
        producer=p, tenant_id="t1", session_id="s1", model_id="m",
        agent_type_id=None, input_tokens=1, output_tokens=1, source="reason",
    )
    # ANTES de terminar: o set e' a referencia forte que impede a coleta.
    assert task in usage_emitter._IN_FLIGHT

    await usage_emitter.drain_llm_token_emissions()

    # DEPOIS: sai do set — senao o set cresce sem teto num processo longo, que
    # trocaria um vazamento de metrica por um vazamento de memoria.
    assert task not in usage_emitter._IN_FLIGHT
    assert usage_emitter._IN_FLIGHT == set()
    assert p.sources() == {"reason"}


@pytest.mark.asyncio
async def test_drenar_com_nada_em_voo_nao_trava():
    """Testemunha negativa do proprio helper de espera."""
    from .. import usage_emitter

    assert usage_emitter._IN_FLIGHT == set()
    await asyncio.wait_for(usage_emitter.drain_llm_token_emissions(), timeout=1.0)


# ── 2. Caminho `reason` ───────────────────────────────────────────────────────

def _reason_engine(producer, response: FakeResponse):
    from ..reason import ReasonEngine

    class _Profile:
        provider = "anthropic"
        model_id = "m-1"

    return ReasonEngine(
        provider=FakeProvider(response),
        model_profiles={"balanced": _Profile()},
        kafka_producer=producer,
    )


def _reason_req(**over):
    from ..models import ReasonRequest, OutputFieldSchema
    base = dict(
        session_id="s1", tenant_id="t1", prompt_id="p1", input={},
        output_schema={"ok": OutputFieldSchema(type="boolean", required=True)},
    )
    base.update(over)
    return ReasonRequest(**base)


@pytest.mark.asyncio
async def test_reason_emite_com_source_reason():
    p = FakeProducer()
    eng = _reason_engine(p, FakeResponse(content='{"ok": true}',
                                         usage={"input_tokens": 42, "output_tokens": 7}))
    await eng.process(_reason_req())
    await drain_llm_token_emissions()

    assert p.sources() == {"reason"}
    assert p.events("llm_tokens_input")[0]["quantity"] == 42
    assert p.events("llm_tokens_output")[0]["quantity"] == 7
    assert p.events("llm_tokens_input")[0]["session_id"] == "s1"


@pytest.mark.asyncio
async def test_reason_com_tenant_vazio_nao_emite():
    """Testemunha negativa no caminho real, não só no emissor."""
    p = FakeProducer()
    eng = _reason_engine(p, FakeResponse(content='{"ok": true}'))
    await eng.process(_reason_req(tenant_id=""))
    await drain_llm_token_emissions()
    assert p.usage_sent() == []


# ── 3. Caminho `sentiment` — 42% das chamadas, sem rota própria ──────────────

@pytest.mark.asyncio
async def test_sentiment_emite_com_source_sentiment():
    from ..sentiment_analyzer import analyze_and_emit_sentiment

    p = FakeProducer()
    await analyze_and_emit_sentiment(
        redis=None,
        provider=FakeProvider(FakeResponse(content='{"sentiment_score": -0.5}',
                                           usage={"input_tokens": 30, "output_tokens": 5})),
        producer=p, tenant_id="t1", session_id="s1",
        customer_utterance="isso é inaceitável", model_id="haiku",
    )
    await drain_llm_token_emissions()
    assert "sentiment" in p.sources()
    assert p.events("llm_tokens_input")[0]["quantity"] == 30


@pytest.mark.asyncio
async def test_sentiment_emite_mesmo_com_resposta_ilegivel():
    """
    O token foi PAGO ainda que o parse falhe. Emitir depois do `if score is None`
    perderia toda chamada malsucedida — e o gasto some do relatório sem sumir da
    fatura.
    """
    from ..sentiment_analyzer import analyze_and_emit_sentiment

    p = FakeProducer()
    await analyze_and_emit_sentiment(
        redis=None,
        provider=FakeProvider(FakeResponse(content="isto não é JSON nenhum",
                                           usage={"input_tokens": 12, "output_tokens": 2})),
        producer=p, tenant_id="t1", session_id="s1",
        customer_utterance="oi", model_id="haiku",
    )
    await drain_llm_token_emissions()
    assert "sentiment" in p.sources()


@pytest.mark.asyncio
async def test_sentiment_sem_fala_do_cliente_nao_emite():
    """Sem `customer_utterance` não há chamada ao modelo — logo, não há consumo."""
    from ..sentiment_analyzer import analyze_and_emit_sentiment

    p = FakeProducer()
    await analyze_and_emit_sentiment(
        redis=None, provider=FakeProvider(FakeResponse()), producer=p,
        tenant_id="t1", session_id="s1", customer_utterance="", model_id="haiku",
    )
    assert p.usage_sent() == []
