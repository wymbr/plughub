# -*- coding: utf-8 -*-
"""CTX-12 — o net-pass do webhook (`_mask_pii`) separa CPF cru de telefone pelo DV.

O validador é o de `plughub_contextstore.masking` (paridade com o TS em
`probe_detect_validator_parity.sh`); este arquivo prova a APLICAÇÃO dele aqui — o gate
não roda o `webhook.py`, e um `_mask_pii` que ignorasse o validador passaria por lá.

Os pares importam: sem o celular, uma regra de CPF que engolisse todo número de 11
dígitos passaria no primeiro caso.
"""
from plughub_channel_gateway.adapters.webhook import _mask_pii


def test_cpf_cru_com_dv_valido_vira_display_de_cpf():
    out = _mask_pii("CPF 52998224725")
    assert "52998224725" not in out
    # display de CPF (`preserve_last_digits=2`): 9 asteriscos + os 2 últimos dígitos
    assert out == "CPF *********25"


def test_celular_cru_segue_como_telefone():
    out = _mask_pii("cel 11987654321")
    assert "11987654321" not in out
    # display de telefone (`preserve_last_digits=4`)
    assert out == "cel *******4321"


def test_cpf_pontuado_continua_cpf_mesmo_com_dv_errado():
    """O formato pontuado identifica o CPF — o DV só desempata o cru."""
    assert _mask_pii("CPF 123.456.789-00") == "CPF *********00"


def test_testemunha_texto_sem_dado_fica_intacto():
    assert _mask_pii("nada sensível aqui") == "nada sensível aqui"
