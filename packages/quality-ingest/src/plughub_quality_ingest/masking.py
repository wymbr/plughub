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
        pattern=r"\b(?:\+55\s?)?(?:\(?\d{2}\)?[\s-]?)?9?\d{4}[-\s]?\d{4}\b",
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

    preserve_pattern takes precedence over preserve_last_digits (mirrors TS).
    """
    if rule.preserve_pattern:
        m = re.search(rule.preserve_pattern, match_text)
        if not m:
            return rule.replacement
        suffix = m.group(1)
        # Keep only the replacement portion before the suffix's leading delimiter,
        # so an email "****@****.***" + "@example.com" → "****@example.com"
        # (not a doubled "@").
        delim = suffix[0]
        base = rule.replacement.split(delim)[0] if delim in rule.replacement else rule.replacement
        return f"{base}{suffix}"
    if rule.preserve_last_digits and rule.preserve_last_digits > 0:
        digits = re.sub(r"\D", "", match_text)
        tail = digits[-rule.preserve_last_digits:] if digits else ""
        return f"{rule.replacement[:-len(tail)]}{tail}" if tail else rule.replacement
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
