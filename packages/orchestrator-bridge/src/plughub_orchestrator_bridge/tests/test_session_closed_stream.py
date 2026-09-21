"""
VOZ-40 — o fim do contato vai ao stream canônico em TODO canal, não só quando há consumer group.

Medido em 2026-09-21: 637 sessões com mensagem em `session_stream_events` em 30 dias e zero com
`session_closed`. O XADD existia, mas só com `xinfo_groups` não vazio (despertador do external-mcp).
"""
from __future__ import annotations

import ast
import json
import pathlib

import pytest

import plughub_orchestrator_bridge.main as bridge_mod

SID = "sess-voz40"


class _Redis:
    def __init__(self, exists: bool = True, groups: list | None = None, falha: bool = False):
        self._exists = exists
        self._groups = groups or []
        self._falha = falha
        self.xadds: list[tuple[str, dict]] = []
        self.kv: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def get(self, key):
        return self.kv.get(key)

    async def delete(self, key):
        self.kv.pop(key, None)

    async def exists(self, key):
        return 1 if self._exists else 0

    async def xinfo_groups(self, key):
        return self._groups

    async def xadd(self, key, fields, **kw):
        if self._falha:
            raise RuntimeError("redis caiu")
        self.xadds.append((key, fields))


@pytest.mark.asyncio
async def test_escreve_sem_consumer_group():
    """O caso que faltava: stream sem grupo nenhum (webrtc, voice, webchat) recebe o fim."""
    r = _Redis(exists=True, groups=[])
    assert await bridge_mod.write_session_closed(r, SID, "client_disconnect") is True
    [(key, f)] = r.xadds
    assert key == f"session:{SID}:stream"
    assert f["type"] == "session_closed" and f["visibility"] == "agents_only"
    assert json.loads(f["payload"]) == {"close_reason": "client_disconnect"}
    assert f["reason"] == "client_disconnect"            # leitores antigos
    assert f["event_id"] and f["timestamp"]


@pytest.mark.asyncio
async def test_controle_com_consumer_group_continua_escrevendo():
    r = _Redis(exists=True, groups=[{"name": "ext-1"}])
    assert await bridge_mod.write_session_closed(r, SID, "agent_done") is True
    assert len(r.xadds) == 1


@pytest.mark.asyncio
async def test_stream_inexistente_nao_e_criado(caplog):
    """XADD criaria uma chave SEM TTL — o não-fato também tem de ser testado, e dito no log."""
    r = _Redis(exists=False)
    with caplog.at_level("INFO"):
        assert await bridge_mod.write_session_closed(r, SID, "timeout") is False
    assert r.xadds == []
    assert any("stream inexistente" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_falha_do_redis_e_dita_e_nao_derruba(caplog):
    r = _Redis(exists=True, falha=True)
    with caplog.at_level("WARNING"):
        assert await bridge_mod.write_session_closed(r, SID, "timeout") is False
    assert any("Could not XADD session_closed" in rec.message for rec in caplog.records)
    # o guarda saiu: o próximo eco, que consegue escrever, não é recusado
    r._falha = False
    assert await bridge_mod.write_session_closed(r, SID, "agent_done") is True


def test_o_fechamento_do_contato_chama_a_funcao_e_nao_ha_xadd_condicional_sobrando():
    """Censo por AST: `process_contact_event` chama `write_session_closed`, e nenhum outro XADD de
    `session_closed` ficou para trás no módulo (o condicional antigo reapareceria calado)."""
    src = pathlib.Path(bridge_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    alvo = next(n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "process_contact_event")
    chamadas = [n for n in ast.walk(alvo) if isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == "write_session_closed"]
    assert len(chamadas) == 1

    # E o fechamento da camada do contato escreve ANTES de anunciar no Kafka: o
    # `conversations.session_closed` dispara o Persister, que copia o stream (medido: com a ordem
    # inversa, o registro durável perdia o fim).
    camada = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "_close_contact_layer")
    escrita = [n.lineno for n in ast.walk(camada) if isinstance(n, ast.Call)
               and getattr(n.func, "id", None) == "write_session_closed"]
    anuncios = [n.lineno for n in ast.walk(camada) if isinstance(n, ast.Call)
                and getattr(n.func, "attr", None) == "send_and_wait"
                and n.args and isinstance(n.args[0], ast.Constant)
                and n.args[0].value == "conversations.session_closed"]
    assert len(escrita) == 1 and len(anuncios) == 1 and escrita[0] < anuncios[0], (escrita, anuncios)

    xadds_de_fim = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "xadd":
            for d in ast.walk(n):
                if isinstance(d, ast.Constant) and d.value == "session_closed":
                    xadds_de_fim.append(n.lineno)
    donos = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
             and n.name in ("write_session_closed", "_xadd_session_closed")]
    assert xadds_de_fim and all(any(d.lineno <= ln <= d.end_lineno for d in donos)
                                for ln in xadds_de_fim), xadds_de_fim


@pytest.mark.asyncio
async def test_um_fim_por_sessao_e_a_primeira_causa_vence(caplog):
    """Medido ao vivo: o cliente desliga (`client_disconnect`) e a plataforma fecha (`agent_done`) —
    dois `contact_closed` para o mesmo contato. O registro guarda UM, com a causa."""
    r = _Redis(exists=True)
    assert await bridge_mod.write_session_closed(r, SID, "client_disconnect") is True
    with caplog.at_level("INFO"):
        assert await bridge_mod.write_session_closed(r, SID, "agent_done") is False
    [(_, f)] = r.xadds
    assert json.loads(f["payload"])["close_reason"] == "client_disconnect"
    assert any("ja registrado" in rec.message for rec in caplog.records)
