"""
Testes do comparador declarado x gravado (D7 do arco ALLOWLIST).

Funcao pura, sem DB de proposito — e por isso estes testes rodam em qualquer
lugar. O que eles guardam:

  · a divergencia tem DUAS direcoes, e a que interessa a quem vai rodar
    `--overwrite` e a que some (`only_stored` + `changed`);
  · "igual" e `None`, nunca relatorio vazio — senao o caller trata "igual" e
    "diferente sem detalhe" pelo mesmo caminho;
  · nunca sai relatorio vazio dizendo que diverge.
"""
import pytest

from plughub_config_api.config_drift import (
    MAX_NAMED,
    canonical,
    describe_divergence,
)


# ── igual e IGUAL ────────────────────────────────────────────────────────────

def test_identico_devolve_none():
    v = {"a": 1, "b": [{"id": "x"}]}
    assert describe_divergence(v, dict(v)) is None


def test_ordem_de_chave_nao_e_divergencia():
    a = {"alfa": 1, "beta": 2}
    b = {"beta": 2, "alfa": 1}
    assert describe_divergence(a, b) is None


def test_ordem_de_item_com_identidade_nao_e_divergencia():
    # Reordenar uma lista de regras nao muda a politica; se contasse como
    # divergencia o log viraria ruido e ninguem o leria.
    a = {"rules": [{"id": "p"}, {"id": "q"}]}
    b = {"rules": [{"id": "q"}, {"id": "p"}]}
    assert describe_divergence(a, b) is None


# ── as duas direcoes ─────────────────────────────────────────────────────────

def test_item_so_no_declarado_e_acrescimo_nao_destrutivo():
    a = {"rules": [{"id": "p"}, {"id": "novo"}]}
    b = {"rules": [{"id": "p"}]}
    r = describe_divergence(a, b)
    assert r is not None
    assert r.only_declared == ["rules[novo]"]
    assert r.only_stored == []
    assert r.overwrite_would_drop == 0


def test_item_so_no_gravado_e_DESTRUTIVO():
    # Este e o caso que impede o seed de consertar sozinho: reaplicar apagaria
    # `session.cpf_titular`, que e regra real e que nenhum glob declarado cobre.
    a = {"rules": [{"id": "p"}]}
    b = {"rules": [{"id": "p"}, {"id": "session.cpf_titular"}]}
    r = describe_divergence(a, b)
    assert r is not None
    assert r.only_stored == ["rules[session.cpf_titular]"]
    assert r.overwrite_would_drop == 1


def test_bidirecional_conta_os_dois_lados():
    a = {"rules": [{"id": "p"}, {"id": "so_declarado"}]}
    b = {"rules": [{"id": "p"}, {"id": "so_gravado"}]}
    r = describe_divergence(a, b)
    assert r.only_declared == ["rules[so_declarado]"]
    assert r.only_stored == ["rules[so_gravado]"]
    assert r.total == 2
    assert r.overwrite_would_drop == 1


def test_campo_alterado_conta_como_perda():
    # Sobrescrever um valor alterado tambem DESCARTA o que estava la.
    a = {"rules": [{"id": "p", "type": "last_2"}]}
    b = {"rules": [{"id": "p", "type": "hidden"}]}
    r = describe_divergence(a, b)
    assert r.changed == ["rules[p].type"]
    assert r.overwrite_would_drop == 1


# ── identidade de item ───────────────────────────────────────────────────────

def test_identidade_por_pattern_quando_nao_ha_id():
    a = {"rules": [{"pattern": "*.cpf", "type": "last_2"}]}
    b = {"rules": []}
    r = describe_divergence(a, b)
    assert r.only_declared == ["rules[*.cpf]"]


def test_identidade_repetida_nao_e_usada():
    # Duas entradas com o mesmo `id` identificariam duas coisas como uma so —
    # uma delas sumiria do relatorio sem ninguem dizer. Sem identidade boa, a
    # lista vira um valor unico.
    a = {"rules": [{"id": "p", "v": 1}, {"id": "p", "v": 2}]}
    b = {"rules": [{"id": "p", "v": 1}]}
    r = describe_divergence(a, b)
    assert r.only_declared == [] and r.only_stored == []
    assert len(r.changed) == 1
    assert "lista" in r.changed[0]


def test_lista_de_escalares_e_um_valor_so():
    a = {"roles": ["supervisor", "admin"]}
    b = {"roles": ["supervisor"]}
    r = describe_divergence(a, b)
    assert len(r.changed) == 1
    assert "2 declarados × 1 gravados" in r.changed[0]


# ── nunca sai vazio, nunca trunca calado ─────────────────────────────────────

def test_tipo_incompativel_na_raiz_nao_sai_vazio():
    r = describe_divergence({"a": 1}, ["a"])
    assert r is not None
    assert r.total >= 1
    assert "raiz" in r.summary()


def test_diverge_implica_relatorio_nao_vazio():
    for a, b in [({"a": 1}, {"a": 2}), ([], {}), ("x", "y"), (1, 2)]:
        r = describe_divergence(a, b)
        assert r is not None and r.total >= 1, (a, b)


def test_excedente_e_contado_nunca_truncado_calado():
    n = MAX_NAMED + 7
    a = {"rules": [{"id": f"r{i}"} for i in range(n)]}
    b = {"rules": []}
    r = describe_divergence(a, b)
    s = r.summary()
    assert f"só no DECLARADO={n}" in s
    assert "+7 não nomeados" in s


# ── canonical ────────────────────────────────────────────────────────────────

def test_canonical_estavel_sob_ordem():
    assert canonical({"b": 1, "a": 2}) == canonical({"a": 2, "b": 1})


def test_aninhamento_profundo_nomeia_o_caminho():
    a = {"x": {"y": {"z": 1}}}
    b = {"x": {"y": {"z": 2}}}
    assert describe_divergence(a, b).changed == ["x.y.z"]


def test_lista_na_raiz_nomeia_o_item_sem_colchete_vazio():
    """`agent_activity.pause_reasons` E a lista — nao ha prefixo a citar.

    Antes desta correcao o rotulo saia `[[almoco]]`: colchete em volta de um
    caminho vazio, que nao diz de que lista o item e. Quem nomeia a lista e a
    linha do log (`DIVERGE agent_activity.pause_reasons`); aqui basta o item.
    Achado pelo gate `probe_seed_drift_named.sh`, que reprovou por isto.
    """
    a = [{"id": "intervalo", "label": "Intervalo"}, {"id": "almoco"}]
    b = [{"id": "intervalo", "label": "INJETADO"}, {"id": "probe_drift"}]
    r = describe_divergence(a, b)
    assert r.only_declared == ["almoco"]
    assert r.only_stored == ["probe_drift"]
    assert r.changed == ["intervalo.label"]
    assert "[[" not in r.summary()
