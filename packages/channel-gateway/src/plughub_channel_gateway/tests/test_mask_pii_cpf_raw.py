# -*- coding: utf-8 -*-
"""CTX-12 — o net-pass do webhook (`_mask_pii`) separa CPF cru de telefone pelo DV.

O validador é o de `plughub_contextstore.masking` (paridade com o TS em
`probe_detect_validator_parity.sh`); este arquivo prova a APLICAÇÃO dele aqui — o gate
não roda o `webhook.py`, e um `_mask_pii` que ignorasse o validador passaria por lá.

Os pares importam: sem o celular, uma regra de CPF que engolisse todo número de 11
dígitos passaria no primeiro caso.

MSK-05 (2026-09-25): a exibição é a máscara do `operator` do catálogo que o chamador
passa (`last_2` no CPF, `last_4` no telefone, no semeado). Antes era a cópia Python do
`buildDisplay` (`*********25`). CPF e telefone continuam distinguíveis pela exibição.
"""
from plughub_channel_gateway.adapters.webhook import _mask_pii


def _cat(**por_id: str) -> dict:
    return {i: {"id": i, "mascara": {"by_role": {"operator": m}}} for i, m in por_id.items()}


SEMEADO = _cat(cpf="last_2", credit_card="last_4", phone="last_4", email_addr="email_domain")


def test_cpf_cru_com_dv_valido_vira_display_de_cpf():
    out = _mask_pii("CPF 52998224725", SEMEADO)
    assert "52998224725" not in out
    assert out == "CPF ***25"          # `last_2` — exibição de CPF


def test_celular_cru_segue_como_telefone():
    out = _mask_pii("cel 11987654321", SEMEADO)
    assert "11987654321" not in out
    assert out == "cel ***4321"        # `last_4` — exibição de telefone


def test_cpf_pontuado_continua_cpf_mesmo_com_dv_errado():
    """O formato pontuado identifica o CPF — o DV só desempata o cru."""
    assert _mask_pii("CPF 123.456.789-00", SEMEADO) == "CPF ***00"


def test_testemunha_texto_sem_dado_fica_intacto():
    assert _mask_pii("nada sensível aqui", SEMEADO) == "nada sensível aqui"


def test_msk05_a_exibicao_e_a_do_catalogo_recebido():
    """Trocar o `by_role` troca a exibição — é o catálogo que decide."""
    assert _mask_pii("CPF 52998224725", _cat(cpf="last_4")) == "CPF ***4725"


def test_msk05_catalogo_indisponivel_esconde_tudo():
    """`{}` é o que `get_masking_catalog` devolve quando não carrega: toda categoria vira
    `full`, e o valor não aparece. Esconder por não saber, nunca revelar."""
    out = _mask_pii("CPF 52998224725 cel 11987654321", {})
    assert out == "CPF *** cel ***"
