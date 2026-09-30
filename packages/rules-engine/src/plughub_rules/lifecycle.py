"""
lifecycle.py
Rule lifecycle state machine.
Spec: PlugHub v24.0 section 3.2b

Valid transitions:
  draft    → dry_run, disabled
  dry_run  → shadow, draft, disabled
  shadow   → active, dry_run, disabled
  active   → shadow, disabled
  disabled → draft
"""

from __future__ import annotations

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft":    {"dry_run", "disabled"},
    "dry_run":  {"shadow", "draft", "disabled"},
    "shadow":   {"active", "dry_run", "disabled"},
    "active":   {"shadow", "disabled"},
    "disabled": {"draft"},
}


# RUL-03 (2026-09-30, decisão do dono): só a regra que NÃO age nem mede se edita ou se apaga.
# Mudar uma regra ativa ou em shadow pularia o ciclo que existe para ela ser medida antes de
# agir; para mudá-la: disabled → draft → editar → dry_run → shadow → active.
EDITABLE_STATUSES: frozenset[str] = frozenset({"draft", "disabled"})


def transitions() -> dict[str, list[str]]:
    """A máquina de estados, para quem precisa MOSTRÁ-LA (a tela) sem copiá-la."""
    return {k: sorted(v) for k, v in _VALID_TRANSITIONS.items()}


def validate_transition(from_status: str, to_status: str) -> None:
    """
    Validates a lifecycle transition.

    Raises ValueError if the transition is not allowed.
    The error message includes guidance for draft → active attempts.
    """
    allowed = _VALID_TRANSITIONS.get(from_status, set())

    if to_status not in allowed:
        # Provide a more descriptive message for the common mistake of
        # trying to jump from draft directly to active.
        if from_status in ("draft", "dry_run") and to_status == "active":
            raise ValueError(
                f"Transition not allowed: {from_status} → {to_status}. "
                f"{from_status} requires passing through dry_run before active."
            )
        raise ValueError(
            f"Transition not allowed: {from_status} → {to_status}"
        )
