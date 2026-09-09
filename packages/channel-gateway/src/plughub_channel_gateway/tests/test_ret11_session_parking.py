"""
test_ret11_session_parking.py — o registro DURÁVEL do parque, e a reidratação.

RET-11. O que estes casos protegem, e por quê:

  · o parque é gravado a partir do evento que JÁ existe (`session_suspended`),
    cobrindo os dois mecanismos (`suspend` e `collect`);
  · parque SEM `resume_token` (o caso `collect`, 49 das 212 encalhadas) vira LINHA
    contável, nunca silêncio;
  · o scanner reidrata no Redis o endereço que o Postgres ainda tem — e **não**
    sobrescreve o que está vivo;
  · falha ao ler o registro durável degrada BARULHENTO, mantendo o comportamento
    antigo em vez de derrubar a passada.

⚠️ Sem banco real: o pool é dublê. O que se testa aqui é a DECISÃO do adapter
(o que ele lê, o que reinsere, o que recusa), não o SQL — este é exercido pelo
gate de integração.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from plughub_channel_gateway import session_parking


VENCIDO = datetime.now(timezone.utc) - timedelta(hours=2)


class _RedisFalso:
    def __init__(self, hash_inicial: dict[str, str] | None = None):
        self.hashes: dict[str, dict[str, str]] = {}
        if hash_inicial:
            self.hashes["t1:resume_tokens"] = dict(hash_inicial)
        self.hsets: list[tuple[str, str, str]] = []

    async def hexists(self, chave: str, campo: str) -> bool:
        return campo in self.hashes.get(chave, {})

    async def hset(self, chave: str, campo: str, valor: str) -> int:
        self.hashes.setdefault(chave, {})[campo] = valor
        self.hsets.append((chave, campo, valor))
        return 1


class _PoolFalso:
    """Devolve as linhas combinadas; `explode` simula banco fora do ar."""

    def __init__(self, linhas: list[dict], explode: bool = False):
        self.linhas, self.explode = linhas, explode


async def _parques_vencidos_falso(pool, limit=200):
    if pool.explode:
        raise RuntimeError("banco fora do ar")
    return pool.linhas


def _adapter(redis, pool, monkeypatch):
    """Instância mínima: só os atributos que a reidratação toca."""
    from plughub_channel_gateway.adapters.webhook import WebhookAdapter
    a = WebhookAdapter.__new__(WebhookAdapter)
    a._redis   = redis
    a._db_pool = pool
    monkeypatch.setattr(session_parking, "parques_vencidos", _parques_vencidos_falso)
    return a


def _linha(session_id="s1", token="tk1", step="passo", exp=VENCIDO):
    return {"tenant_id": "t1", "session_id": session_id, "token": token,
            "step_id": step, "reason": "approval", "expires_at": exp}


# ── reidratação ───────────────────────────────────────────────────────────────

async def test_reidrata_o_endereco_que_sumiu_do_cache(monkeypatch):
    """O caso REAL: sessão viva, token perdido pelo TTL do hash compartilhado."""
    redis = _RedisFalso()                       # hash vazio: o token sumiu
    a = _adapter(redis, _PoolFalso([_linha()]), monkeypatch)

    n = await a._reidratar_parques_duraveis()

    assert n == 1
    assert redis.hashes["t1:resume_tokens"]["tk1"].startswith("s1:passo:")


async def test_NAO_sobrescreve_entrada_viva(monkeypatch):
    """
    Controle na direção oposta: sobrescrever trocaria o prazo CORRENTE — que pode
    ter sido legitimamente estendido — pelo do registro.
    """
    redis = _RedisFalso({"tk1": "s1:passo:2099-01-01T00:00:00+00:00"})
    a = _adapter(redis, _PoolFalso([_linha()]), monkeypatch)

    n = await a._reidratar_parques_duraveis()

    assert n == 0
    assert redis.hsets == []
    assert redis.hashes["t1:resume_tokens"]["tk1"].endswith("2099-01-01T00:00:00+00:00")


async def test_sem_pool_a_passada_segue_sem_explodir(monkeypatch):
    """Instalação sem Postgres continua varrendo o cache, como antes da RET-11."""
    a = _adapter(_RedisFalso(), None, monkeypatch)
    assert await a._reidratar_parques_duraveis() == 0


async def test_banco_fora_do_ar_degrada_BARULHENTO(monkeypatch, caplog):
    """
    Degradar em silêncio aqui devolveria o gate ao estado que a RET-11 fecha: a
    varredura seguiria só no cache e ninguém saberia por quê.
    """
    a = _adapter(_RedisFalso(), _PoolFalso([], explode=True), monkeypatch)
    with caplog.at_level(logging.WARNING):
        n = await a._reidratar_parques_duraveis()
    assert n == 0
    assert any("registro duravel" in r.getMessage() for r in caplog.records)


# ── o registro em si ──────────────────────────────────────────────────────────

def test_parque_sem_endereco_e_uma_LINHA_e_nao_silencio():
    """
    RET-12: o `collect` parqueia com `collect_token`, então o evento chega sem
    `resume_token`. A linha tem de nascer mesmo assim — invisível é pior que
    inendereçável, porque nem contar dá.
    """
    ddl = session_parking.DDL
    assert "token       text        NOT NULL DEFAULT ''" in ddl
    # e o índice de unicidade tem de ser PARCIAL, senão dois parques sem endereço
    # colidiriam entre si
    assert "WHERE token <> ''" in ddl


def test_o_scanner_so_pesca_parque_ENDERECAVEL():
    """
    Trazer parque sem token para a fila do scanner o faria tentar em laço algo que
    não tem caminho de volta.
    """
    import inspect
    src = inspect.getsource(session_parking.parques_vencidos)
    assert "token <> ''" in src
    assert "resolved_at IS NULL" in src


@pytest.mark.parametrize("payload,esperado", [
    ({"resume_expires_at": "2026-01-01T00:00:00+00:00"}, 2026),
    ({"expires_at":        "2027-01-01T00:00:00Z"},      2027),
    ({},                                                 None),
    ({"resume_expires_at": "nao-e-data"},                None),
])
def test_prazo_ausente_ou_ilegivel_e_None_nunca_agora(payload, esperado):
    """
    Um prazo inventado seria o "valor plausível": a linha pareceria ter deadline e
    o scanner a pescaria na hora errada.
    """
    r = session_parking._quando(payload, "resume_expires_at", "expires_at")
    assert (r.year if r else None) == esperado
