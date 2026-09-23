"""option_tree.py — a opção de menu vista do CANAL: uma linha, um nível.

── ORQ-19 (2026-09-23): o `menu` leva UM nível ──────────────────────────────────

Este módulo nasceu na F2 do orquestrador por árvore para desenhar uma árvore de
opções num turno só (seções tituladas no WhatsApp, grupos no widget). O ramo foi
APOSENTADO: ele nunca recebeu árvore pelo caminho vivo, porque o `notification_send`
removia os filhos antes de publicar. A árvore se percorre nível a nível pelo fluxo
(`dialog_tree_level`), e o `notification_send` agora NOMEIA o descarte quando alguém
entrega um menu com filhos (`oneLevelMenuOptions`).

Fica aqui o que o caminho plano usa: a linha da lista e a segunda linha da opção
(ORQ-15).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Tetos da lista interativa do WhatsApp (Cloud API). Não são preferência: acima
# deles o provider REJEITA a mensagem, e o cliente não veria menu nenhum.
WA_ROW_TITLE_MAX = 24
# ORQ-15 — é também o teto que o AUTOR tem (`DIALOG_OPTION_DESCRIPTION_MAX`), por
# isso o corte aqui só age sobre texto que não veio de um DialogForm validado.
WA_ROW_DESCRIPTION_MAX = 72


# ── ORQ-15 — a segunda linha da opção ────────────────────────────────────────
#
# A `description` chega pronta do `render` (resolvida na língua, sem espaço, e
# ausente em vez de `""`). Quem tem onde pô-la, põe; quem NÃO tem, descarta
# NOMEANDO. O descarte mudo é o que o achatamento de árvore do adapter já custou
# uma vez: o cliente vê um menu que parece completo, e ninguém sabe o que faltou.


def option_description(op: Any) -> str:
    """A segunda linha de uma opção, ou `""` quando não há."""
    if not isinstance(op, dict):
        return ""
    d = op.get("description")
    return d.strip() if isinstance(d, str) else ""


def note_dropped_descriptions(
    channel: str,
    options: Any,
    reason: str,
    *,
    session_id: str = "",
    menu_id: str = "",
) -> int:
    """Loga o descarte das descrições deste NÍVEL e devolve quantas eram.

    Só loga quando havia o que descartar — menu sem descrição não é descarte, e
    uma linha por menu comum afogaria a que importa. Conta o nível VISÍVEL, não a
    subárvore: filhos nunca chegam ao canal (ORQ-19 — o `notification_send` os
    descarta nomeando), e contá-los aqui seria dizer duas vezes a mesma perda.
    """
    if not isinstance(options, list):
        return 0
    n = sum(1 for o in options if option_description(o))
    if n:
        logger.info(
            "%s: descricao de %d opcao(oes) NAO exibida ao cliente — %s "
            "(ORQ-15) session=%s menu=%s",
            channel, n, reason, session_id, menu_id,
        )
    return n


def _row(rid: str, title: str, op: dict[str, Any], row_title_max: int) -> dict[str, Any]:
    """Linha de lista do WhatsApp, com a `description` quando a opção a tem."""
    row: dict[str, Any] = {"id": rid, "title": title[:row_title_max]}
    d = option_description(op)
    if d:
        row["description"] = d[:WA_ROW_DESCRIPTION_MAX]
    return row


def list_row(op: dict[str, Any], *, row_title_max: int = WA_ROW_TITLE_MAX) -> dict[str, Any]:
    """Linha da lista PLANA (nível corrente), com as mesmas regras de id/título de
    sempre (`id` cai para o rótulo) e a segunda linha da ORQ-15."""
    return _row(str(op.get("id", op.get("label", ""))), str(op.get("label", "")), op, row_title_max)
