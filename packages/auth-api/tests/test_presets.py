"""
test_presets.py — `build_module_config`: o preset de NASCIMENTO de um usuário.

POR QUE ESTE ARQUIVO NASCEU (outra mutação sobreviveu)
======================================================
No passo 5 da consolidação do verificador, `presets._RANK` — a **quarta** cópia da
tabela de rank no repositório — passou a ser `plughub_authz.ACCESS_RANK`. A bateria de
mutação plantou `if _RANK.get(acesso, 0) > _RANK.get(melhor, 0)` → `if False:` e **70
de 70 continuaram verdes**: a função não tinha teste nenhum.

Isso importa porque a regra que ela implementa é invariante declarado no CLAUDE.md —
*"múltiplos papéis rendem o MAIOR acesso por campo, nunca a interseção"* — e o modo de
falha de perder a ordenação é AUSÊNCIA: o usuário nasce com menos grant do que devia, e
o sintoma na tela é "o menu não mostra", que se atribui a qualquer outra coisa.

Existe um gate ponta-a-ponta (`infra/test/probe_role_preset_on_create.sh`), mas ele
precisa da stack de pé e cria usuário. Esta é a metade que roda sempre.
"""
from __future__ import annotations

import pytest

from plughub_auth_api.presets import build_module_config

# Um catálogo mínimo no formato de `auth.module_registry`.
MODULOS = [
    {
        "module_id": "contacts",
        "permission_schema": {
            "operacao": {
                "domain": ["none", "read_only", "read_write"],
                "role_defaults": {"operator": "read_only", "supervisor": "read_write"},
            },
            "visualizar": {
                "domain": ["none", "read_only"],
                "role_defaults": {"supervisor": "read_only"},
            },
            # Campo sem `role_defaults` para ninguém — não deve entrar.
            "exportar": {"domain": ["none", "read_write"]},
        },
    },
    {
        "module_id": "billing",
        "permission_schema": {
            "faturas": {
                "domain": ["none", "read_write"],
                "role_defaults": {"admin": "read_write"},
            },
        },
    },
]


def test_sem_papel_nao_nasce_com_nada():
    """Ausência é negação — e é o ramo que sustenta o grant-first na criação."""
    assert build_module_config([], MODULOS) == {}


def test_papel_unico_recebe_o_seu_default():
    out = build_module_config(["operator"], MODULOS)
    assert out == {"contacts": {"operacao": {"access": "read_only", "scope": []}}}


def test_multiplos_papeis_rendem_o_MAIOR_por_campo():
    """⚠️ O invariante do CLAUDE.md, e o que a mutação sobrevivente derrubava.

    `operator` dá `read_only` em `contacts.operacao`; `supervisor` dá `read_write`.
    A união por campo escolhe o maior — **nunca a interseção**, que daria `read_only` e
    faria um supervisor-também-operator nascer com menos do que um supervisor puro.
    """
    out = build_module_config(["operator", "supervisor"], MODULOS)
    assert out["contacts"]["operacao"]["access"] == "read_write"
    # E o campo que só o supervisor tem continua vindo:
    assert out["contacts"]["visualizar"]["access"] == "read_only"


def test_a_ordem_dos_papeis_nao_muda_o_resultado():
    """Se mudasse, o preset dependeria de como a lista foi montada — e ela vem do DB."""
    a = build_module_config(["operator", "supervisor"], MODULOS)
    b = build_module_config(["supervisor", "operator"], MODULOS)
    assert a == b


def test_papeis_de_modulos_diferentes_se_somam():
    out = build_module_config(["admin", "operator"], MODULOS)
    assert out["billing"]["faturas"]["access"] == "read_write"
    assert out["contacts"]["operacao"]["access"] == "read_only"


def test_campo_sem_role_defaults_NAO_entra():
    """Escrever `access: none` encheria o config de ruído que a tela teria de filtrar."""
    out = build_module_config(["operator", "supervisor", "admin"], MODULOS)
    assert "exportar" not in out["contacts"]


def test_papel_desconhecido_nao_inventa_grant():
    assert build_module_config(["papel_que_nao_existe"], MODULOS) == {}


def test_preset_fora_do_DOMAIN_e_recusado_ALTO_e_logado(caplog):
    """Declaração inválida no catálogo: o campo é ignorado, com ERROR nomeando-o.

    Deixar passar empurraria o 422 para a criação do usuário, longe da causa — e
    ignorar em silêncio faria o campo sumir do preset sem ninguém saber por quê.
    """
    catalogo = [
        {
            "module_id": "audit",
            "permission_schema": {
                "sessions": {
                    "domain": ["none", "read_only"],
                    "role_defaults": {"admin": "read_write"},   # fora do domain
                },
            },
        },
    ]
    with caplog.at_level("ERROR"):
        out = build_module_config(["admin"], catalogo)
    assert out == {}
    assert any("audit.sessions" in r.message or "sessions" in r.message for r in caplog.records)


@pytest.mark.parametrize(
    "modulo",
    [
        {"permission_schema": {"x": {"role_defaults": {"admin": "read_write"}}}},  # sem module_id
        {"module_id": "m", "permission_schema": "nao_e_dict"},
        {"module_id": "m", "permission_schema": {"x": "nao_e_dict"}},
        {"module_id": "m", "permission_schema": {"x": {"role_defaults": "nao_e_dict"}}},
    ],
)
def test_catalogo_malformado_nao_derruba_o_boot(modulo):
    """Linha estranha no registry é PULADA, nunca exceção: este código roda na criação
    de usuário, e falhar ali transformaria um catálogo torto em serviço quebrado."""
    assert build_module_config(["admin"], [modulo]) == {}


def test_scope_nasce_vazio_e_isso_e_o_EIXO_DE_ESCOPO_intocado():
    """Preset concede CAPACIDADE, nunca escopo. Os dois eixos são independentes — foi
    confundi-los que fez o claim `unrestricted` liberar o menu, em 2026-08-27."""
    out = build_module_config(["supervisor"], MODULOS)
    assert all(campo["scope"] == [] for campo in out["contacts"].values())
