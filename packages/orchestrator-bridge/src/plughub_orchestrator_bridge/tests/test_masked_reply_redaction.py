"""test_masked_reply_redaction.py — a redação da resposta do cliente é UMA decisão.

Contexto (2026-08-29). A resposta de um step `menu` sai do bridge por cinco
destinos, e a decisão "o que deste texto pode aparecer aqui?" estava DUPLICADA:
dois destinos traziam a redação por campo, três testavam só `any_masked`
(o flag de STEP). Com masking declarado por CAMPO — `fields[].masked: true` sem
`masked` no step, que é `skill_auth_form_v1` no pool `auth_form_ia` — `any_masked`
é FALSO, e os três publicavam o texto CRU. Um deles é o Kafka
`conversations.events`, ou seja, `senha` e `codigo_2fa` no ClickHouse.

O que estes testes guardam:

  1. o VEREDICTO (`redact_customer_reply`), incluindo os ramos que só existem
     porque o masking é por campo;
  2. a AUSÊNCIA da forma antiga no fonte — é ela que reabre o vazamento, e
     reintroduzi-la num destino novo não quebraria nenhum teste de comportamento.

O (2) é o que faz este arquivo valer: sem ele, a próxima porta que alguém abrir
com `if not any_masked` passa verde.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from plughub_orchestrator_bridge.main import (
    _MASKED_SUPPRESSED,
    _MASKED_SUPPRESSED_HUMAN,
    redact_customer_reply,
)


# ── O caso que vazava ────────────────────────────────────────────────────────
# Form de `skill_auth_form_v1`: email em claro, senha e 2FA mascarados POR CAMPO.
# `any_masked` é False — é exatamente isto que os três destinos não olhavam.

FORM_REPLY = json.dumps(
    {"email": "cliente@exemplo.com", "senha": "hunter2", "codigo_2fa": "914455"}
)
FORM_MASKED_FIELDS = {"senha", "codigo_2fa"}


@pytest.mark.parametrize("decorate", [True, False])
def test_field_level_masking_redige_sem_any_masked(decorate: bool) -> None:
    """O ramo do vazamento: masking por campo, step-level ausente."""
    out, vis = redact_customer_reply(
        FORM_REPLY,
        msg_type          = "menu_result",
        any_masked        = False,          # ← o flag que os três destinos liam
        masked_fields     = FORM_MASKED_FIELDS,
        decorate_non_text = decorate,
    )
    assert "hunter2" not in out
    assert "914455" not in out
    # O que NÃO é mascarado sobrevive — é o ponto do masking por campo.
    assert "cliente@exemplo.com" in out
    assert vis == "all"


def test_step_level_suprime_tudo() -> None:
    out, vis = redact_customer_reply(
        "1234",
        msg_type      = "menu_result",
        any_masked    = True,
        masked_fields = set(),
    )
    assert out == _MASKED_SUPPRESSED
    assert vis == "agents_only"
    assert "1234" not in out


def test_placeholder_do_humano_e_o_declarado_pelo_chamador() -> None:
    out, _ = redact_customer_reply(
        "1234",
        msg_type        = "menu_result",
        any_masked      = True,
        masked_fields   = set(),
        suppressed_text = _MASKED_SUPPRESSED_HUMAN,
    )
    assert out == _MASKED_SUPPRESSED_HUMAN


def test_step_level_vence_field_level() -> None:
    """Precedência: com o step inteiro mascarado nada é redigido campo a campo."""
    out, vis = redact_customer_reply(
        FORM_REPLY,
        msg_type      = "menu_result",
        any_masked    = True,
        masked_fields = FORM_MASKED_FIELDS,
    )
    assert out == _MASKED_SUPPRESSED
    assert "cliente@exemplo.com" not in out   # nem o campo em claro escapa
    assert vis == "agents_only"


def test_payload_ilegivel_com_campo_mascarado_nao_cai_para_o_cru() -> None:
    """Parse falhou, mas há campo mascarado DECLARADO.

    O desconhecido aqui é o valor, não a política. Cair para `reply_text` seria
    o vazamento original com outra roupa.
    """
    out, _ = redact_customer_reply(
        "{isto nao e json: hunter2",
        msg_type      = "menu_result",
        any_masked    = False,
        masked_fields = FORM_MASKED_FIELDS,
    )
    assert "hunter2" not in out


def test_resposta_nao_dict_com_campo_mascarado_nao_vaza() -> None:
    out, _ = redact_customer_reply(
        json.dumps("hunter2"),
        msg_type      = "menu_result",
        any_masked    = False,
        masked_fields = FORM_MASKED_FIELDS,
    )
    assert "hunter2" not in out


# ── Testemunha de presença ───────────────────────────────────────────────────
# Sem estes, um redator que devolvesse "[entrada mascarada]" SEMPRE passaria em
# tudo acima. É o controle positivo: o caminho limpo tem de continuar limpo.

def test_sem_masking_o_texto_passa_intacto() -> None:
    out, vis = redact_customer_reply(
        "quero falar com um atendente",
        msg_type      = "text",
        any_masked    = False,
        masked_fields = set(),
    )
    assert out == "quero falar com um atendente"
    assert vis == "all"


def test_destino_de_dado_nao_decora() -> None:
    """`decorate_non_text=False` preserva o formato que analytics e `receive` consomem."""
    payload = json.dumps({"opcao": "sac"})
    out, _ = redact_customer_reply(
        payload,
        msg_type          = "menu_result",
        any_masked        = False,
        masked_fields     = set(),
        decorate_non_text = False,
    )
    assert out == payload          # sem "[Seleção: …]"


def test_destino_de_exibicao_decora() -> None:
    out, _ = redact_customer_reply(
        "sac",
        msg_type          = "menu_result",
        any_masked        = False,
        masked_fields     = set(),
        decorate_non_text = True,
    )
    assert out == "[Seleção: sac]"


# ── Guarda estrutural: a forma antiga não pode voltar ────────────────────────

_MAIN_PY = Path(__file__).resolve().parents[1] / "main.py"

# `reply_text if not any_masked else …` — a linha que decidia sem olhar
# `all_masked_fields`. Tolerante a espaçamento; deliberadamente ANCORADA em
# `reply_text` para não acusar usos legítimos de `any_masked` (logs, métricas).
_FORMA_ANTIGA = re.compile(r"reply_text\s+if\s+not\s+any_masked")


def test_a_forma_antiga_nao_existe_mais_no_fonte() -> None:
    """CÓDIGO, não comentário.

    O cabeçalho de `redact_customer_reply` cita a linha antiga textualmente para
    explicar o vazamento — e um contador ingênuo acusaria justamente a prosa que
    documenta a remoção, reproduzindo o número anterior ao conserto. Linha de
    comentário é excluída de propósito.
    """
    fonte = _MAIN_PY.read_text(encoding="utf-8")
    ocorrencias = [
        (i, ln.strip())
        for i, ln in enumerate(fonte.splitlines(), start=1)
        if _FORMA_ANTIGA.search(ln) and not ln.lstrip().startswith("#")
    ]
    assert not ocorrencias, (
        "Decisão de redação fora de `redact_customer_reply` — foi assim que "
        "senha e código 2FA chegaram ao ClickHouse:\n"
        + "\n".join(f"  main.py:{i}: {ln}" for i, ln in ocorrencias)
    )


def test_todo_destino_chama_o_redator() -> None:
    """Conta os call sites. Cinco destinos declarados na docstring do redator.

    Não afirma QUAIS são — afirma que ninguém removeu um. Se um destino novo
    aparecer, este número sobe DE PROPÓSITO, com o autor olhando para ele.
    """
    fonte = _MAIN_PY.read_text(encoding="utf-8")
    chamadas = len(re.findall(r"^\s*.*redact_customer_reply\(", fonte, flags=re.M))
    # 1 definição + 5 call sites
    assert chamadas == 6, (
        f"esperado 6 (1 def + 5 destinos), encontrado {chamadas} — "
        "um destino foi adicionado ou removido sem revisar a redação"
    )
