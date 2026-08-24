"""
test_sla_target_predicate.py — UM predicado para `sla_target_ms` (2026-08-24).

Até esta data o mesmo campo era derivado da mesma fonte em QUATRO lugares, com
QUATRO respostas. O gate abaixo tem duas metades que só julgam juntas:

  · a TABELA do predicado          → o veredicto está certo, caso a caso
  · a guarda ESTRUTURAL (AST)      → os quatro sites de fato o consultam, em vez de
                                     cada um refazer a conta

⚠️ **A linha que dá valor à tabela é a do `0`.** Um teste de "todos concordam" só
julga se a população contiver o caso em que eles DISCORDAVAM — e era exatamente
este: `kafka_listener` preservava o zero (`is not None`), `registry` o convertia em
480 000 (truthiness), e os dois de `main.py` o preservavam. Sem essa linha, a tabela
passaria idêntica sobre o código velho e não provaria nada.

⚠️ E a linha do valor VÁLIDO é a testemunha de presença: sem ela, um predicado que
recusasse tudo passaria em todas as outras.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from plughub_routing.models import SLA_TARGET_MS_FALLBACK, resolve_sla_target_ms


# ══ Metade 1: a tabela do veredicto ════════════════════════════════════════════

@pytest.mark.parametrize(
    "raw, expected, porque",
    [
        # TESTEMUNHA DE PRESENÇA — sem ela, um predicado que recusa tudo passa.
        (300_000, 300_000, "alvo válido atravessa intacto"),
        (15_000,   15_000, "o menor alvo real do parque (15 s) é válido"),

        # ══ O DISCRIMINADOR ══ os quatro sites divergiam AQUI, e só aqui.
        (0, None, "`0` não é alvo: o contrato Zod é `.positive()`"),

        (None,  None, "ausente — campo obrigatório no contrato"),
        (-1,    None, "negativo é não-positivo"),
        (-300_000, None, "negativo grande também"),

        # `int()` aceita string numérica, e o `registry.py` dependia disso
        # (`int(_sla_src or …)`). Preservar, ou a migração seria regressão silenciosa
        # para qualquer fonte que serialize número como texto.
        ("300000", 300_000, "string numérica preservada (era o comportamento do registry)"),
        ("0",      None,    "string numérica também passa pelo teste de positividade"),
        ("abc",    None,    "string ilegível não vira alvo"),

        # `isinstance(True, int)` é True em Python: sem guarda explícita, `true` no
        # JSON viraria um alvo de 1 ms — valor plausível produzido por tipo errado.
        (True,  None, "bool é int em Python, e 1 ms seria plausível demais"),
        (False, None, "idem, e cairia no ramo do zero"),

        ({}, None, "tipo estruturado não vira alvo"),
    ],
)
def test_veredicto_do_predicado(raw, expected, porque):
    assert resolve_sla_target_ms(raw, where="test", pool_id="p", tenant_id="t") == expected, porque


def test_nunca_devolve_zero():
    """
    Invariante do contrato de RETORNO, e não de um caso: `0` é o único valor cuja
    presença silenciosa causa dano em quatro consumidores de comportamento (aging
    no teto, `sla_urgency` sempre > 1, redirect+oncall na voz, ETA 0 ao cliente).
    Nenhuma entrada pode produzi-lo.
    """
    entradas = [0, "0", -0, False, None, -1, "abc", {}, [], 0.0, "0.0"]
    saidas   = [resolve_sla_target_ms(x, where="test") for x in entradas]
    assert all(s is None for s in saidas), (
        f"alguma entrada inválida produziu valor: {list(zip(entradas, saidas))}"
    )


def test_fallback_nao_mora_dentro_do_predicado():
    """
    O predicado responde *"há alvo?"*; ele NÃO fabrica um. Quem precisa de `int`
    aplica o `SLA_TARGET_MS_FALLBACK` no call site, para que a invenção apareça
    onde acontece.

    Reprova se alguém "simplificar" devolvendo o fallback daqui — que é o que
    tornaria os dois sites de `main.py` (ETA ao cliente, coluna de analytics)
    incapazes de dizer "não sei" de novo.
    """
    assert resolve_sla_target_ms(None, where="test") is not SLA_TARGET_MS_FALLBACK
    assert resolve_sla_target_ms(None, where="test") is None


def test_degradacao_nao_e_silenciosa(caplog):
    """
    § Postura de Engenharia: *"se um caminho degrada, ele loga POR QUE degradou"*.
    E os motivos têm de ser distinguíveis — "ausente" e "zero" chegam por caminhos
    diferentes (evento malformado × chave expirada) e pedem investigações
    diferentes.
    """
    import logging
    with caplog.at_level(logging.WARNING, logger="plughub.routing"):
        resolve_sla_target_ms(None, where="X", pool_id="p1", tenant_id="t1")
        resolve_sla_target_ms(0,    where="X", pool_id="p1", tenant_id="t1")

    # `getMessage()`, nunca `.message`: este último só existe depois de o handler
    # formatar, e lê-lo cru devolveria o template com os `%s` por interpolar — as
    # asserções de `p1`/`t1` passariam por acidente noutro caso e falhariam aqui.
    texto = "\n".join(r.getMessage() for r in caplog.records)
    assert "AUSENTE" in texto,      "ausência degradou sem dizer que era ausência"
    assert "NÃO-POSITIVO" in texto, "o zero degradou sem dizer que era o zero"
    assert "p1" in texto and "t1" in texto, "o log não diz de QUAL pool está falando"


# ══ Metade 2: a guarda estrutural ══════════════════════════════════════════════
#
# A tabela acima prova que o predicado está certo — não que alguém o use. Esta
# metade pergunta à AST, não ao texto: cada uma das quatro funções contém uma
# chamada a `resolve_sla_target_ms`?
#
# ⚠️ Por que AST e não `grep`: o docstring do próprio predicado LISTA os quatro
# sites pelo nome, e vários comentários citam `sla_target_ms`. Busca textual
# contaria a documentação da mudança como se fosse a mudança — o modo de falha que
# a memória deste repositório registra como *"grep conta a string, não a coisa"*.

_SRC = Path(__file__).resolve().parents[1]

_SITES = [
    ("kafka_listener.py", "_handle_pool_event"),
    ("registry.py",       "refresh_pool_snapshot"),
    ("main.py",           "_pool_sla_target"),
    ("main.py",           "_queue_position_and_eta"),
]


def _func_node(arquivo: str, funcao: str) -> ast.AST:
    caminho = _SRC / arquivo
    assert caminho.exists(), f"{caminho} não existe — teste medindo o lugar errado"
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    for no in ast.walk(arvore):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)) and no.name == funcao:
            return no
    pytest.fail(f"função {funcao} não encontrada em {arquivo} — renomeada?")


@pytest.mark.parametrize("arquivo, funcao", _SITES)
def test_site_consulta_o_predicado(arquivo, funcao):
    no = _func_node(arquivo, funcao)
    chamadas = [
        c for c in ast.walk(no)
        if isinstance(c, ast.Call)
        and isinstance(c.func, ast.Name)
        and c.func.id == "resolve_sla_target_ms"
    ]
    assert len(chamadas) == 1, (
        f"{arquivo}::{funcao} faz {len(chamadas)} chamadas ao predicado (esperado 1) — "
        f"ou voltou a decidir sozinho, ou decide duas vezes"
    )


@pytest.mark.parametrize("arquivo, funcao", _SITES)
def test_site_nao_tem_aritmetica_propria_de_fallback(arquivo, funcao):
    """
    Reprova se um site voltar a resolver o campo por conta própria — os dois
    padrões históricos eram `x if x is not None else FALLBACK` e `int(x or
    FALLBACK)`, e o que os separava era o tratamento do `0`.

    Aqui a pergunta é estreita de propósito: **`SLA_TARGET_MS_FALLBACK` não pode
    aparecer dentro de um `BoolOp`** (o `or`/`and` da truthiness). Aplicá-lo por
    `if … is None` continua permitido e é o que os dois sites de `PoolConfig`
    fazem — a fabricação é legítima ali; o que não é legítimo é decidi-la pela
    verdade do valor.
    """
    no = _func_node(arquivo, funcao)
    ofensas = [
        b for b in ast.walk(no)
        if isinstance(b, ast.BoolOp)
        and any(
            isinstance(v, ast.Name) and v.id == "SLA_TARGET_MS_FALLBACK"
            for v in ast.walk(b)
        )
    ]
    assert not ofensas, (
        f"{arquivo}::{funcao} aplica o FALLBACK por truthiness (`or`/`and`) — é "
        f"exatamente a divergência sobre o `0` que esta fatia fechou"
    )
