"""
masking.py
Masking net-pass (§5). PII must not exist outside PlugHub: the contract requires
`content` already masked + `masked=true` + `masked_categories`. Because the LGPD
responsibility falls on STORAGE, the module runs a defensive net-pass with the
default MaskingRule set on ingest before emitting `message_sent`.

No Python masking engine exists in the repo (the live engine is TypeScript, in
Core/mcp-server). This is a faithful Python port of DEFAULT_MASKING_RULES
(@plughub/schemas/audit.ts) — same regexes and validators. The DISPLAY is the operator mask of the catalog
(MSK-05), not a per-rule replacement.

`original_content` is never produced here: imported transcripts are review-blind by
construction (the emitter sets original_content=null downstream).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MaskingRule:
    """DETECÇÃO: padrão + categoria + validador. A exibição NÃO mora na regra (MSK-05)."""
    pattern: str
    category: str
    validator: str | None = None


def passes_detect_validator(validator: str | None, match: str) -> bool:
    """CTX-12 — CÓPIA de `passesDetectValidator` (@plughub/schemas/audit.ts).

    Cópia porque este serviço não depende de `plughub-contextstore` (onde mora o gêmeo
    do channel-gateway); a mesma dívida das regras acima. Quem impede a divergência é
    `infra/test/probe_detect_validator_parity.sh`, que roda ESTA função sobre a fixture.

    `cpf_dv`: pontuado vale pelo formato; 11 dígitos crus só com DV válido.
    """
    if not validator:
        return True
    if validator == "cpf_dv":
        if not (len(match) == 11 and match.isdigit() and match.isascii()):
            return True
        if match == match[0] * 11:
            return False

        def dv(n: int) -> int:
            s = sum(int(match[i]) * (n + 1 - i) for i in range(n))
            r = (s * 10) % 11
            return 0 if r == 10 else r

        return dv(9) == int(match[9]) and dv(10) == int(match[10])
    return False


# Mirror of DEFAULT_MASKING_RULES (audit.ts) — LGPD + PCI-DSS aligned.
DEFAULT_MASKING_RULES: list[MaskingRule] = [
    MaskingRule(
        # CTX-12: também os 11 dígitos CRUS, que só são CPF com DV válido.
        pattern=r"\b(?:\d{3}\.\d{3}\.\d{3}-\d{2}|\d{11})\b",
        category="cpf",
        validator="cpf_dv",
    ),
    MaskingRule(
        pattern=r"\b(?:\d{4}[\s-]?){3}\d{4}\b",
        category="credit_card",
    ),
    MaskingRule(
        # `(?<!\w)` e não `\b` — ver audit.ts: com `\b` o `\(?` é ramo morto.
        pattern=r"(?<!\w)(?:\+55\s?)?(?:\(?\d{2}\)?[\s-]?)?9?\d{4}[-\s]?\d{4}\b",
        category="phone",
    ),
    MaskingRule(
        pattern=r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b",
        category="email_addr",
    ),
]


# Máscara do `operator` para as quatro categorias detectáveis — CÓPIA do `by_role` semeado
# em `DEFAULT_DATA_TYPE_CATALOG` (@plughub/schemas/audit.ts).
#
# ⚠️ MSK-05 (2026-09-25): a exibição do dado detectado passou a ser a de /config/masking
# por papel (decisão do dono). Os outros serviços leem o catálogo VIVO; este não tem
# acesso ao config-api nem depende de `plughub-contextstore`, então carrega a cópia — a
# mesma dívida declarada das regras acima. Quem acusa quando ela envelhece é
# `infra/test/probe_masking_display_parity.sh`, que dá às outras portas o catálogo VIVO
# e a esta, nada: se o tenant mudar a exibição, a linha diverge e fica vermelha.
_OPERATOR_MASK: dict[str, str] = {
    "cpf":         "last_2",
    "credit_card": "last_4",
    "phone":       "last_4",
    "email_addr":  "email_domain",
}


def _apply(raw: str, mask: str) -> str:
    """As máscaras que `_OPERATOR_MASK` usa, com a semântica de `applyMaskingTypeToValue`.
    Máscara que esta cópia não conhece sai `***` — esconder, nunca revelar."""
    digits = re.sub(r"\D", "", raw)
    if mask == "last_2":
        return f"***{digits[-2:]}" if len(digits) >= 2 else "***"
    if mask == "last_4":
        if len(digits) >= 4:
            return f"***{digits[-4:]}"
        return f"***{digits}" if digits else "***"
    if mask == "email_domain":
        at = raw.find("@")
        if at > 0:
            return f"{raw[0]}***{raw[at:]}"
        return f"{raw[0]}***" if raw else "***"
    return "***"


def _display(match_text: str, category: str, active: list[MaskingRule]) -> str:
    """Exibição de UM trecho detectado — a mesma de `detectedDisplay` (TS) e
    `detected_display` (py-contextstore): a do `operator`, e `***` quando o resultado
    ainda casaria a rede (não escondeu o que foi detectado)."""
    d = _apply(match_text, _OPERATOR_MASK.get(category, "full"))
    if not d or any(re.search(r.pattern, d) for r in active):
        return "***"
    return d


def mask_text(
    text: str,
    rules: list[MaskingRule] | None = None,
) -> tuple[str, list[str]]:
    """Apply the masking net-pass to `text`.

    Returns (masked_text, categories_detected). Idempotent on already-masked text
    (a exibição nunca casa a rede — `_display` recusa a que casaria — so a second
    pass is a no-op).
    """
    if not text:
        return text, []
    active = rules if rules is not None else DEFAULT_MASKING_RULES
    detected: list[str] = []

    masked = text
    for rule in active:
        compiled = re.compile(rule.pattern)
        casou = False

        def _troca(m: "re.Match[str]", r: MaskingRule = rule) -> str:
            nonlocal casou
            # CTX-12: casamento que não passa no validador fica INTACTO para a próxima
            # regra — e não conta como categoria detectada.
            if not passes_detect_validator(r.validator, m.group(0)):
                return m.group(0)
            casou = True
            return _display(m.group(0), r.category, active)

        masked = compiled.sub(_troca, masked)
        if casou and rule.category not in detected:
            detected.append(rule.category)

    return masked, detected
