"""
test_resume_terminal_once.py — Fase F do ADR
`adr-work-item-requeue-and-agent-affinity.md` (D7: resume terminal-uma-vez).

O QUE ESTA SUÍTE JULGA

Uma workflow suspensa tem sempre mais de um retomador possível — quem deveria
retomá-la e o scanner de prazo, que roda no MESMO event loop do endpoint HTTP.
Onde há item parqueado há um terceiro, o encerramento do supervisor. Os três
entram por `handle_resume`, e até a Fase F a janela entre o HGET do token e o
HDEL (com um round-trip HTTP ao árbitro no meio) deixava dois passarem juntos.

Três eixos, e o terceiro é o que a D7 pede de fato:

  1. EXCLUSÃO — exatamente um resume atravessa; o segundo é recusado, nunca
     aplicado em silêncio;
  2. NÃO-REGRESSÃO — o perdedor não consome o token, e o lock sai mesmo quando o
     corpo levanta (senão uma recusa legítima vira indisponibilidade de 45 s);
  3. NOME — a recusa diz QUEM encerrou e POR QUÊ. Antes, o agente cujo item o
     supervisor acabara de encerrar recebia "token não encontrado ou expirado" e
     concluía que a própria sessão tinha vencido. Recusa sobre causa errada não
     é recusa explícita.

O que faria cada teste FICAR VERMELHO está no docstring de cada um — a suíte só
vale se puder reprovar.

Sem I/O: Redis e Kafka são AsyncMock, salvo o teste de corrida real, que usa um
fake com semântica NX de verdade (um AsyncMock devolve truthy para os DOIS
chamadores e o teste passaria sem exclusão nenhuma).
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from plughub_channel_gateway.adapters.webhook import (
    ResumeAlreadyTerminalError,
    WebhookAdapter,
    _resume_actor,
    _terminal_cause,
)
from plughub_channel_gateway.config import Settings


TENANT_ID    = "tenant_test"
SESSION_ID   = "sid-term-001"
RESUME_TOKEN = "f" * 43
STEP_ID      = "aguardar_wrapup"
TOKEN_VALUE  = f"{SESSION_ID}:{STEP_ID}:2026-09-01T12:00:00+00:00"
LOCK_KEY     = f"{TENANT_ID}:resume_inflight:{RESUME_TOKEN}"
TERM_KEY     = f"{TENANT_ID}:resume_terminal:{RESUME_TOKEN}"


def _settings() -> Settings:
    return Settings(
        kafka_brokers            = "localhost:9092",
        kafka_group_id           = "test-group",
        kafka_topic_inbound      = "conversations.inbound",
        kafka_topic_outbound     = "conversations.outbound",
        kafka_topic_events       = "conversations.events",
        redis_url                = "redis://localhost:6379",
        tenant_id                = TENANT_ID,
        storage_root             = "/tmp/plughub_test",
        attachment_expiry_days   = 1,
        database_url             = "postgresql://plughub:plughub@localhost/plughub",
        webchat_serving_base_url = "http://localhost:8010/webchat/v1/attachments",
        webchat_upload_base_url  = "http://localhost:8010/webchat/v1/upload",
    )


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.hget   = AsyncMock(return_value=TOKEN_VALUE)
    redis.hdel   = AsyncMock(return_value=1)
    redis.get    = AsyncMock(return_value=None)
    redis.set    = AsyncMock(return_value=True)   # lock ganho, por default
    redis.delete = AsyncMock(return_value=1)
    redis.xadd   = AsyncMock(return_value=b"1-0")
    return redis


@pytest.fixture
def adapter(mock_redis):
    return WebhookAdapter(producer=AsyncMock(), redis=mock_redis, settings=_settings())


# ══ 1. `_terminal_cause` — a causa, das três que existem ═════════════════════
# Era expressão inline no meio do handle_resume e por isso nunca teve teste. A
# Fase F precisa dela em DOIS lugares (o encerramento no routing e o registro
# terminal), e duas cópias divergiriam: o item sairia gravado com um nome e
# recusado com outro.

@pytest.mark.parametrize("payload,expected", [
    ({},                                                        "task_done"),
    ({"decision": "input", "source": "agent"},                  "task_done"),
    ({"decision": "timeout", "source": "timeout_scanner"},      "acw_expired"),
    ({"decision": "timeout", "source": "supervisor:u-42"},      "acw_supervisor_closed"),
])
def test_terminal_cause(payload, expected):
    """
    Vermelho se alguém colapsar prazo e supervisor num valor só — que é
    justamente a pergunta que o supervisor faz ("quantos eu tive de limpar?") e
    que a série histórica não reprocessa depois.
    """
    assert _terminal_cause(payload) == expected


# ══ 2. `_resume_actor` — quem está encerrando ════════════════════════════════

@pytest.mark.parametrize("payload,approver,expected", [
    ({"source": "agent"},              {"decided_by": "alice"}, "human:alice"),
    ({"source": "supervisor:u-42"},    None,                    "supervisor:u-42"),
    ({},                               None,                    "external"),
])
def test_resume_actor(payload, approver, expected):
    """
    Vermelho se o ator puder sair vazio: um registro terminal sem autor recusa o
    segundo sem dizer quem foi o primeiro, que é metade do ponto da D7.
    """
    assert _resume_actor(payload, approver) == expected


# ══ 3. O lock — exclusão, e o que ele NÃO pode quebrar ══════════════════════

@pytest.mark.asyncio
async def test_lock_is_claimed_nx_and_released(adapter, mock_redis):
    """
    O caminho feliz: reivindica com NX e solta no fim.

    Vermelho se o lock virar um SET comum (sem `nx`) — que passaria a sobrescrever
    o lock do outro em vez de perder para ele, e a exclusão sumiria sem nada
    ficar diferente na tela.
    """
    sid = await adapter.handle_resume(resume_token=RESUME_TOKEN, tenant_id=TENANT_ID)

    assert sid == SESSION_ID
    lock_calls = [c for c in mock_redis.set.call_args_list if c.args[0] == LOCK_KEY]
    assert len(lock_calls) == 1
    assert lock_calls[0].kwargs.get("nx") is True
    assert lock_calls[0].kwargs.get("ex") == 45
    mock_redis.delete.assert_any_call(LOCK_KEY)


@pytest.mark.asyncio
async def test_lock_lost_refuses_with_holder(adapter, mock_redis):
    """
    O segundo a chegar recebe recusa NOMEADA, não 200 silencioso.

    Vermelho se o `handle_resume` voltar a tratar lock perdido como
    "segue mesmo assim" — o comportamento anterior à Fase F.
    """
    mock_redis.set.return_value = None            # NX não pegou: outro detém
    mock_redis.get.return_value = "supervisor:u-42"

    with pytest.raises(ResumeAlreadyTerminalError) as exc:
        await adapter.handle_resume(resume_token=RESUME_TOKEN, tenant_id=TENANT_ID)

    assert exc.value.state == "in_flight"
    assert exc.value.by    == "supervisor:u-42"
    assert exc.value.as_detail()["error"] == "resume_already_terminal"


@pytest.mark.asyncio
async def test_lock_lost_does_not_consume_token(adapter, mock_redis):
    """
    O perdedor NÃO pode consumir o token — senão a recusa dele apagaria o item
    que o vencedor ainda está processando, e a corrida voltaria pelo avesso.

    Vermelho se o HDEL escapar do caminho recusado.
    """
    mock_redis.set.return_value = None

    with pytest.raises(ResumeAlreadyTerminalError):
        await adapter.handle_resume(resume_token=RESUME_TOKEN, tenant_id=TENANT_ID)

    assert not [c for c in mock_redis.hdel.call_args_list
                if RESUME_TOKEN in [str(a) for a in c.args]]


@pytest.mark.asyncio
async def test_lock_released_even_when_body_raises(adapter, mock_redis):
    """
    O 403 do A5 (posse) sai por exceção. Se o lock não saísse no `finally`, uma
    recusa legítima deixaria o item travado por 45 s — recusa virando
    indisponibilidade.

    Vermelho se o `finally` for trocado por liberação no caminho de sucesso.
    """
    adapter._routing_work_task_holder = AsyncMock(
        return_value={"found": True, "instance_id": "human-bob",
                      "via": "record", "in_queue": False},
    )
    with pytest.raises(PermissionError):
        await adapter.handle_resume(
            resume_token      = RESUME_TOKEN,
            tenant_id         = TENANT_ID,
            payload           = {"answers": {}},
            approver          = {"decided_by": "alice", "verification_class": "possessed"},
            claim_pool_id     = "wrapup_detached_ia-int",
            claim_instance_id = "human-alice",
        )
    mock_redis.delete.assert_any_call(LOCK_KEY)


@pytest.mark.asyncio
async def test_lock_unavailable_degrades_permissive(adapter, mock_redis):
    """
    Redis fora no momento do lock não pode recusar resume legítimo — degrada
    permissivo, e o log diz por quê (a regra "degradação nunca é silenciosa").

    Vermelho se a falha do lock passar a abortar o resume: uma indisponibilidade
    de infra viraria perda de entrega de trabalho humano já feito.
    """
    mock_redis.set.side_effect = RuntimeError("redis down")

    sid = await adapter.handle_resume(resume_token=RESUME_TOKEN, tenant_id=TENANT_ID)
    assert sid == SESSION_ID


# ══ 4. O registro terminal — o NOME da recusa ═══════════════════════════════

@pytest.mark.asyncio
async def test_terminal_record_written_before_token_is_consumed(adapter, mock_redis):
    """
    Ordem load-bearing: entre o HDEL e a escrita do registro haveria um instante
    com o token já ausente e a causa ainda inexistente — e um resume caindo ali
    receberia o 404 antigo, a recusa sem nome que esta fase remove.

    Vermelho se alguém "simplificar" movendo a escrita para depois do HDEL.
    """
    await adapter.handle_resume(
        resume_token = RESUME_TOKEN,
        tenant_id    = TENANT_ID,
        payload      = {"decision": "timeout", "source": "supervisor:u-42"},
    )

    names = [c[0] for c in mock_redis.mock_calls]
    term_idx = next(i for i, c in enumerate(mock_redis.mock_calls)
                    if c[0] == "set" and c.args and c.args[0] == TERM_KEY)
    hdel_idx = next(i for i, n in enumerate(names) if n == "hdel")
    assert term_idx < hdel_idx, "registro terminal tem de ser gravado ANTES do consumo"

    payload = json.loads(
        [c for c in mock_redis.set.call_args_list if c.args[0] == TERM_KEY][0].args[1]
    )
    assert payload["cause"] == "acw_supervisor_closed"
    assert payload["by"]    == "supervisor:u-42"


@pytest.mark.asyncio
async def test_absent_token_with_terminal_record_refuses_with_cause(adapter, mock_redis):
    """
    O caso sequencial que MENTIA: supervisor encerra, agente clica em enviar.

    Vermelho se voltar a devolver 404/None — que é o mesmo código de "token nunca
    existiu" e faz a tela dizer ao agente que a sessão dele venceu.
    """
    mock_redis.hget.return_value = None
    mock_redis.get.return_value  = json.dumps({
        "session_id": SESSION_ID, "step_id": STEP_ID,
        "by": "supervisor:u-42", "cause": "acw_supervisor_closed",
        "at": "2026-08-04T14:32:00+00:00",
    })

    with pytest.raises(ResumeAlreadyTerminalError) as exc:
        await adapter.handle_resume(resume_token=RESUME_TOKEN, tenant_id=TENANT_ID)

    assert exc.value.state      == "terminal"
    assert exc.value.by         == "supervisor:u-42"
    assert exc.value.cause      == "acw_supervisor_closed"
    assert exc.value.session_id == SESSION_ID


@pytest.mark.asyncio
async def test_absent_token_without_record_still_404(adapter, mock_redis):
    """
    Ausência HONESTA continua 404. A Fase F separou duas causas que saíam com a
    mesma resposta; não pode passar a afirmar encerramento onde não houve.

    Vermelho se o 409 virar resposta genérica de token ausente.
    """
    mock_redis.hget.return_value = None
    mock_redis.get.return_value  = None

    assert await adapter.handle_resume(
        resume_token=RESUME_TOKEN, tenant_id=TENANT_ID,
    ) is None


# ══ 5. A corrida REAL — dois resumes no mesmo event loop ════════════════════

TOKENS_KEY = f"{TENANT_ID}:resume_tokens"


class _NxRedis:
    """
    Fake com semântica NX de verdade, e classe SIMPLES de propósito.

    Um AsyncMock devolveria truthy para os DOIS chamadores do `SET NX` e o teste
    de corrida passaria sem exclusão nenhuma — o teste-que-não-pode-reprovar que
    o TODO.md cataloga. E sem `__getattr__` genérico: um método não previsto tem
    de estourar AttributeError, não ser absorvido em silêncio.

    **Todo método CEDE o controle (`sleep(0)`) antes de agir, e isso é o teste.**
    Sem o yield, uma corrotina sem `await` real roda até o fim dentro do próprio
    passo do `gather`: a segunda encontraria o token já consumido e seria recusada
    pelo ramo SEQUENCIAL (`terminal`). O teste continuaria verde, continuaria
    reprovando contra o código antigo — e não teria exercitado a corrida uma única
    vez. O yield no `set` põe as duas dentro da janela ao mesmo tempo, que é a
    condição que a D7 descreve; daí a asserção poder exigir `state="in_flight"`.
    """

    def __init__(self) -> None:
        self._kv:     dict[str, str] = {}
        self._hashes: dict[str, dict[str, str]] = {
            TOKENS_KEY: {RESUME_TOKEN: TOKEN_VALUE},
        }

    async def set(self, key, value, nx=False, ex=None, keepttl=False):
        await asyncio.sleep(0)
        if nx and key in self._kv:
            return None
        self._kv[key] = value
        return True

    async def get(self, key):
        await asyncio.sleep(0)
        return self._kv.get(key)

    async def delete(self, key):
        await asyncio.sleep(0)
        return int(bool(self._kv.pop(key, None)))

    async def hget(self, hash_key, field):
        await asyncio.sleep(0)
        return self._hashes.get(hash_key, {}).get(field)

    async def hdel(self, hash_key, field):
        await asyncio.sleep(0)
        return int(bool(self._hashes.get(hash_key, {}).pop(field, None)))

    async def xadd(self, *_a, **_kw):
        await asyncio.sleep(0)
        return b"1-0"


@pytest.mark.asyncio
async def test_concurrent_resumes_exactly_one_wins():
    """
    A corrida da D7, construída: a entrega do agente e a expiração do prazo
    partindo juntas sobre o mesmo token.

    Vermelho contra o código anterior à Fase F, em que os DOIS retornavam o
    session_id e publicavam `session_resumed` — o fluxo seguindo o ramo de
    entrega e o `on_timeout` a partir do mesmo passo suspenso.
    """
    redis = _NxRedis()
    adapter = WebhookAdapter(producer=AsyncMock(), redis=redis, settings=_settings())

    results = await asyncio.gather(
        adapter.handle_resume(
            resume_token=RESUME_TOKEN, tenant_id=TENANT_ID,
            payload={"answers": {"disposition": "resolved"}, "source": "agent"},
        ),
        adapter.handle_resume(
            resume_token=RESUME_TOKEN, tenant_id=TENANT_ID,
            payload={"decision": "timeout", "source": "timeout_scanner"},
        ),
        return_exceptions=True,
    )

    winners = [r for r in results if r == SESSION_ID]
    refused = [r for r in results if isinstance(r, ResumeAlreadyTerminalError)]
    assert len(winners) == 1, f"esperado exatamente 1 vencedor, veio {results}"
    assert len(refused) == 1, f"esperada exatamente 1 recusa, veio {results}"
    # A recusa tem de ser pelo LOCK, não pelo token já consumido: se sair
    # `terminal`, as duas não estiveram na janela ao mesmo tempo e este teste
    # está medindo o caso sequencial com nome de corrida.
    assert refused[0].state == "in_flight", (
        f"a recusa saiu como {refused[0].state!r} — as corrotinas não se "
        "interlaçaram e a corrida NÃO foi exercitada"
    )
    # E o token foi consumido UMA vez: o perdedor não o apagou.
    assert RESUME_TOKEN not in redis._hashes[TOKENS_KEY]


# ══ 6. O scanner perder é o resultado CERTO ═════════════════════════════════

@pytest.mark.asyncio
async def test_scanner_swallows_already_terminal(adapter, mock_redis):
    """
    O prazo perder para uma entrega viva não é falha: o agente estava submetendo
    quando o prazo bateu, e a entrega vence o prazo.

    Vermelho se a exceção escapar do laço do scanner — uma varredura abortaria no
    primeiro token contencioso e os demais tokens vencidos daquela passada
    ficariam sem expirar.
    """
    mock_redis.scan_iter = lambda **_kw: _aiter([f"{TENANT_ID}:resume_tokens"])
    mock_redis.hgetall   = AsyncMock(return_value={RESUME_TOKEN: "sid:step:2000-01-01T00:00:00+00:00"})
    adapter.handle_resume = AsyncMock(
        side_effect=ResumeAlreadyTerminalError(state="in_flight", by="agent"),
    )

    await adapter._scan_expired_resume_tokens()   # não pode levantar
    adapter.handle_resume.assert_awaited_once()


async def _aiter(items):
    for i in items:
        yield i
