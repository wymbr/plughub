"""
test_deploy_lens.py
Arc 6 Fase 2 — lente `deploy` de query_agents_compare, **ancorada no POOL** (spec §11).

Cobre:
  - 'deploy' é lente válida (não cai em invalid_lens)
  - SQL: lê de evaluation_finalized (Oficial / final_score), bucketiza pela data da
    SESSÃO (session_started_at), agrupa por **attr.pool_id** (curva por pool) e gateia
    domain ai (agent_type != 'human')
  - série diária por POOL + summary.n_evaluations (significância)
  - deploy_markers: vêm da timeline do POOL (mock), com pool_id + version_label,
    filtrados ao window e ordenados
  - meta.lens == 'deploy' + meta.min_sample == 30
  - degradação: registry fora (helper → []) → deploy_markers == []
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ..reports_query import query_agents_compare, _DEPLOY_MIN_SAMPLE, _COMPARE_LENSES

TENANT = "tenant_telco"
DB     = "plughub"

# agent_key = pool_id nesta lente (curva por pool)
_COLS = ["agent_key", "agent_type", "label", "bucket", "n", "avg_score"]


def _ch_result(col_names, rows) -> MagicMock:
    r = MagicMock()
    r.column_names = col_names
    r.result_rows  = rows
    return r


def _make_client(*query_results) -> MagicMock:
    client = MagicMock()
    client.query = MagicMock(side_effect=list(query_results))
    return client


def _patch_deploys(deploys):
    """Patcha o helper async de deploys POR POOL usado por _fetch_deploy_markers."""
    return patch(
        "plughub_analytics_api.deployments_client.fetch_pool_deployments",
        new=AsyncMock(return_value=deploys),
    )


# ─── lente registrada ─────────────────────────────────────────────────────────

def test_deploy_is_registered_lens():
    assert "deploy" in _COMPARE_LENSES


# ─── SQL + série + markers ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deploy_reads_finalized_grouped_by_pool_and_attaches_markers():
    # entidade = pool "sac_ia"; a curva é a qualidade do pool no tempo.
    client = _make_client(_ch_result(_COLS, [
        ["sac_ia", "ai", "sac_ia", "2026-06-18", 40, 0.82],
        ["sac_ia", "ai", "sac_ia", "2026-06-20", 12, 0.74],
    ]))
    deploys = [
        {"deploy_id": "dep_2", "skill_id": "skill_atendimento_sac_v1", "version_label": "2.0",
         "deployed_at": "2026-06-19T10:00:00Z", "deployed_by": "u_ana"},
        {"deploy_id": "dep_0", "skill_id": "skill_atendimento_sac_v1", "version_label": "1.0",
         "deployed_at": "2026-05-01T08:00:00Z", "deployed_by": "u_bob"},  # fora do window
    ]
    with _patch_deploys(deploys):
        result = await query_agents_compare(
            client, DB, TENANT, lens="deploy", entities=["sac_ia"],
            from_dt="2026-06-01", to_dt="2026-06-30",
        )

    sql = client.query.call_args_list[-1][0][0]
    assert "evaluation_finalized" in sql            # modo Oficial (invariante)
    assert "final_score" in sql
    assert "session_started_at" in sql              # regra de ouro §7
    assert "attr.pool_id" in sql                    # curva ancorada no pool
    assert "agent_type != 'human'" in sql           # domain ai
    assert "JOIN" in sql

    ent = result["data"]["entities"][0]
    assert ent["agent_key"] == "sac_ia"             # a entidade é o pool
    assert ent["summary"]["n_evaluations"] == 52    # 40 + 12
    assert ent["series"][0]["avg_score"] == pytest.approx(0.82)

    markers = result["deploy_markers"]
    assert len(markers) == 1                        # só o deploy dentro do window
    assert markers[0]["pool_id"] == "sac_ia"        # marcador amarrado à curva do pool
    assert markers[0]["version_label"] == "2.0"
    assert markers[0]["skill_id"] == "skill_atendimento_sac_v1"

    assert result["meta"]["lens"] == "deploy"
    assert result["meta"]["min_sample"] == _DEPLOY_MIN_SAMPLE == 30


@pytest.mark.asyncio
async def test_shared_deploy_marks_each_pool_curve():
    # um deploy atinge 2 pools → aparece como marker em cada curva de pool.
    client = _make_client(_ch_result(_COLS, [
        ["pool_a", "ai", "pool_a", "2026-06-10", 30, 0.9],
        ["pool_b", "ai", "pool_b", "2026-06-10", 25, 0.7],
    ]))
    # cada pool devolve o mesmo deploy (mesma data/versão) — é o deploy compartilhado.
    deploy = {"deploy_id": "dep_shared", "skill_id": "skill_x", "version_label": "3.0",
              "deployed_at": "2026-06-12T00:00:00Z"}
    with _patch_deploys([deploy]):
        result = await query_agents_compare(
            client, DB, TENANT, lens="deploy", entities=["pool_a", "pool_b"],
            from_dt="2026-06-01", to_dt="2026-06-30",
        )
    markers = result["deploy_markers"]
    pools = sorted(m["pool_id"] for m in markers)
    assert pools == ["pool_a", "pool_b"]            # mesmo deploy marca as duas curvas
    assert all(m["version_label"] == "3.0" for m in markers)


@pytest.mark.asyncio
async def test_deploy_degrades_when_registry_down():
    client = _make_client(_ch_result(_COLS, [
        ["sac_ia", "ai", "sac_ia", "2026-06-10", 5, 0.7],
    ]))
    with _patch_deploys([]):                         # helper já degrada p/ [] no erro
        result = await query_agents_compare(
            client, DB, TENANT, lens="deploy", entities=["sac_ia"],
            from_dt="2026-06-01", to_dt="2026-06-30",
        )
    assert result["deploy_markers"] == []           # série intacta, sem markers
    assert result["data"]["entities"][0]["summary"]["n_evaluations"] == 5


@pytest.mark.asyncio
async def test_deploy_skips_pool_pseudo_entities_for_markers():
    # entidades pool:<id> (pseudo-média) não disparam fetch de deploys.
    # (duas queries: per_agent do escopo + a pseudo-entidade do pool)
    client = _make_client(_ch_result(_COLS, []), _ch_result(_COLS, []))
    fake = AsyncMock(return_value=[])
    with patch("plughub_analytics_api.deployments_client.fetch_pool_deployments", new=fake):
        await query_agents_compare(
            client, DB, TENANT, lens="deploy", entities=["pool:retencao_ia"],
            from_dt="2026-06-01", to_dt="2026-06-30",
        )
    fake.assert_not_called()
