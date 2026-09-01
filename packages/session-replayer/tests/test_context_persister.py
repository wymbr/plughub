"""
test_context_persister.py — F5 unit tests (sem infra).

O QUE ESTES TESTES PROTEGEM não é "grava a linha" — é a fronteira que a F5 desenhou:

  · **O persister NUNCA mascara.** O masking é do mcp-server e só dele. Uma segunda
    implementação divergiria em silêncio nos DOIS sentidos: de menos vaza PII, de
    mais é invisível (ninguém abre chamado por ver `***`).
  · **Sem credencial, RECUSA — nunca degrada para gravar cru.** Gravar o valor real
    por omissão de env criaria o cofre de PII que a R7 recusou, sem ninguém decidir.
  · **`hidden` é CONTADO, não omitido**, e o contador chega à coluna. Snapshot que
    esconde a própria incompletude faz o leitor concluir que a chamada não escreveu.
  · **A foto do PROCESSO é por SESSÃO-MEMBRO.** É a diferença entre fotos
    consecutivas que responde *"o que este contato acrescentou ao processo"*; uma
    linha por processo guardaria só a última e apagaria o sinal.
  · **Raiz ausente ⇒ nenhuma foto de processo.** Inventar a raiz penduraria a foto
    na journey errada, e nada ficaria vermelho.

Roda standalone:  python3 packages/session-replayer/tests/test_context_persister.py
Ou via pytest:    pytest packages/session-replayer/tests/test_context_persister.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))

from session_replayer.context_persister import ContextStorePersister  # noqa: E402


# ── Fakes ─────────────────────────────────────────────────────────────────────

class _AcquireCtx:
    def __init__(self, conn): self._conn = conn
    async def __aenter__(self): return self._conn
    async def __aexit__(self, *a): return False


class FakeConn:
    def __init__(self) -> None:
        self.executes: list[tuple] = []

    async def execute(self, sql, *args):
        self.executes.append((sql, args))
        return "INSERT 0 1"


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    def acquire(self):
        return _AcquireCtx(self._conn)


def _persister(conn: FakeConn, token: str = "tok") -> ContextStorePersister:
    return ContextStorePersister(FakePool(conn), "http://mcp:3100", token)


def _inserts(conn: FakeConn) -> list[tuple]:
    """Só os INSERT do snapshot — descarta o CREATE TABLE do ensure_schema."""
    return [e for e in conn.executes if "INSERT INTO session_context_snapshot" in e[0]]


MASKED_RESPONSE = {
    "session": {
        "entries": {
            "caller.cpf":  {"value": "***-00", "masked": True, "category": "last_2",
                            "source": "crm", "confidence": 0.9,
                            "updated_at": "2026-08-26T10:04:12Z"},
            "core.pool.id": {"value": "retencao_humano", "source": "routing_engine"},
            "caller.senha": {"value": None, "masked": True, "category": "hidden"},
        },
        "total": 3,
        "hidden_count": 1,
    },
    "journey": {
        "root": "root-abc",
        "entries": {"journey.limite_aprovado": {"value": "5000", "source": "flow"}},
        "total": 1,
        "hidden_count": 0,
    },
}


# ── Testes ────────────────────────────────────────────────────────────────────

async def test_writes_both_scopes():
    """Uma resposta, DUAS linhas: sessão e processo."""
    conn = FakeConn()
    p = _persister(conn)
    p._fetch_masked = lambda *_a, **_k: _coro(MASKED_RESPONSE)  # type: ignore[assignment]
    assert await p.persist("sess-1", "t1") is True

    ins = _inserts(conn)
    assert len(ins) == 2, f"esperado 2 inserts, houve {len(ins)}"
    scopes = {args[2]: args for _sql, args in ins}
    assert set(scopes) == {"session", "journey"}

    # scope_ref: a sessão aponta para si; o processo, para a RAIZ CANÔNICA.
    assert scopes["session"][3] == "sess-1"
    assert scopes["journey"][3] == "root-abc"
    # …mas as DUAS linhas são da mesma sessão-membro — é isso que dá N fotos por
    # processo, e é a diferença entre elas que carrega o sinal.
    assert scopes["session"][1] == "sess-1"
    assert scopes["journey"][1] == "sess-1"


async def test_counts_travel_to_the_columns():
    """`hidden_count` é a razão de a entrada oculta ser CONTADA e não omitida."""
    conn = FakeConn()
    p = _persister(conn)
    p._fetch_masked = lambda *_a, **_k: _coro(MASKED_RESPONSE)  # type: ignore[assignment]
    await p.persist("sess-1", "t1")

    sess = next(args for _s, args in _inserts(conn) if args[2] == "session")
    assert sess[5] == 3, "entry_count"
    assert sess[6] == 1, "hidden_count"
    # E a entrada oculta CONTINUA na linha, com valor nulo — nunca sumida.
    entries = json.loads(sess[4])
    assert "caller.senha" in entries
    assert entries["caller.senha"]["value"] is None


async def test_the_persister_never_masks():
    """O valor gravado é EXATAMENTE o que o mcp-server devolveu. Se um dia este
    teste falhar, alguém pôs regra de masking do lado Python — que é a segunda
    implementação que esta fatia existe para não criar."""
    conn = FakeConn()
    p = _persister(conn)
    p._fetch_masked = lambda *_a, **_k: _coro(MASKED_RESPONSE)  # type: ignore[assignment]
    await p.persist("sess-1", "t1")

    sess = next(args for _s, args in _inserts(conn) if args[2] == "session")
    entries = json.loads(sess[4])
    assert entries["caller.cpf"]["value"] == "***-00"
    assert entries["core.pool.id"]["value"] == "retencao_humano"


async def test_no_token_writes_nothing():
    """TESTEMUNHA NEGATIVA. Sem credencial não há chamada e não há linha — e,
    sobretudo, não há caminho que grave o valor cru."""
    conn = FakeConn()
    p = _persister(conn, token="")
    assert await p.persist("sess-1", "t1") is False
    assert _inserts(conn) == []


async def test_fetch_failure_writes_nothing():
    """503/timeout ⇒ nada gravado. Ausência de snapshot é honesta; snapshot
    parcial inventado não seria."""
    conn = FakeConn()
    p = _persister(conn)
    p._fetch_masked = lambda *_a, **_k: _coro(None)  # type: ignore[assignment]
    assert await p.persist("sess-1", "t1") is False
    assert _inserts(conn) == []


async def test_empty_entries_is_not_an_error():
    """ctx vazio (TTL vencido, contato sem contexto) não grava e não levanta."""
    conn = FakeConn()
    p = _persister(conn)
    p._fetch_masked = lambda *_a, **_k: _coro(  # type: ignore[assignment]
        {"session": {"entries": {}, "total": 0, "hidden_count": 0}}
    )
    assert await p.persist("sess-1", "t1") is False
    assert _inserts(conn) == []


async def test_journey_without_root_is_not_written():
    """Raiz ausente ⇒ nenhuma foto de processo. Pendurá-la numa raiz inventada
    afirmaria uma pertença que o dado não tem."""
    conn = FakeConn()
    p = _persister(conn)
    p._fetch_masked = lambda *_a, **_k: _coro({  # type: ignore[assignment]
        "session": MASKED_RESPONSE["session"],
        "journey": {"root": "", "entries": {"journey.x": {"value": "1"}},
                    "total": 1, "hidden_count": 0},
    })
    await p.persist("sess-1", "t1")
    assert [args[2] for _s, args in _inserts(conn)] == ["session"]


def _coro(value):
    async def _inner():
        return value
    return _inner()


# ── Runner standalone ─────────────────────────────────────────────────────────

def _main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            asyncio.run(t()) if asyncio.iscoroutinefunction(t) else t()
            print(f"  ✓ {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {t.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passaram")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
