"""
test_available_actions.py — T10: available_actions por result_state + round + posse.

Função pura (`_compute_available_actions`), sem DB/Kafka. Cobre a matriz da spec §17.2:
  open(R)         ∧ dono do segmento ∧ campo de contestação do round → ["contest"]
  under_review(R) ∧ caller ≠ avaliado ∧ campo de revisão     do round → ["review"]
  caso contrário (sem campo / não-dono / self-review / locked / sem token)             → []
Mais o gate de leitura `_can_view_transcript` (qualquer campo do módulo evaluation).
"""
from __future__ import annotations

from ..router import _compute_available_actions, _can_view_transcript


# ─── helpers ────────────────────────────────────────────────────────────────────

def _result(state: str, *, rnd: int = 1, evaluated: str | None = "u_agent",
            locked: bool = False, eval_status: str = "open") -> dict:
    return {
        "result_state": state,
        "current_round": rnd,
        "evaluated_user_id": evaluated,
        "locked": locked,
        "eval_status": eval_status,
    }


def _jwt(sub: str, **fields: str) -> dict:
    """JWT com module_config.evaluation.{field}={access, scope:[]} (scope global)."""
    eval_cfg = {f: {"access": acc, "scope": []} for f, acc in fields.items()}
    return {"sub": sub, "module_config": {"evaluation": eval_cfg}}


# ─── contest (open) ───────────────────────────────────────────────────────────

def test_open_owner_with_contestar_can_contest():
    r = _result("open", rnd=1, evaluated="u_agent")
    jwt = _jwt("u_agent", contestar="read_write")
    assert _compute_available_actions(r, jwt, None) == ["contest"]


def test_open_owner_without_field_cannot_contest():
    r = _result("open", rnd=1, evaluated="u_agent")
    jwt = _jwt("u_agent")  # sem contestar
    assert _compute_available_actions(r, jwt, None) == []


def test_open_non_owner_cannot_contest_even_with_field():
    r = _result("open", rnd=1, evaluated="u_agent")
    jwt = _jwt("u_other", contestar="read_write")  # tem o campo mas não é o avaliado
    assert _compute_available_actions(r, jwt, None) == []


def test_open_round2_requires_replica_field():
    r = _result("open", rnd=2, evaluated="u_agent")
    assert _compute_available_actions(r, _jwt("u_agent", contestar="read_write"), None) == []
    assert _compute_available_actions(r, _jwt("u_agent", contestar_replica="read_write"), None) == ["contest"]


# ─── review (under_review) ────────────────────────────────────────────────────

def test_under_review_reviewer_with_revisar_can_review():
    r = _result("under_review", rnd=1, evaluated="u_agent")
    jwt = _jwt("u_reviewer", revisar="read_write")
    assert _compute_available_actions(r, jwt, None) == ["review"]


def test_under_review_self_cannot_review():
    r = _result("under_review", rnd=1, evaluated="u_agent")
    jwt = _jwt("u_agent", revisar="read_write")  # é o próprio avaliado
    assert _compute_available_actions(r, jwt, None) == []


def test_under_review_without_field_cannot_review():
    r = _result("under_review", rnd=1, evaluated="u_agent")
    assert _compute_available_actions(r, _jwt("u_reviewer"), None) == []


def test_under_review_round3_requires_treplica_field():
    r = _result("under_review", rnd=3, evaluated="u_agent")
    assert _compute_available_actions(r, _jwt("u_rev", revisar="read_write"), None) == []
    assert _compute_available_actions(r, _jwt("u_rev", revisar_treplica="read_write"), None) == ["review"]


# ─── read-only / terminal / guards ────────────────────────────────────────────

def test_finalized_has_no_actions():
    r = _result("finalized", rnd=1, evaluated="u_agent")
    assert _compute_available_actions(r, _jwt("u_agent", contestar="read_write"), None) == []


def test_locked_has_no_actions():
    r = _result("open", rnd=1, evaluated="u_agent", locked=True)
    assert _compute_available_actions(r, _jwt("u_agent", contestar="read_write"), None) == []


def test_no_jwt_has_no_actions():
    r = _result("open", rnd=1, evaluated="u_agent")
    assert _compute_available_actions(r, None, None) == []


def test_legacy_token_degrades_to_allow_review():
    # token sem module_config (ex.: admin) → _check_abac_permission degrada p/ permitir
    r = _result("under_review", rnd=1, evaluated="u_agent")
    legacy = {"sub": "u_admin"}  # sem module_config
    assert _compute_available_actions(r, legacy, None) == ["review"]


# ─── _can_view_transcript ─────────────────────────────────────────────────────

def test_view_anonymous_allowed():
    assert _can_view_transcript(None, None) is True


def test_view_empty_module_config_allowed():
    assert _can_view_transcript({"sub": "u", "module_config": {}}, None) is True


def test_view_with_report_only_allowed():
    jwt = _jwt("u_sup", report="read_only")
    assert _can_view_transcript(jwt, None) is True


def test_view_with_no_evaluation_access_denied():
    jwt = {"sub": "u", "module_config": {"evaluation": {"contestar": {"access": "none", "scope": []}}}}
    assert _can_view_transcript(jwt, None) is False
