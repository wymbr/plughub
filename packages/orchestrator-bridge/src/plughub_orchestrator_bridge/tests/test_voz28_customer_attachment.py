"""
test_voz28_customer_attachment.py — VOZ-28: o anexo do cliente vira fato da conversa.

Medido em 2026-10-01 (gate `probe_voz28_call_upload`, ramo U3): o webchat publicava
`content.type == "media"` e o bridge o descartava como "Unknown content type" — o arquivo subia
e ficava guardado, e nenhum agente, IA, stream ou transcrição o via, com ou sem chamada.

Proposições, cada uma com o seu controle:
  1. o anexo chega aos QUATRO destinos de sempre (stream, humano, menu da IA, analytics) como um
     indicador legível, e o stream e o humano levam também `attachment` para ABRIR o arquivo;
  2. o NOME do arquivo mora só no indicador (que passa pela rede de texto livre), nunca no
     `attachment`;
  3. com step mascarado, o link não viaja;
  4. sem `file_id` não há anexo, e o descarte é dito.
"""
from __future__ import annotations

import json

import plughub_orchestrator_bridge.main as bridge_mod

from .test_wch11_fala_um_registro import SID, _Redis, producer  # noqa: F401 — fixture


def _media(**extra) -> dict:
    payload = {"media_type": "document", "file_id": "f-1", "file_name": "contrato.pdf",
               "mime_type": "application/pdf", "size_bytes": 1234,
               "url": "http://gw/webchat/v1/attachments/f-1", **extra}
    return {"session_id": SID, "contact_id": "c-1", "message_id": "m-1",
            "timestamp": "2026-10-01T10:00:00Z", "channel": "webchat",
            "author": {"type": "customer", "id": "c-1"},
            "content": {"type": "media", "payload": payload}}


def _stream_contents(r: _Redis) -> list[dict]:
    return [json.loads(f["payload"])["content"] for _, f in r.xadds]


class TestAnexoVira:
    async def test_ia_recebe_o_indicador_e_o_stream_leva_o_anexo(self, producer):
        r = _Redis()
        await bridge_mod.process_inbound(_media(caption="segue o contrato"), r)
        assert r.lpushes == [(f"menu:result:{SID}", "[Anexo: contrato.pdf] segue o contrato")]
        [c] = _stream_contents(r)
        assert c["text"] == "[Anexo: contrato.pdf] segue o contrato"
        assert c["attachment"] == {"media_type": "document", "file_id": "f-1",
                                   "mime_type": "application/pdf", "size_bytes": 1234,
                                   "url": "http://gw/webchat/v1/attachments/f-1"}
        assert "file_name" not in c["attachment"], "o nome mora so no indicador (passa pela rede)"
        msgs = [ev["content"] for t, ev in producer.sent if ev.get("event_type") == "message_sent"]
        assert msgs == ["[Anexo: contrato.pdf] segue o contrato"]

    async def test_humano_recebe_o_anexo_no_console(self, producer):
        r = _Redis(human=True)
        await bridge_mod.process_inbound(_media(), r)
        [(canal, data)] = r.publishes
        ev = json.loads(data)
        assert canal == f"agent:events:{SID}" and ev["text"] == "[Anexo: contrato.pdf]"
        assert ev["attachment"]["file_id"] == "f-1"
        assert _stream_contents(r)[0]["attachment"]["url"].endswith("/f-1")

    async def test_sem_nome_o_indicador_diz_o_tipo(self, producer):
        r = _Redis()
        msg = _media()
        del msg["content"]["payload"]["file_name"]
        msg["content"]["payload"]["media_type"] = "image"
        await bridge_mod.process_inbound(msg, r)
        assert r.lpushes[0][1] == "[Anexo: imagem]"

    async def test_step_mascarado_nao_leva_o_link(self, producer):
        r = _Redis()

        async def hgetall(key):
            return {"_default_": json.dumps({"visibility": "all", "masked": True})} if key.startswith("menu:waiting:") else {}
        r.hgetall = hgetall
        await bridge_mod.process_inbound(_media(), r)
        assert all("attachment" not in c for c in _stream_contents(r))

    async def test_sem_file_id_nao_ha_anexo_e_e_dito(self, producer, caplog):
        r = _Redis()
        msg = _media()
        msg["content"]["payload"]["file_id"] = ""
        await bridge_mod.process_inbound(msg, r)
        assert r.lpushes == [] and r.xadds == []
        assert "SEM file_id" in caplog.text

    async def test_controle_texto_nao_ganha_anexo(self, producer):
        """Sem este, um bridge que pusesse `attachment` em toda mensagem passaria."""
        r = _Redis()
        msg = _media()
        msg["content"] = {"type": "text", "text": "oi"}
        await bridge_mod.process_inbound(msg, r)
        assert _stream_contents(r) == [{"type": "text", "text": "oi"}]
