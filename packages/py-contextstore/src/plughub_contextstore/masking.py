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

import re
from typing import Any, Iterable, Mapping, Pattern

__all__ = [
    "CONTEXT_MASKING_TYPES",
    "apply_masking_type_to_value",
    "passes_detect_validator",
    "resolve_mask_for_audience",
]

#: Espelha `ContextMaskingType` (@plughub/schemas/audit.ts).
CONTEXT_MASKING_TYPES = (
    "plain", "hidden", "full", "last_2", "last_4",
    "first_1", "first_word", "email_domain", "financial",
)


def passes_detect_validator(validator: str | None, match: str) -> bool:
    """CTX-12 — gêmeo de `passesDetectValidator` (@plughub/schemas/audit.ts).

    `cpf_dv`: trecho pontuado vale como CPF pelo formato; 11 dígitos CRUS só com dígito
    verificador válido (senão o trecho segue para a próxima regra — o telefone). Sem
    validador ⇒ passa; validador DESCONHECIDO ⇒ recusa o casamento (nome que nenhum
    motor conhece não vira "passa em tudo"). Paridade:
    `infra/test/probe_detect_validator_parity.sh`.
    """
    if not validator:
        return True
    if validator == "cpf_dv":
        if not (len(match) == 11 and match.isdigit() and match.isascii()):
            return True                           # pontuado: o formato identifica
        if match == match[0] * 11:
            return False                          # 000…0, 111…1: DV "válido" e falso

        def dv(n: int) -> int:
            s = sum(int(match[i]) * (n + 1 - i) for i in range(n))
            r = (s * 10) % 11
            return 0 if r == 10 else r

        return dv(9) == int(match[9]) and dv(10) == int(match[10])
    return False


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


def detected_display(
    match: str,
    category: str,
    catalogo: Mapping[str, Mapping[str, Any]],
    rede: Iterable[Pattern[str]] = (),
) -> str:
    """O que aparece NO LUGAR de um dado DETECTADO em texto livre (MSK-05).

    Gêmeo de `detectedDisplay` (@plughub/schemas/ctx-audience.ts): a máscara do
    `operator` no `masking.types` do tenant, a mesma que o dado DECLARADO segue.

    `catalogo` é indexado por id (o formato de `get_masking_catalog`). `{}` — catálogo
    indisponível — faz toda categoria cair em `full` e sair `***`: esconder por não
    saber, como o loader promete.

    `rede` são os padrões de detecção de quem chama. Duas recusas, as mesmas do TS:
    `hidden` (`""`) vira `***`, porque não se omite pedaço de frase; e exibição que
    ainda casa a rede não escondeu o que foi detectado, então vira `***` — é o que
    mantém a passada idempotente sobre qualquer config. `plain` é a escolha declarada.
    """
    mascara = resolve_mask_for_audience(catalogo.get(category), "operator")
    if mascara == "plain":
        return match
    d = apply_masking_type_to_value(match, mascara)
    if d == "":
        return "***"
    for padrao in rede:
        if padrao.search(d):
            return "***"
    return d


# ── A REDE de texto livre, em Python (MSK-06, 2026-09-25) ───────────────────────
#
# Espelho das regras DETECTÁVEIS de `DEFAULT_MASKING_RULES` (@plughub/schemas/audit.ts,
# derivadas do catálogo semeado): categoria + padrão + validador. A ordem é contrato — a
# rede aplica em sequência, e o CPF cru com DV inválido tem de chegar à regra de telefone.
#
# Mora AQUI para ser a casa Python única: o bridge (fala do cliente) e o channel-gateway
# (edições de aprovação) a consomem. O quality-ingest não depende deste pacote e mantém
# cópia declarada. Quem acusa divergência com o TS é `probe_detect_validator_parity.sh`,
# que roda `mask_free_text` sobre a mesma fixture da rede do engine.
FREE_TEXT_RULES: tuple[tuple[str, Pattern[str], str | None], ...] = (
    ("cpf",         re.compile(r"\b(?:\d{3}\.\d{3}\.\d{3}-\d{2}|\d{11})\b"), "cpf_dv"),
    ("credit_card", re.compile(r"\b(?:\d{4}[\s-]?){3}\d{4}\b"), None),
    # `(?<!\w)` e não `\b` — com `\b` o `\(?` é ramo morto e o parêntese ficava órfão.
    ("phone",       re.compile(r"(?<!\w)(?:\+55\s?)?(?:\(?\d{2}\)?[\s-]?)?9?\d{4}[-\s]?\d{4}\b"), None),
    ("email_addr",  re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b"), None),
)


def mask_free_text(
    text: str,
    catalogo: Mapping[str, Mapping[str, Any]],
) -> tuple[str, list[str]]:
    """Mascara PII reconhecível por FORMA em `text`. Gêmeo de `maskFreeText` (TS), só folha.

    Devolve `(texto_mascarado, categorias_detectadas)`. A exibição de cada trecho é
    `detected_display` sobre `catalogo` (`masking.types` indexado por id; `{}` esconde tudo).
    Idempotente: a exibição nunca casa a rede. Texto sem dado sai idêntico, com
    `categorias` vazia — é ela, nunca a comparação dos textos, que diz se houve máscara.

    ⚠️ MITIGAÇÃO, nunca controle (§D12 do ADR de plateia): pega 4 tipos, só por forma.
    """
    if not text:
        return text, []
    rede = [p for _, p, _ in FREE_TEXT_RULES]
    detectadas: list[str] = []
    s = text
    for categoria, padrao, validador in FREE_TEXT_RULES:
        casou = False

        def _troca(m: "re.Match[str]", c: str = categoria, v: str | None = validador) -> str:
            nonlocal casou
            if not passes_detect_validator(v, m.group(0)):
                return m.group(0)
            casou = True
            return detected_display(m.group(0), c, catalogo, rede)

        s = padrao.sub(_troca, s)
        if casou and categoria not in detectadas:
            detectadas.append(categoria)
    return (s if detectadas else text), detectadas
