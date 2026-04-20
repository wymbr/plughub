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
