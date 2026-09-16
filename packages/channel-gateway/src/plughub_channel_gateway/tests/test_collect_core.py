"""
tests/test_collect_core.py — a semântica da coleta por teclado e fala (VOZ-05 fatia 5b).

Cada regra com o seu controle: o desfecho que deveria sair, e o vizinho que NÃO deveria.
"""
from __future__ import annotations

import pytest

from plughub_channel_gateway.collect_core import (
    CollectNotApplicable,
    CollectPlan,
    CollectSession,
    Done,
    Echo,
    Retry,
)


def _menu(interaction="button", **collect) -> dict:
    c = {"input": ["dtmf", "voice"], "first_input_timeout_s": 5}
    c.update(collect)
    m = {"menu_id": "m1", "interaction": interaction, "prompt": "Como prefere receber a fatura?",
         "collect": c}
    if interaction in ("button", "list"):
        m["options"] = [{"id": "email", "label": "Email"}, {"id": "correio", "label": "Correio"}]
    return m


def _sess(menu: dict) -> CollectSession:
    s = CollectSession(CollectPlan.from_menu(menu))
    s.arm(0.0)
    return s


def _done(actions) -> Done | None:
    return next((a for a in actions if isinstance(a, Done)), None)


class TestPlano:
    def test_sem_collect_ou_so_texto_nao_e_coleta(self):
        m = _menu()
        m.pop("collect")
        assert CollectPlan.from_menu(m) is None
        assert CollectPlan.from_menu(_menu(input=["text"])) is None

    @pytest.mark.parametrize("interaction", ["checklist", "form"])
    def test_interacao_de_varios_valores_e_recusada_dita(self, interaction):
        with pytest.raises(CollectNotApplicable, match=interaction):
            CollectPlan.from_menu(_menu(interaction))

    def test_sem_prazo_e_recusada(self):
        with pytest.raises(CollectNotApplicable, match="first_input_timeout_s"):
            CollectPlan.from_menu(_menu(first_input_timeout_s=None))

    def test_prompt_falado_tem_tecla_e_fala_de_cada_opcao(self):
        texto = CollectPlan.from_menu(_menu()).spoken_prompt()
        assert texto.startswith("Como prefere receber a fatura?")
        assert "Para Email, tecle um ou diga Email." in texto
        assert "Para Correio, tecle dois ou diga Correio." in texto

    def test_prompt_so_teclado_nao_manda_dizer(self):
        texto = CollectPlan.from_menu(_menu(input=["dtmf"])).spoken_prompt()
        assert "tecle dois" in texto and "diga" not in texto

    def test_parametros_de_fala_que_o_stt_nao_aplica_sao_listados(self):
        plan = CollectPlan.from_menu(_menu(voice={"end_silence_ms": 500, "min_confidence": 0.5}))
        assert plan.ignored_params == ("end_silence_ms",)


class TestTeclaEmMenu:
    def test_tecla_da_opcao_e_o_valor_com_eco(self):
        acts = _sess(_menu()).digit("2", 1.0)
        assert Echo("2") in acts
        assert _done(acts) == Done("value", "correio", "dtmf")

    def test_tecla_fora_das_opcoes_e_invalida_e_por_default_ignorada_sem_mensagem(self):
        s = _sess(_menu())
        acts = s.digit("7", 1.0)
        assert Retry(1, None) in acts and _done(acts) is None

    def test_invalido_com_mensagem_e_limite(self):
        s = _sess(_menu(invalid_message="Opção inválida.", max_invalid=2))
        assert Retry(1, "Opção inválida.") in s.digit("9", 1.0)
        assert _done(s.digit("9", 2.0)) == Done("invalid")
        assert s.digit("1", 3.0) == []                     # terminou: nada mais conta

    def test_opcao_de_dois_digitos_espera_o_intervalo(self):
        m = _menu(inter_digit_timeout_s=2)
        m["options"] = [{"id": f"o{i}", "label": f"Opcao {i}"} for i in range(1, 12)]
        s = _sess(m)
        assert _done(s.digit("1", 1.0)) is None            # "1" ou "10"/"11"?
        assert s.tick(2.5) == []
        assert _done(s.tick(3.1)) == Done("value", "o1", "dtmf")
        s2 = _sess(m)
        s2.digit("1", 1.0)
        assert _done(s2.digit("1", 1.5)) == Done("value", "o11", "dtmf")

    def test_modo_sem_teclado_ignora_tecla(self):
        assert _sess(_menu(input=["voice"])).digit("1", 1.0) == []


class TestPrazo:
    def test_sem_entrada_vira_timeout(self):
        s = _sess(_menu())
        assert s.tick(4.9) == []
        assert _done(s.tick(5.0)) == Done("timeout")

    def test_prazo_so_corre_depois_de_armado(self):
        s = CollectSession(CollectPlan.from_menu(_menu()))
        assert s.tick(100.0) == []                          # prompt ainda tocando
        s.arm(100.0)
        assert _done(s.tick(105.0)) == Done("timeout")

    def test_invalido_recomeca_o_prazo(self):
        s = _sess(_menu())
        s.digit("9", 4.0)
        assert s.tick(8.9) == []
        assert _done(s.tick(9.0)) == Done("timeout")


class TestCampoAberto:
    def test_digitos_ate_o_terminador(self):
        s = _sess(_menu("text", input=["dtmf"], min_digits=3, max_digits=11, terminator="#"))
        for i, d in enumerate("12345"):
            s.digit(d, 1.0 + i)
        assert _done(s.digit("#", 7.0)) == Done("value", "12345", "dtmf")

    def test_curto_demais_e_invalido(self):
        s = _sess(_menu("text", input=["dtmf"], min_digits=3, terminator="#"))
        s.digit("1", 1.0)
        assert Retry(1, None) in s.digit("#", 2.0)

    def test_maximo_fecha_sozinho(self):
        s = _sess(_menu("text", input=["dtmf"], max_digits=3))
        s.digit("1", 1.0), s.digit("2", 1.1)
        assert _done(s.digit("3", 1.2)) == Done("value", "123", "dtmf")

    def test_intervalo_entre_digitos_fecha_a_entrada(self):
        s = _sess(_menu("text", input=["dtmf"], inter_digit_timeout_s=2, min_digits=2))
        s.digit("4", 1.0), s.digit("2", 1.5)
        assert s.tick(3.4) == []
        assert _done(s.tick(3.5)) == Done("value", "42", "dtmf")

    def test_asterisco_fora_do_dominio_e_invalido_e_dentro_e_valor(self):
        s = _sess(_menu("text", input=["dtmf"], max_digits=2))
        assert Retry(1, None) in s.digit("*", 1.0)
        s2 = _sess(_menu("text", input=["dtmf"], max_digits=2, domain="digits_star_hash"))
        s2.digit("*", 1.0)
        assert _done(s2.digit("1", 1.1)) == Done("value", "*1", "dtmf")


class TestFala:
    def test_rotulo_dito_escolhe_a_opcao(self):
        assert _done(_sess(_menu()).speech("pode ser por email", 1.0, 1.0)) == Done("value", "email", "voice")

    def test_tecla_dita_escolhe(self):
        assert _done(_sess(_menu()).speech("Opção dois.", 1.0, 1.0)) == Done("value", "correio", "voice")

    def test_frase_com_um_de_artigo_nao_e_a_opcao_um(self):
        acts = _sess(_menu()).speech("quero um boleto", 1.0, 1.0)
        assert _done(acts) is None and Retry(1, None) in acts

    def test_fala_que_casa_com_duas_opcoes_e_invalida(self):
        acts = _sess(_menu()).speech("email ou correio", 1.0, 1.0)
        assert _done(acts) is None and Retry(1, None) in acts

    def test_confianca_abaixo_do_minimo_e_invalida(self):
        s = _sess(_menu(voice={"min_confidence": 0.8}))
        assert _done(s.speech("email", 0.5, 1.0)) is None
        assert _done(s.speech("email", 0.9, 2.0)) == Done("value", "email", "voice")

    def test_modo_sem_fala_ignora(self):
        assert _sess(_menu(input=["dtmf"])).speech("email", 1.0, 1.0) == []

    def test_digitos_ditos_em_campo_numerico(self):
        s = _sess(_menu("text", input=["voice", "dtmf"], min_digits=4, max_digits=4))
        assert _done(s.speech("um dois, três e quatro", 1.0, 1.0)) == Done("value", "1234", "voice")
        s2 = _sess(_menu("text", input=["voice", "dtmf"], min_digits=4, max_digits=4))
        assert _done(s2.speech("não sei o número", 1.0, 1.0)) is None

    def test_campo_de_texto_por_fala_devolve_a_frase(self):
        s = _sess(_menu("text", input=["voice"]))
        assert _done(s.speech(" Rua das Flores, 10 ", 1.0, 1.0)) == Done("value", "Rua das Flores, 10", "voice")
