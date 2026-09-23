"""O canal desenha a árvore que couber — e RECUSA nomeando o resto.

⚠️ Os testes que carregam o peso aqui são os que devolvem `None`. Um achatador
que sempre devolvesse seções passaria em todo caso feliz e produziria, nos casos
recusados, uma mensagem que o WhatsApp REJEITA (mais de 10 linhas) ou uma árvore
de 3 níveis desenhada como se fosse de 2 — o cliente escolheria uma pasta
acreditando escolher um serviço.
"""

from plughub_channel_gateway.option_tree import (
    flatten_to_sections,
    is_tree,
    list_row,
    note_dropped_descriptions,
    option_description,
    tree_depth,
)


# ── ORQ-15 — a segunda linha ─────────────────────────────────────────────────

def test_option_description_so_texto_real():
    assert option_description({"description": "  cobre X  "}) == "cobre X"
    assert option_description({"description": "   "}) == ""
    assert option_description({"description": 7}) == ""
    assert option_description({}) == ""
    assert option_description("nao-dict") == ""


def test_list_row_sem_descricao_nao_tem_a_chave():
    assert list_row({"id": "a", "label": "A"}) == {"id": "a", "title": "A"}
    assert list_row({"id": "a", "label": "A", "description": "d"})["description"] == "d"


def test_descarte_conta_o_nivel_e_so_loga_quando_ha(caplog):
    opts = [{"id": "a", "description": "x"}, {"id": "b"}, {"id": "c", "description": "y",
            "options": [{"id": "f", "description": "filho nao conta"}]}]
    with caplog.at_level("INFO"):
        assert note_dropped_descriptions("sms", opts, "motivo", menu_id="m1") == 2
        assert note_dropped_descriptions("sms", [{"id": "z"}], "motivo") == 0
        assert note_dropped_descriptions("sms", None, "motivo") == 0
    linhas = [r.getMessage() for r in caplog.records]
    assert len(linhas) == 1
    assert "sms" in linhas[0] and "motivo" in linhas[0] and "m1" in linhas[0]

ARVORE = [
    {"id": "sac", "label": "SAC", "options": [
        {"id": "info_plano", "label": "Informações sobre o plano"},
        {"id": "status_servico", "label": "Status do serviço"},
    ]},
    {"id": "portabilidade", "label": "Portabilidade"},
    {"id": "reembolso", "label": "Reembolso"},
]

PLANA = [{"id": "sim", "label": "Sim"}, {"id": "nao", "label": "Não"}]


def test_is_tree_distingue():
    assert is_tree(ARVORE) is True
    assert is_tree(PLANA) is False
    assert is_tree(None) is False
    assert is_tree([]) is False


def test_profundidade():
    assert tree_depth(PLANA) == 1
    assert tree_depth(ARVORE) == 2
    fundo = [{"id": "a", "options": [{"id": "b", "options": [{"id": "c"}]}]}]
    assert tree_depth(fundo) == 3


def test_pasta_vira_secao_e_folha_carrega_o_CAMINHO():
    secoes = flatten_to_sections(ARVORE)
    assert secoes is not None
    assert secoes[0]["title"] == "SAC"
    # O id é o caminho, não a folha — sem isso o cursor do fluxo procuraria
    # `info_plano` na RAIZ, não acharia, e a navegação reiniciaria "com sucesso".
    assert [r["id"] for r in secoes[0]["rows"]] == ["sac.info_plano", "sac.status_servico"]
    # Folhas de primeiro nível ficam numa seção SEM título — inventar "Outros"
    # seria conteúdo que ninguém autorou.
    assert "title" not in secoes[1]
    assert [r["id"] for r in secoes[1]["rows"]] == ["portabilidade", "reembolso"]


def test_lista_plana_nao_e_agrupada():
    # Não é árvore: não há o que agrupar, e o caminho plano já serve.
    assert flatten_to_sections(PLANA) is None


def test_recusa_profundidade_3():
    # Seção dentro de seção não existe no WhatsApp. Desenhar assim entregaria
    # uma pasta com cara de serviço.
    fundo = [{"id": "a", "label": "A", "options": [
        {"id": "b", "label": "B", "options": [{"id": "c", "label": "C"}]},
    ]}]
    assert flatten_to_sections(fundo) is None


def test_recusa_acima_do_teto_de_linhas():
    # Acima de 10 o provider rejeita a MENSAGEM INTEIRA — o cliente não veria
    # menu nenhum. Recusar aqui devolve o caminho nível-a-nível, que funciona.
    grande = [{"id": "p", "label": "P", "options": [
        {"id": f"f{i}", "label": f"F{i}"} for i in range(11)
    ]}]
    assert flatten_to_sections(grande) is None
    # Controle POSITIVO: exatamente no teto, desenha.
    no_teto = [{"id": "p", "label": "P", "options": [
        {"id": f"f{i}", "label": f"F{i}"} for i in range(10)
    ]}]
    assert flatten_to_sections(no_teto) is not None


def test_trunca_titulo_mas_nunca_o_id():
    longo = [{"id": "pasta", "label": "P" * 40, "options": [
        {"id": "folha", "label": "L" * 40},
    ]}]
    secoes = flatten_to_sections(longo)
    assert secoes is not None
    assert len(secoes[0]["title"]) == 24
    assert len(secoes[0]["rows"][0]["title"]) == 24
    # O id é ENDEREÇO: truncá-lo mandaria o contato para outro lugar.
    assert secoes[0]["rows"][0]["id"] == "pasta.folha"
