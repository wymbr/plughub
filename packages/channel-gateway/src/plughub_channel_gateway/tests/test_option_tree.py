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
    tree_depth,
)

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
