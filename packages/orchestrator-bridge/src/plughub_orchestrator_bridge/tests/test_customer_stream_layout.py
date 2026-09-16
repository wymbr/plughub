"""
test_customer_stream_layout.py — RPL-01: a mensagem do cliente chega ao avaliador de qualidade.

O avaliador recebe `ReplayContext.events`, montado pelo Stream Persister e pelo replayer, e os
dois leem `author` (JSON) e `payload` de cada entrada. O bridge gravava a mensagem do cliente só
com os campos flat (`author_role`, `content`): a linha persistida saía com autor nulo e payload
`{}`. Medido em 2026-09-16: das 252 sessões fechadas em 7 dias com mensagem, 4 íntegras.

A segunda metade é o fechamento: o `DEL` do stream corria contra o persister.
"""
from __future__ import annotations

import json

import pytest

import plughub_orchestrator_bridge.main as bridge_mod

# O layout que `writeStreamEntry` (mcp-server, `lib/write-stream-entry.ts`) grava. Se um dos
# lados mudar sem o outro, os leitores voltam a ver dois formatos.
WRITE_STREAM_ENTRY_FIELDS = {
    "event_id", "type", "timestamp", "author_id", "author_role", "author",
    "visibility", "segment_id", "payload",
}


def _entrada(**over):
    kw = dict(event_id="e1", timestamp="2026-09-16T12:00:00Z", author_id="cust_1",
              text="quero falar sobre minha fatura", visibility="all")
    kw.update(over)
    return bridge_mod.customer_message_stream_fields(**kw)


def _como_o_persister_le(fields: dict) -> dict:
    """O que `StreamPersister._parse_entries` faz com cada campo: tenta JSON, senão string."""
    out = {}
    for k, v in fields.items():
        try:
            out[k] = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            out[k] = v
    return {"author": out.get("author"), "payload": out.get("payload", {}),
            "visibility": out.get("visibility"), "type": out.get("type")}


class TestLayoutCanonico:
    def test_mesmos_campos_do_write_stream_entry(self):
        assert set(_entrada()) == WRITE_STREAM_ENTRY_FIELDS

    def test_o_persister_le_autor_cliente_e_texto(self):
        lido = _como_o_persister_le(_entrada())
        assert lido["type"] == "message"
        assert lido["author"]["role"] == "customer"
        assert lido["author"]["participant_id"] == "cust_1"
        assert lido["payload"]["content"] == {"type": "text", "text": "quero falar sobre minha fatura"}

    @pytest.mark.parametrize("vis", ["all", "agents_only", ["cust_1", "part_x"]])
    def test_visibilidade_volta_igual(self, vis):
        assert _como_o_persister_le(_entrada(visibility=vis))["visibility"] == vis

    @pytest.mark.parametrize("vis", ["all", ["cust_1"]])
    def test_todo_campo_e_string_e_visibilidade_e_json(self, vis):
        # O Redis só grava string; e `writeStreamEntry` serializa a visibilidade em JSON — a
        # emulação do leitor acima aceitaria `"all"` cru e uma lista Python, então não prova isto.
        f = _entrada(visibility=vis)
        assert all(isinstance(v, str) for v in f.values())
        assert f["visibility"] == json.dumps(vis)

    def test_texto_com_acento_nao_vira_escape(self):
        f = _entrada(text="não há saída")
        assert "não há saída" in f["payload"]

    def test_campos_flat_continuam_para_o_sse_do_supervisor(self):
        f = _entrada()
        assert f["author_role"] == "customer" and f["author_id"] == "cust_1"

    def test_texto_ja_redigido_e_o_que_viaja(self):
        # A função não decide mascaramento: grava o que recebeu.
        assert json.loads(_entrada(text="••••••")["payload"])["text"] == "••••••"


class _RedisFake:
    def __init__(self):
        self.expires: list[tuple[str, int]] = []
        self.deletes: list[tuple[str, ...]] = []

    async def expire(self, key, ttl):
        self.expires.append((key, ttl))

    async def delete(self, *keys):
        self.deletes.append(keys)


class TestFechamento:
    async def test_stream_ganha_carencia_e_nao_e_apagado(self):
        r = _RedisFake()
        await bridge_mod.retire_session_stream(r, "s1")
        assert r.expires == [("session:s1:stream", bridge_mod.STREAM_CLOSE_GRACE_S)]
        assert r.deletes == []

    def test_carencia_e_finita_e_cobre_o_persister(self):
        assert 600 <= bridge_mod.STREAM_CLOSE_GRACE_S <= 14_400
