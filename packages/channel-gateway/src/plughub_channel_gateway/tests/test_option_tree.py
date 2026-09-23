"""A opção de menu vista do canal: uma linha, um nível (ORQ-15, ORQ-19)."""

from plughub_channel_gateway import option_tree
from plughub_channel_gateway.option_tree import (
    list_row,
    note_dropped_descriptions,
    option_description,
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


# ── ORQ-19 — o ramo de seções foi aposentado ─────────────────────────────────

def test_o_canal_nao_desenha_mais_arvore_ORQ19():
    """O `menu` leva UM nível. Se o achatador em seções voltar, ele volta a produzir
    linhas com id de CAMINHO que nenhum produtor vivo pede — remover a alternativa é
    o que não depende de memória."""
    for nome in ("flatten_to_sections", "is_tree", "tree_depth", "WA_MAX_ROWS", "WA_SECTION_TITLE_MAX"):
        assert not hasattr(option_tree, nome), nome

