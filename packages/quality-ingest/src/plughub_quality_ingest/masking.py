"""
masking.py
Masking net-pass (§5). PII must not exist outside PlugHub: the contract requires
`content` already masked + `masked=true` + `masked_categories`. Because the LGPD
responsibility falls on STORAGE, the module runs a defensive net-pass with the
default MaskingRule set on ingest before emitting `message_sent`.

No Python masking engine exists in the repo (the live engine is TypeScript, in
Core/mcp-server). This is a faithful Python port of DEFAULT_MASKING_RULES
(@plughub/schemas/audit.ts) — same regexes, same replacements, same
preserve_last_digits / preserve_pattern semantics.

`original_content` is never produced here: imported transcripts are review-blind by
construction (the emitter sets original_content=null downstream).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MaskingRule:
    pattern: str
    category: str
    replacement: str
    preserve_last_digits: int | None = None
    preserve_pattern: str | None = None


# Mirror of DEFAULT_MASKING_RULES (audit.ts) — LGPD + PCI-DSS aligned.
DEFAULT_MASKING_RULES: list[MaskingRule] = [
    MaskingRule(
        pattern=r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b",
        category="cpf",
        replacement="***.***.***.--",
        preserve_last_digits=2,
    ),
    MaskingRule(
        pattern=r"\b(?:\d{4}[\s-]?){3}\d{4}\b",
        category="credit_card",
        replacement="**** **** **** ****",
        preserve_last_digits=4,
    ),
    MaskingRule(
        # `(?<!\w)` e não `\b` — ver audit.ts: com `\b` o `\(?` é ramo morto.
        pattern=r"(?<!\w)(?:\+55\s?)?(?:\(?\d{2}\)?[\s-]?)?9?\d{4}[-\s]?\d{4}\b",
        category="phone",
        replacement="(##) ****-####",
        preserve_last_digits=4,
    ),
    MaskingRule(
        pattern=r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b",
        category="email_addr",
        replacement="****@****.***",
        preserve_pattern=r"(@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})$",
    ),
]


def _mask_match(match_text: str, rule: MaskingRule) -> str:
    """Build the masked display for one matched span, honoring preserve rules.

    preserve_pattern takes precedence over preserve_last_digits.

    ⚠️ CORRIGIDO em 2026-08-26. Esta função declarava fidelidade ao TS
    ("same preserve_last_digits / preserve_pattern semantics") e NÃO era fiel: ela
    montava o display a partir de `replacement`, enquanto o canônico
    (`MaskingService.buildDisplay`, mcp-server-plughub/src/lib/masking.ts) monta
    `"*" * (n_dígitos − N) + cauda` e só cai em `replacement` como ÚLTIMO recurso.
    Medido lado a lado (`infra/test/q_masking_display_parity.sh`): o mesmo CPF saía
    `*********00` pelo stream vivo e `***.***.***.00` por aqui — três portas, três
    displays, e nenhuma comparação entre elas.

    O canônico é o TS por ser o que produz o `display_partial` que o cliente recebe
    pelo WebSocket; alinhar na outra direção mudaria o que o operador lê no stream e
    deixaria os tokens já gravados com o display antigo, duas grafias na mesma sessão.

    `replacement` continua no schema e tem um único papel: fallback quando não há
    nada a preservar.
    """
    if rule.preserve_pattern:
        m = re.search(rule.preserve_pattern, match_text)
        if m:
            preserved = m.group(1) if m.lastindex else m.group(0)
            prefix = match_text[: len(match_text) - len(preserved)]
            masked_len = max(1, -(-len(prefix) // 4))  # ceil(len/4), como o Math.ceil do TS
            return f"{'*' * masked_len}{preserved}"
    if rule.preserve_last_digits and rule.preserve_last_digits > 0:
        digits = re.sub(r"\D", "", match_text)
        if len(digits) > rule.preserve_last_digits:
            tail = digits[-rule.preserve_last_digits:]
            return f"{'*' * (len(digits) - rule.preserve_last_digits)}{tail}"
    return rule.replacement


def mask_text(
    text: str,
    rules: list[MaskingRule] | None = None,
) -> tuple[str, list[str]]:
    """Apply the masking net-pass to `text`.

    Returns (masked_text, categories_detected). Idempotent on already-masked text
    (the replacements contain no PII patterns, so a second pass is a no-op).
    """
    if not text:
        return text, []
    active = rules if rules is not None else DEFAULT_MASKING_RULES
    detected: list[str] = []

    masked = text
    for rule in active:
        compiled = re.compile(rule.pattern)
        if not compiled.search(masked):
            continue
        if rule.category not in detected:
            detected.append(rule.category)
        masked = compiled.sub(lambda m, r=rule: _mask_match(m.group(0), r), masked)

    return masked, detected
