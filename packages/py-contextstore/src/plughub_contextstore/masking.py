# -*- coding: utf-8 -*-
"""Aplicação de máscara por TIPO — gêmeo Python de `applyMaskingTypeToValue`.

── Por que este módulo existe (2026-09-02) ──────────────────────────────────────

O levantamento de máscara achou **seis motores em duas famílias**. Na família "por
TIPO" havia dois, e eles divergiam:

  · `applyMaskingTypeToValue` (mcp-server/lib/context-masking.ts) — **9 de 9**, canônico
  · `_apply_preview_mask`     (channel-gateway/adapters/webhook.py) — **5 de 9**

E o docstring do segundo dizia *"Vocabulário espelha `masking.context_rules`"* — promessa
sem mecanismo, a família do DDL de `participation_intervals`. A consequência era concreta e
silenciosa: `financial` não existia ali, então um campo daquele tipo seria **omitido** do
preview em vez de mascarado, e quem "consertasse" o spec para nomeá-lo faria o campo sumir.

Este módulo é o gêmeo do canônico, com gate de paridade sobre fixture única — mesmo desenho
que a ALW-02 usou para o carimbo. Puro e sem I/O pelo mesmo motivo: função pura é a única
espécie que se cross-checa barato entre duas linguagens.

── Duas divergências MEDIDAS, e o que foi feito com cada uma ────────────────────

1. **`last_N` sobre dígitos × alfanuméricos.** O canônico usa `raw.replace(/\\D/g,"")`; o
   preview usava `ch.isalnum()`. Num número de cartão coincide; num identificador com letras
   (`AB1234`) não — o canônico devolveria `***1234`, o preview `***1234` a partir de outro
   conjunto. **Este módulo segue o CANÔNICO**: dígitos.
2. **Desconhecido.** O canônico devolve `"***"` (mascara); o preview devolve `None` (omite e
   loga). As duas são seguras, e a segunda é mais conservadora — *"rebaixar para `plain`
   seria trocar erro de configuração por vazamento silencioso"*. Aqui o comportamento é o do
   CANÔNICO (`"***"`), e a decisão de OMITIR fica com o chamador, que é onde ela sempre
   esteve. Separar as duas é o que permite comparar este módulo com a TS.
"""
from __future__ import annotations

from typing import Any, Mapping

__all__ = [
    "CONTEXT_MASKING_TYPES",
    "apply_masking_type_to_value",
    "resolve_mask_for_audience",
]

#: Espelha `ContextMaskingType` (@plughub/schemas/audit.ts).
CONTEXT_MASKING_TYPES = (
    "plain", "hidden", "full", "last_2", "last_4",
    "first_1", "first_word", "email_domain", "financial",
)


def apply_masking_type_to_value(raw: str, mask: str) -> str:
    """Aplica UMA máscara. Espelho 1:1 de `applyMaskingTypeToValue`.

    ⚠️ `hidden` devolve **string vazia**, que é SINAL para o chamador omitir o campo — não
    é "o valor é vazio". O canônico faz o mesmo, e o comentário dele diz isso; manter a
    convenção é o que permite comparar as duas saídas.
    """
    digits = "".join(ch for ch in raw if ch.isdigit())

    if mask == "plain":
        return raw
    if mask == "hidden":
        return ""                      # sinal: o chamador omite o campo
    if mask == "full":
        return "***"
    if mask == "last_2":
        return f"***{digits[-2:]}" if len(digits) >= 2 else "***"
    if mask == "last_4":
        if len(digits) >= 4:
            return f"***{digits[-4:]}"
        return f"***{digits}" if digits else "***"
    if mask == "first_1":
        return f"{raw[0]}***" if raw else "***"
    if mask == "first_word":
        word = raw.split()[0] if raw.split() else ""
        return f"{word} ***" if word else "***"
    if mask == "email_domain":
        at = raw.find("@")
        if at > 0:
            local, domain = raw[:at], raw[at:]
            return f"{local[0] if local else '*'}***{domain}"
        return f"{raw[0]}***" if raw else "***"
    if mask == "financial":
        return "R$ ****,**"
    return "***"


def resolve_mask_for_audience(
    tipo_entry: Mapping[str, Any] | None,
    audiencia: str,
) -> str:
    """Qual máscara um TIPO aplica para uma AUDIÊNCIA.

    ── A regra, e por que ela tem três ramos ────────────────────────────────────

        by_role VAZIO            → "plain"
        audiência declarada      → a dela
        audiência não declarada  → a do `operator`

    **`by_role: {}` significa ABERTO, declarado** — não "esqueceram de preencher". É a marca
    dos tipos de FINALIDADE (`linha_em_servico`, `valor_declarado_pelo_cliente`): a máscara é
    vazia e a **classe LGPD é preservada**, que é a regra da D8. Tratar `{}` como "sem
    resposta" e cair no `operator` esconderia do cliente o que ele mesmo declarou — foi
    exatamente esse o caso que fez o `preview` nascer com vocabulário próprio.

    ⚠️ **O fallback para `operator` é declarado, não é ordenação de severidade.** Hoje
    `operator` é a única audiência que o catálogo declara (medido em 2026-09-02: 11 de 13
    tipos, e `supervisor` em nenhum). Inventar um "mais restritivo" exigiria ordenar as nove
    máscaras por força, o que é opinião; usar a única declarada é fato. Quando o eixo
    `customer` existir, o segundo ramo o pega sem mudar nada aqui.

    Tipo ausente do catálogo → `"full"`. Recusa alta: um tipo que a config não conhece não
    pode virar `plain` por omissão.
    """
    if tipo_entry is None:
        return "full"
    by_role = (tipo_entry.get("mascara") or {}).get("by_role")
    if not isinstance(by_role, Mapping):
        return "full"
    if not by_role:
        return "plain"                 # declarado ABERTO (tipo de finalidade)
    m = by_role.get(audiencia) or by_role.get("operator")
    return m if isinstance(m, str) and m else "full"
