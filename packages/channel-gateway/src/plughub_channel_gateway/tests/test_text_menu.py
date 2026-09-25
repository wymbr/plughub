"""
test_text_menu.py — menu de ESCOLHA em SMS e WhatsApp (NIV-14/17): o que o canal numerou, o canal
traduz de volta para o id; o que não nomeia opção segue cru e o MOTOR recusa (NIV-13).

As proposições, cada uma com o controle ao lado:
  * TRADUÇÃO — "2", "dois", o rótulo e o id viram o id; "quero um boleto" e "5" num menu de 3 não;
    checklist "1, 3" / "1 e 3" vira a lista, e um pedaço inválido invalida a resposta inteira
    (sem inventar metade). "Boleto e PIX" é UMA opção, não duas.
  * SMS — o menu PLANO (o que o motor publica) é entregue; antes nada saía. A resposta traduzida
    vai como `menu_result` com o id; a não traduzida vai como texto. Campo de formulário guarda o
    id, nunca o `value`.
  * WHATSAPP — >10 e checklist viram texto numerado (antes: formulário de um campo / botões de
    escolha única); menu `text` é só o prompt (antes pedia "o número da opção"); o rótulo DIGITADO
    num menu de botões vira o id; o clique responde o menu em aberto.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from plughub_channel_gateway import text_menu
from plughub_channel_gateway.adapters.sms import SMSAdapter
from plughub_channel_gateway.adapters.sms_provider import MockSMSProvider
from plughub_channel_gateway.adapters.whatsapp import WhatsAppAdapter
from plughub_channel_gateway.adapters.whatsapp_provider import MockWhatsAppProvider
from plughub_channel_gateway.collect_core import options_from_menu
from plughub_channel_gateway.config import Settings

SID = "sid-niv17"
CONTATO = "+5511999990001"
OPTS = [{"id": "segunda_via", "label": "Segunda via"}, {"id": "pagamento", "label": "Pagamento"},
        {"id": "boleto_pix", "label": "Boleto e PIX"}]


class MemRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    async def get(self, k):
        return self.kv.get(k)

    async def setex(self, k, ttl, v):
        self.kv[k] = v
        return True

    async def set(self, k, v, **kw):
        self.kv[k] = v
        return True

    async def delete(self, *ks):
        return sum(1 for k in ks if self.kv.pop(k, None) is not None)

    async def expire(self, k, ttl):
        return k in self.kv


def _settings() -> Settings:
    return Settings(kafka_brokers="x", kafka_group_id="g", kafka_topic_inbound="conversations.inbound",
                    kafka_topic_outbound="o", kafka_topic_events="e", redis_url="redis://x",
                    tenant_id="tenant_test", storage_root="/tmp", database_url="postgresql://x/y",
                    webchat_serving_base_url="http://x", webchat_upload_base_url="http://x")


def _publicados(producer) -> list[dict]:
    out = []
    for c in producer.send.await_args_list:
        if c.args and c.args[0] == "conversations.inbound":
            out.append(json.loads(c.kwargs.get("value") or c.args[1]))
    return out


def _menu(interaction="list", opts=OPTS, **kw) -> dict:
    return {"type": "menu.payload", "contact_id": CONTATO, "session_id": SID, "menu_id": "m1",
            "interaction": interaction, "prompt": "Sobre o que e?", "options": opts, **kw}


# ── A tradução ───────────────────────────────────────────────────────────────

class TestResolve:
    O = options_from_menu(OPTS)

    @pytest.mark.parametrize("texto,esperado", [
        ("2", "pagamento"), (" dois ", "pagamento"), ("opção 2", "pagamento"),
        ("Segunda via", "segunda_via"), ("segunda_via", "segunda_via"), ("Boleto e PIX", "boleto_pix"),
    ])
    def test_nomeia_a_opcao(self, texto, esperado):
        assert text_menu.resolve(texto, self.O, "list") == esperado

    @pytest.mark.parametrize("texto", ["quero um boleto", "5", "", "   ", "1 2",
                                       "Segunda via ou Pagamento"])     # duas opções: ambíguo
    def test_controle_nao_nomeia(self, texto):
        assert text_menu.resolve(texto, self.O, "list") is None

    @pytest.mark.parametrize("texto,esperado", [
        ("1, 3", ["segunda_via", "boleto_pix"]), ("1 e 3", ["segunda_via", "boleto_pix"]),
        ("1 3", ["segunda_via", "boleto_pix"]), ("Boleto e PIX; 1", ["boleto_pix", "segunda_via"]),
        ("2, 2", ["pagamento"]),
    ])
    def test_checklist_vira_lista(self, texto, esperado):
        assert text_menu.resolve(texto, self.O, "checklist") == esperado

    def test_checklist_com_pedaco_invalido_nao_inventa_metade(self):
        assert text_menu.resolve("1, 9", self.O, "checklist") is None

    def test_render_numera_e_diz_como_responder(self):
        t = text_menu.render("Sobre o que e?", self.O, "list")
        assert "1. Segunda via" in t and "3. Boleto e PIX" in t and "número da opção" in t
        assert "separados por vírgula" in text_menu.render("x", self.O, "checklist")


# ── SMS ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def sms():
    prov, prod = MockSMSProvider(), AsyncMock()
    ad = SMSAdapter(producer=prod, redis=MemRedis(), settings=_settings(), provider=prov)
    assert hasattr(SMSAdapter, "_accumulate_parts") and hasattr(SMSAdapter, "_resolve_session")

    async def _acc(**kw):
        return kw["body"]

    async def _res(**kw):
        return SID, "tenant_test"
    ad._accumulate_parts, ad._resolve_session = _acc, _res
    return ad, prov, prod


async def _sms_chega(ad, texto):
    await ad._handle_inbound({"From": CONTATO, "Body": texto, "SmsSid": "SM1"})


class TestSMS:
    async def test_menu_plano_e_entregue_numerado(self, sms):
        ad, prov, _ = sms
        await ad.deliver_menu(_menu())
        [m] = prov.sent_messages
        assert "Sobre o que e?" in m["body"] and "2. Pagamento" in m["body"]

    async def test_numero_vira_menu_result_com_o_id(self, sms):
        ad, _, prod = sms
        await ad.deliver_menu(_menu())
        await _sms_chega(ad, "2")
        [ev] = _publicados(prod)
        assert ev["content"]["type"] == "menu_result"
        assert ev["content"]["payload"] == {"menu_id": "m1", "interaction": "list", "result": "pagamento"}

    async def test_controle_texto_que_nao_e_opcao_segue_cru(self, sms):
        ad, _, prod = sms
        await ad.deliver_menu(_menu())
        await _sms_chega(ad, "quero falar com alguem")
        [ev] = _publicados(prod)
        assert ev["content"] == {"type": "text", "text": "quero falar com alguem"} or \
            (ev["content"]["type"] == "text" and ev["content"]["text"] == "quero falar com alguem")

    async def test_respondido_o_menu_sai_de_aberto(self, sms):
        ad, _, prod = sms
        await ad.deliver_menu(_menu())
        await _sms_chega(ad, "2")
        await _sms_chega(ad, "1")                       # já não há menu: "1" é só texto
        tipos = [e["content"]["type"] for e in _publicados(prod)]
        assert tipos == ["menu_result", "text"]

    async def test_checklist(self, sms):
        ad, prov, prod = sms
        await ad.deliver_menu(_menu("checklist"))
        assert "separados por vírgula" in prov.sent_messages[0]["body"]
        await _sms_chega(ad, "1 e 3")
        assert _publicados(prod)[0]["content"]["payload"]["result"] == ["segunda_via", "boleto_pix"]

    async def test_menu_texto_e_so_o_prompt(self, sms):
        ad, prov, _ = sms
        await ad.deliver_menu(_menu("text", opts=[], prompt="Digite o CPF."))
        assert [m["body"] for m in prov.sent_messages] == ["Digite o CPF."]

    async def test_campo_de_formulario_guarda_o_id(self, sms):
        """Ramo sem entrada real (o schema do menu não declara `options` em campo) — guarda-se o
        comportamento pedido pela NIV-14 para quando houver: o id, nunca o `value`."""
        ad, _, prod = sms
        await ad.deliver_menu(_menu("form", opts=[], fields=[
            {"id": "assunto", "label": "Assunto", "options": [{"id": "fat", "label": "Fatura", "value": "F"}]}]))
        await _sms_chega(ad, "Fatura")
        [ev] = _publicados(prod)
        assert ev["content"]["payload"]["result"] == {"assunto": "fat"}

    async def test_sessao_fechada_esquece_o_menu(self, sms):
        ad, _, prod = sms
        await ad.deliver_menu(_menu())
        await ad.deliver_session_closed({"contact_id": CONTATO, "session_id": SID})
        await _sms_chega(ad, "2")
        assert _publicados(prod)[0]["content"]["type"] == "text"


# ── WhatsApp ─────────────────────────────────────────────────────────────────

@pytest.fixture
def wa():
    prov, prod = MockWhatsAppProvider(), AsyncMock()
    ad = WhatsAppAdapter(producer=prod, redis=MemRedis(), settings=_settings(), provider=prov)
    assert hasattr(WhatsAppAdapter, "_resolve_session") and hasattr(WhatsAppAdapter, "_record_arrival")

    async def _res(contact_id):
        return SID, "tenant_test", "pool_x"
    ad._resolve_session = _res
    ad._record_arrival = AsyncMock()
    return ad, prov, prod


async def _wa_texto(ad, texto):
    await ad._handle_message({}, {"from": CONTATO, "id": "wamid.1", "type": "text", "text": {"body": texto}})


async def _wa_clique(ad, rid, titulo):
    await ad._handle_message({}, {"from": CONTATO, "id": "wamid.2", "type": "interactive",
                                  "interactive": {"type": "button_reply", "button_reply": {"id": rid, "title": titulo}}})


class TestWhatsApp:
    async def test_mais_de_dez_e_texto_numerado_e_o_numero_vira_id(self, wa):
        ad, prov, prod = wa
        opts = [{"id": f"op_{i}", "label": f"Opcao {i}"} for i in range(1, 13)]
        await ad.deliver_menu(_menu("list", opts))
        assert prov.sent_messages[0]["type"] == "text" and "12. Opcao 12" in prov.sent_messages[0]["text"]
        await _wa_texto(ad, "12")
        [ev] = _publicados(prod)
        assert ev["content"]["type"] == "menu_result"
        assert ev["content"]["payload"]["result"] == "op_12", "nunca {'option': ...}"

    async def test_checklist_nao_vira_botao_de_escolha_unica(self, wa):
        ad, prov, prod = wa
        await ad.deliver_menu(_menu("checklist"))
        assert prov.sent_messages[0]["type"] == "text"
        await _wa_texto(ad, "2, 3")
        assert _publicados(prod)[0]["content"]["payload"]["result"] == ["pagamento", "boleto_pix"]

    async def test_menu_texto_e_so_o_prompt(self, wa):
        ad, prov, prod = wa
        await ad.deliver_menu(_menu("text", opts=[], prompt="Qual o seu CPF?"))
        [m] = prov.sent_messages
        assert m["type"] == "text" and m["text"] == "Qual o seu CPF?"
        await _wa_texto(ad, "12345678900")
        [ev] = _publicados(prod)
        assert ev["content"]["type"] == "text" and ev["content"]["text"] == "12345678900"

    async def test_rotulo_digitado_no_menu_de_botoes_vira_id(self, wa):
        ad, prov, prod = wa
        await ad.deliver_menu(_menu("button"))
        assert prov.sent_messages[0]["type"] == "interactive_buttons"
        await _wa_texto(ad, "pagamento")
        assert _publicados(prod)[0]["content"]["payload"]["result"] == "pagamento"

    async def test_clique_responde_o_menu_em_aberto(self, wa):
        ad, _, prod = wa
        await ad.deliver_menu(_menu("button"))
        await _wa_clique(ad, "boleto_pix", "Boleto e PIX")
        [ev] = _publicados(prod)
        assert ev["content"]["type"] == "menu_result" and ev["content"]["payload"]["result"] == "boleto_pix"

    async def test_controle_texto_livre_segue_cru(self, wa):
        ad, _, prod = wa
        await ad.deliver_menu(_menu("button"))
        await _wa_texto(ad, "nenhuma dessas")
        [ev] = _publicados(prod)
        assert ev["content"]["type"] == "text" and ev["content"]["text"] == "nenhuma dessas"

    async def test_sessao_fechada_esquece_o_menu(self, wa):
        ad, _, prod = wa
        await ad.deliver_menu(_menu())
        await ad.deliver_session_closed({"contact_id": CONTATO, "session_id": SID})
        await _wa_texto(ad, "2")
        assert _publicados(prod)[0]["content"]["type"] == "text"


# ── E-mail (NIV-15) ──────────────────────────────────────────────────────────

from plughub_channel_gateway.adapters.email import EmailAdapter  # noqa: E402
from plughub_channel_gateway.adapters.email_provider import MockEmailProvider, ParsedEmail  # noqa: E402

EMAIL = "cliente@exemplo.com"


@pytest.fixture
def mail():
    prov, prod = MockEmailProvider(), AsyncMock()
    ad = EmailAdapter(producer=prod, redis=MemRedis(), settings=_settings(), provider=prov)
    assert hasattr(EmailAdapter, "_resolve_session") and hasattr(EmailAdapter, "_store_attachments")

    async def _res(**kw):
        return SID, "tenant_test"

    async def _anexos(**kw):
        return []
    ad._resolve_session, ad._store_attachments = _res, _anexos
    return ad, prov, prod


async def _mail_chega(ad, prov, corpo):
    prov.load_inbound(ParsedEmail(message_id="<m1@x>", from_address=EMAIL, to_address="a@b", subject="Re: x",
                                  body_text=corpo, body_html="", in_reply_to=None, references=[]))
    await ad._handle_inbound({}, b"")


def _email_menu(interaction="list", opts=OPTS, **kw) -> dict:
    return {**_menu(interaction, opts, **kw), "contact_id": EMAIL}


class TestEmail:
    async def test_menu_plano_e_entregue_numerado(self, mail):
        ad, prov, _ = mail
        await ad.deliver_menu(_email_menu())
        [m] = prov.sent_messages
        assert "Sobre o que e?" in m["body_text"] and "2. Pagamento" in m["body_text"]

    async def test_numero_com_assinatura_vira_o_id(self, mail):
        """A resposta de e-mail traz assinatura: a primeira linha é a que responde."""
        ad, prov, prod = mail
        await ad.deliver_menu(_email_menu())
        await _mail_chega(ad, prov, "2\n\nEnviado do meu iPhone")
        [ev] = _publicados(prod)
        assert ev["content"]["type"] == "menu_result"
        assert ev["content"]["payload"] == {"menu_id": "m1", "interaction": "list", "result": "pagamento"}

    async def test_controle_texto_que_nao_e_opcao_segue_cru(self, mail):
        ad, prov, prod = mail
        await ad.deliver_menu(_email_menu())
        await _mail_chega(ad, prov, "Preciso de ajuda com outra coisa")
        [ev] = _publicados(prod)
        assert ev["content"]["type"] == "text" and "outra coisa" in ev["content"]["text"]

    async def test_menu_texto_e_so_o_prompt(self, mail):
        ad, prov, _ = mail
        await ad.deliver_menu(_email_menu("text", opts=[], prompt="Qual o numero do pedido?"))
        [m] = prov.sent_messages
        assert m["body_text"] == "Qual o numero do pedido?"

    async def test_formulario_campo_a_campo_publica_no_fim(self, mail):
        ad, prov, prod = mail
        await ad.deliver_menu(_email_menu("form", opts=[], prompt="Dados:", fields=[
            {"id": "nome", "label": "Seu nome?"}, {"id": "cidade", "label": "Sua cidade?"}]))
        assert "Seu nome?" in prov.sent_messages[0]["body_text"]
        await _mail_chega(ad, prov, "Ana")
        assert _publicados(prod) == [] and prov.sent_messages[-1]["body_text"] == "Sua cidade?"
        await _mail_chega(ad, prov, "Recife")
        [ev] = _publicados(prod)
        assert ev["content"]["type"] == "menu_result"
        assert ev["content"]["payload"] == {"menu_id": "m1", "interaction": "form",
                                            "result": {"nome": "Ana", "cidade": "Recife"}}

    async def test_sessao_fechada_esquece_menu_e_formulario(self, mail):
        ad, prov, prod = mail
        await ad.deliver_menu(_email_menu())
        await ad.deliver_session_closed({"contact_id": EMAIL, "session_id": SID})
        await _mail_chega(ad, prov, "2")
        assert _publicados(prod)[0]["content"]["type"] == "text"
