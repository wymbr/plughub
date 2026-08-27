"""
test_available_actions.py — T10: available_actions por result_state + round + posse.

Função pura (`_compute_available_actions`), sem DB/Kafka. Cobre a matriz da spec §17.2:
  open(R)         ∧ dono do segmento ∧ campo de contestação do round → ["contest"]
  under_review(R) ∧ caller ≠ avaliado ∧ campo de revisão     do round → ["review"]
  caso contrário (sem campo / não-dono / self-review / locked / sem token)             → []
Mais o gate de leitura `_can_view_transcript` (qualquer campo do módulo evaluation).
"""
from __future__ import annotations

from ..router import (
    _compute_available_actions, _can_view_transcript, _compute_result_scope,
    _check_abac_permission,
)


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


def test_view_empty_module_config_denied():
    """Config VAZIO passou a NEGAR (passo 8, 2026-08-27).

    Antes liberava, e aqui o alcance era TRANSCRICAO — o bypass silencioso chegando a
    conteudo, nao so a listas de id->nome. Ausencia de grants nunca foi autorizacao.
    """
    assert _can_view_transcript({"sub": "u", "module_config": {}}, None) is False


def test_view_with_report_only_allowed():
    jwt = _jwt("u_sup", report="read_only")
    assert _can_view_transcript(jwt, None) is True


def test_view_with_no_evaluation_access_denied():
    jwt = {"sub": "u", "module_config": {"evaluation": {"contestar": {"access": "none", "scope": []}}}}
    assert _can_view_transcript(jwt, None) is False


# ─── T10-C — _compute_result_scope (visibilidade) ─────────────────────────────

def test_scope_no_jwt_unrestricted():
    assert _compute_result_scope(None) == (None, None, None)


def test_scope_unrestricted_claim_sees_everyone():
    """Sem recorte e quem DECLARA nao ter (passo 8, 2026-08-27) — antes era o papel."""
    jwt = {"sub": "u_admin", "roles": ["admin"], "accessible_pools": [],
           "unrestricted": True}
    assert _compute_result_scope(jwt) == (None, None, None)


def test_scope_admin_role_alone_is_scoped():
    """O DISCRIMINADOR da mudanca: papel `admin` sozinho NAO da mais escopo total.

    Escopo (de quem eu vejo avaliacoes) e o eixo que `unrestricted` declara; papel
    decidindo escopo era a mesma divida que o menu tinha. Sem o claim, o admin passa a
    ver so a si mesmo — que e o mesmo tratamento de qualquer nao-supervisor.
    """
    jwt = {"sub": "u_admin", "roles": ["admin"], "accessible_pools": [],
           "supervised_user_ids": []}
    users, pools, self_id = _compute_result_scope(jwt)
    assert users == ["u_admin"] and pools is None and self_id == "u_admin"


def test_scope_unrestricted_with_accessible_pools():
    """O claim libera o eixo de PESSOAS; a lista de pools continua restringindo.

    Sao dois recortes independentes, e o RESTRITIVO vence em cada um.
    """
    jwt = {"sub": "u_admin", "roles": ["admin"], "accessible_pools": ["p1", "p2"],
           "unrestricted": True}
    users, pools, self_id = _compute_result_scope(jwt)
    assert users is None and pools == ["p1", "p2"] and self_id is None


def test_scope_atendente_only_self():
    jwt = {"sub": "u_op", "roles": ["operator"], "accessible_pools": [], "supervised_user_ids": []}
    users, pools, self_id = _compute_result_scope(jwt)
    assert users == ["u_op"] and pools is None and self_id == "u_op"


def test_scope_supervisor_group_plus_self():
    jwt = {"sub": "u_sup", "roles": ["supervisor"], "accessible_pools": [],
           "supervised_user_ids": ["u_a", "u_b"]}
    users, _, self_id = _compute_result_scope(jwt)
    assert set(users) == {"u_sup", "u_a", "u_b"}
    assert self_id == "u_sup"


def test_scope_non_admin_passes_accessible_pools():
    jwt = {"sub": "u_op", "roles": ["operator"], "accessible_pools": ["p9"], "supervised_user_ids": []}
    users, pools, self_id = _compute_result_scope(jwt)
    assert users == ["u_op"] and pools == ["p9"] and self_id == "u_op"


# ─── Bug fix 2026-07-02 — posse é sempre visível, independente de accessible_pools ──

def test_scope_self_view_bypasses_pool_mismatch():
    """Regression: campanha com pool_id administrativo != evaluation_pool_id (pool
    operacional real) não deveria bloquear o próprio avaliado de ver seu resultado.
    O eixo self_user_id existe justamente para permitir esse bypass na camada SQL
    (list_results) — aqui só garantimos que o valor certo é retornado do JWT."""
    jwt = {"sub": "u_op", "roles": ["operator"], "accessible_pools": ["retencao_humano"],
           "supervised_user_ids": []}
    users, pools, self_id = _compute_result_scope(jwt)
    assert self_id == "u_op"
    assert users == ["u_op"]
    assert pools == ["retencao_humano"]
    # A garantia real (SQL bypassa accessible_pools quando evaluated_user_id == self_id)
    # é exercitada em test_router.py / db.py — este teste cobre só a extração do JWT.


# ─── Slice auth quality — _check_abac_permission min_access (curar) ────────────

def test_curar_none_denied():
    assert _check_abac_permission(_jwt("u", curar="none"), "curar") is False


def test_curar_read_write_passes_write():
    jwt = _jwt("u", curar="read_write")
    assert _check_abac_permission(jwt, "curar", None, min_access="read_write") is True


def test_curar_read_only_denied_for_write():
    # leitura concedida NÃO satisfaz endpoint de escrita (read_write).
    jwt = _jwt("u", curar="read_only")
    assert _check_abac_permission(jwt, "curar", None, min_access="read_write") is False


def test_curar_read_only_passes_read():
    jwt = _jwt("u", curar="read_only")
    assert _check_abac_permission(jwt, "curar", None, min_access="read_only") is True


def test_min_access_none_keeps_legacy_behavior():
    # sem min_access, qualquer não-'none' passa (comportamento legado p/ revisar/contestar).
    assert _check_abac_permission(_jwt("u", revisar="read_only"), "revisar") is True


def test_legacy_token_no_module_config_grant_first_denied():
    # grant-first: endpoint com min_access (curar) NÃO degrada — config vazio = sem grant = nega.
    assert _check_abac_permission({"sub": "u"}, "curar", None, min_access="read_write") is False


def test_legacy_token_no_module_config_legacy_field_allowed():
    # legado: campo sem min_access (revisar/contestar) mantém degradação graciosa.
    assert _check_abac_permission({"sub": "u"}, "revisar") is True


def test_curar_scope_pool_match_and_mismatch():
    jwt = {"sub": "u", "module_config": {"evaluation": {
        "curar": {"access": "read_write", "scope": ["pool:retencao_humano"]}}}}
    assert _check_abac_permission(jwt, "curar", "retencao_humano", min_access="read_write") is True
    assert _check_abac_permission(jwt, "curar", "outro_pool", min_access="read_write") is False
