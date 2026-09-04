# -*- coding: utf-8 -*-
"""format_guard.py — a recusa da §D8 no PUBLISH.

`masked: "<tipo>"` já implica o formato quando aquele tipo tem contraparte no
catálogo. Declarar `format` junto e DIFERENTE é uma contradição que o schema
aceitaria (são dois campos opcionais independentes), e o efeito seria um campo
cuja máscara diz uma coisa e cujo veredicto julga outra.

**Escolher um vencedor em silêncio é como se paga a diferença depois.** A recusa
acontece no publish, que é onde há um autor para ler — e nomeia OS DOIS lados,
porque *"conflito de formato"* sem os nomes devolve a mesma investigação que a
ausência de mensagem.

── O que este guarda NÃO faz, e por quê ─────────────────────────────────────
Ele lê o catálogo EMBUTIDO (`plughub_contextstore.dialog_formats`), não o
`dialog.formats` do config-api. A consequência é assimétrica e a direção é
deliberada: um formato que o tenant tenha ACRESCENTADO não é conhecido aqui,
então `derivado` sai vazio e o conflito **passa despercebido** — nunca o
contrário. Um guarda que INVENTASSE conflito bloquearia a publicação de uma
forma válida, que é o dano maior. Mesma dívida do FMT-09, na direção segura.
"""
from __future__ import annotations

from typing import Any

from plughub_contextstore.dialog_formats import DIALOG_FORMAT_CATALOG

# `masked: true` legado resolve para o tipo `opaque` (T1 do ADR do masked tipado).
_LEGADO_TRUE = "opaque"


def _formato_por_tipo() -> dict[str, str]:
    """tipo mascarado → id do formato. A unicidade é invariante do catálogo
    (`ambiguous_masked_ref` no oráculo da TS); aqui o último vence, o que só
    importaria num catálogo já reprovado."""
    return {
        f["from_masked_type"]: f["id"]
        for f in DIALOG_FORMAT_CATALOG.get("formats", [])
        if f.get("from_masked_type")
    }


def _tipo_declarado(masked: Any) -> str | None:
    if masked is True:
        return _LEGADO_TRUE
    if isinstance(masked, str) and masked:
        return masked
    return None


def conflitos_de_formato(nodes: list[dict[str, Any]]) -> list[str]:
    """Lista legível dos conflitos. Vazia = pode publicar.

    Percorre pergunta E campo: os dois sítios declaram `masked` e `validation`,
    e cobrir só um deixaria metade do formulário sem guarda — que é como a D6
    nasceu.
    """
    mapa = _formato_por_tipo()
    achados: list[str] = []

    def julga(onde: str, alvo: dict[str, Any]) -> None:
        tipo = _tipo_declarado(alvo.get("masked"))
        if not tipo:
            return
        derivado = mapa.get(tipo)
        if not derivado:
            # Tipo que mascara sem formatar (`credential`, `opaque`) — desfecho
            # legítimo, e não a ausência de uma regra a "consertar".
            return
        declarado = (alvo.get("validation") or {}).get("format")
        if declarado and declarado != derivado:
            achados.append(
                f"{onde}: masked='{tipo}' deriva o formato '{derivado}', "
                f"mas validation.format declara '{declarado}'"
            )

    for node in nodes or []:
        if node.get("kind") != "question":
            continue
        nid = node.get("id") or node.get("output_key") or "?"
        julga(f"pergunta '{nid}'", node)
        for campo in node.get("fields") or []:
            julga(f"campo '{nid}.{campo.get('id') or '?'}'", campo)

    return achados
