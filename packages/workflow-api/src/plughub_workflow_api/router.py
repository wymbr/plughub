"""
router.py
FastAPI routes for the Workflow API.

Rota viva:
  POST /admin/backfill-events                     — re-emite workflow.* do histórico (X-Admin-Token)
  (GET /v1/health mora no main.py)

⚠️ **As 11 rotas de instância e de proxy SAÍRAM em 2026-09-29 (AUT-64)** — ver o bloco
"Rotas REMOVIDAS" abaixo. Antes dela saíram o cancel (2026-08-07) e as 8 de webhook
(2026-09-08, MOD-11).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from .db import db_list_instances
from .kafka_emitter import emit_events_batch

logger = logging.getLogger("plughub.workflow.router")
router = APIRouter()


def _pool(request: Request):
    return request.app.state.pool


def _producer(request: Request):
    return getattr(request.app.state, "producer", None)


def _settings(request: Request):
    return request.app.state.settings


# ── Rotas REMOVIDAS em 2026-09-29 (AUT-64) ───────────────────────────────────
#
# Saíram 11 rotas, todas medidas SEM chamador de produto. A AUT-58 as achou
# respondendo ao anônimo pela borda pública (o nginx do platform-ui publicava
# `^/v1/workflow`). A ficha mandava medir antes de pôr portão, e o que não tivesse
# chamador sairia. Nenhuma tinha:
#
#   · `GET /v1/workflow/instances`, `…/{id}`, `…/{id}/sessions`,
#     `GET /v1/workflow/campaigns/{id}/collects` — leem `workflow.instances` e
#     `workflow.collect_instances`. A primeira tem ZERO linhas, e nada neste
#     repositório chama `db_create_instance`: o Arc 19 (fase D) trocou a instância
#     pela SESSÃO webhook. O Monitor lê `/sessions/processes` da analytics-api, e o
#     `hooks.ts` da UI já não chamava nenhuma delas.
#   · `persist-suspend`, `complete`, `fail`, `collect/persist`, `collect/respond`
#     — respondiam 410 desde a fase D. O único chamador era o skill-flow-worker,
#     que só age ao consumir `workflow.events`, e o tópico não tem mais produtor de
#     início de instância. A cadeia estava morta nas duas pontas.
#   · `POST /v1/workflow/trigger` e `POST /v1/workflow/resume` — proxies ANÔNIMOS
#     para as rotas internas do channel-gateway (`/v1/channels/webhook/{flow_id}` e
#     `…/resume/{token}`). Com a borda publicando `/v1/workflow`, eram um
#     desvio pela 5174 do que a AUT-20 fechou. O trigger ainda endereçava um SKILL,
#     não um pool (CLAUDE.md § Invariants). Só os cenários e2e 13/14/18/28 os
#     chamavam, e eles saíram junto. O ramo "legado" do resume
#     (`workflow.resumed` sobre instância em PG) operava sobre a tabela vazia.
#
# Logs do container (30 dias): nenhuma chamada a estas rotas além da própria
# varredura da AUT-58. Quem precisa disparar ou retomar processo usa o caminho
# por POOL do channel-gateway, pelo registro de endereço (`ChannelEndpoint`).
#
# Ficam: o scanner de timeout, que opera sobre as mesmas tabelas vazias, e o
# backfill administrativo. A aposentadoria do serviço e do skill-flow-worker
# inteiros é a `WFL-01`.


# ── Cancel — ROTA REMOVIDA em 2026-08-07 (I5, lacuna 4b) ──────────────────────
#
# Era `POST /v1/workflow/instances/{id}/cancel` devolvendo 410 hard, com a
# mensagem *"cancel webhook sessions via the channel-gateway
# (DELETE /v1/channels/webhook/{session_id})"*.
#
# Dois motivos para apagar em vez de manter o 410:
#
#  1. **O substituto que ela nomeava nunca existiu.** O channel-gateway não tem
#     nenhuma rota DELETE: a superfície webhook é POST trigger/resume/pool/
#     collect/delegate + GET …/status. Um 410 que aponta caminho inexistente é
#     pior que ausência — tem cara de decisão tomada, e por isso ninguém foi
#     conferir. (Mesma forma do docstring de `_claim_lease_key`, que citava uma
#     segunda rede inexistente. Ver TODO § "Lacuna 2".)
#  2. **Havia quatro chamadores vivos**, não zero: ProcessosPage,
#     WorkflowsPage, WorkflowMonitorPage e MonitorTab, todas com
#     `catch { alert(String(e)) }` — o operador confirmava o cancelamento e
#     recebia `Error: HTTP 410`. Foram removidas na mesma mudança.
#
# **Não foi reapontada para `/api/force-complete`** porque a medição mostrou que
# não há endereço: esta tabela tem UM escritor (`:794`) e ele grava
# `session_id: None` hardcoded (`:799`) — cobertura 0% por construção. A sonda
# (`probe_workflow_cancel_callers.sh`) saiu na AUT-64 junto com a leitura de
# `/v1/workflow/instances`, de que ela dependia.
#
# Quem precisar encerrar execução parada usa `POST /api/force-complete/{sid}`
# no mcp-server (BFF), que é endereçado por SESSÃO — a unidade do Arc 19.


# ── Admin helpers ─────────────────────────────────────────────────────────────

def _require_admin(request: Request, x_admin_token: str = Header(default="")):
    """
    Portao das operacoes administrativas. Hoje serve `POST /admin/backfill-events`.

    ⚠️ **Ele FALHAVA ABERTO ate 2026-09-08 (MOD-11).** A guarda era
    `if settings.admin_token and x_admin_token != settings.admin_token`, e o `and`
    fazia do segredo AUSENTE um no-op: sem `PLUGHUB_WORKFLOW_ADMIN_TOKEN` o portao
    aprovava todo mundo. Medido no container: **nenhuma env de admin configurada**,
    logo o portao nunca recusou ninguem desde que existe.

    E a postura oposta a que a § Security fixou em 2026-08-30 para o
    `X-Service-Token` da analytics-api: credencial **ACRESCENTA** porta e nunca
    remove exigencia. Aqui o segredo ausente REMOVIA a exigencia -- a mesma forma do
    `_require_service` da evaluation-api, herdada de demo aberto.

    Segredo ausente agora e **503, nao 200**: e falha de configuracao do servico, nao
    veredicto sobre o chamador, e um 401 mentiria dizendo que a credencial dele esta
    errada. Mesma escolha do `_check_audit_access`, e pela mesma razao -- este portao
    guarda uma MUTACAO administrativa, entao degradar aberto e o que nao se pode.
    """
    settings = _settings(request)
    if not settings.admin_token:
        raise HTTPException(
            503,
            "admin_token nao configurado neste servico (PLUGHUB_WORKFLOW_ADMIN_TOKEN); "
            "a rota administrativa fica indisponivel em vez de aberta",
        )
    if x_admin_token != settings.admin_token:
        raise HTTPException(401, "invalid or missing X-Admin-Token")


# ── Webhooks REMOVIDOS em 2026-09-08 (MOD-11) ────────────────────────────────
#
# Eram 8 rotas: o CRUD (`/v1/workflow/webhooks*`, 7) e a porta publica de disparo
# (`POST /v1/workflow/webhook/{webhook_id}`). Sairam porque este servico deixou de
# ser o registro de endereco de webhook, e ha ADR dizendo qual e:
# `adr-webhook-endpoint-single-registry`. O registro unico e o `ChannelEndpoint` do
# agent-registry (`/v1/channel-endpoints`), editado em `/config/channels` sob
# `config.channels`, e a D6 daquele ADR ja carimbava as linhas daqui como
# procedencia `legacy_token`.
#
# ⚠️ Nao houve migracao de dado porque nao havia dado: `workflow.webhooks` e
# `workflow.webhook_deliveries` foram medidas em ZERO linhas na instalacao, contra
# 13 endpoints webhook ja vivos em `channel_endpoints` (12 `internal` + 1
# `external`). A tela que administrava esta tabela (`WebhooksTab`) saiu no mesmo
# commit. As TABELAS ficam de pe -- apagar schema e outra decisao, e vazio nao
# custa; o que nao pode e continuar existindo uma segunda porta de cadastro.
#
# O DDL delas segue em `db.py` e nao foi tocado, de proposito: remover o CREATE
# TABLE junto tornaria irreversivel por deploy uma remocao que hoje e so de rota.


# ── Admin: historical backfill ────────────────────────────────────────────────

@router.post("/admin/backfill-events")
async def backfill_events(
    request:        Request,
    tenant_id:      str = Query(..., description="Tenant to backfill"),
    limit_per_page: int = Query(1000, description="Instances per page"),
    _auth:          None = Depends(_require_admin),
    pool=Depends(_pool),
):
    """
    Re-emits synthetic workflow.* Kafka events for all historical instances
    of the given tenant that are in PostgreSQL but not yet in ClickHouse.

    Safe to run multiple times: ReplacingMergeTree deduplicates by
    (tenant_id, instance_id, timestamp).

    Returns { instances_processed, events_emitted }.
    """
    settings = _settings(request)
    producer = _producer(request)

    # Status → terminal Kafka event_type
    _TERMINAL = {
        "completed": "workflow.completed",
        "failed":    "workflow.failed",
        "timed_out": "workflow.timed_out",
        "cancelled": "workflow.cancelled",
    }

    instances_processed = 0
    events: list[dict] = []
    offset = 0

    while True:
        page = await db_list_instances(
            pool,
            tenant_id=tenant_id,
            limit=limit_per_page,
            offset=offset,
        )
        if not page:
            break

        for inst in page:
            iid        = inst["id"]
            fid        = inst["flow_id"]
            tid        = inst["tenant_id"]
            created_at = inst.get("created_at") or ""
            completed_at = inst.get("completed_at") or created_at
            status     = inst.get("status", "")

            # workflow.started — always emit
            events.append({
                "event_type":      "workflow.started",
                "timestamp":       created_at,
                "installation_id": inst.get("installation_id") or settings.installation_id,
                "organization_id": inst.get("organization_id") or settings.organization_id,
                "tenant_id":       tid,
                "instance_id":     iid,
                "flow_id":         fid,
                "session_id":      inst.get("session_id"),
                "trigger_type":    "backfill",
                "campaign_id":     inst.get("campaign_id"),
                "pool_id":         inst.get("pool_id"),
            })

            # terminal event
            terminal_type = _TERMINAL.get(status)
            if terminal_type:
                ts = completed_at or created_at
                ev: dict = {
                    "event_type":  terminal_type,
                    "timestamp":   ts,
                    "tenant_id":   tid,
                    "instance_id": iid,
                    "flow_id":     fid,
                }
                if inst.get("campaign_id"):
                    ev["campaign_id"] = inst["campaign_id"]

                if terminal_type == "workflow.completed":
                    try:
                        from datetime import datetime as _dt
                        t0 = _dt.fromisoformat(created_at.replace("Z", "+00:00"))
                        t1 = _dt.fromisoformat(ts.replace("Z", "+00:00"))
                        ev["duration_ms"] = int((t1 - t0).total_seconds() * 1000)
                    except Exception:
                        ev["duration_ms"] = 0
                    ev["outcome"] = inst.get("outcome") or "unknown"

                elif terminal_type == "workflow.failed":
                    ev["current_step"] = None
                    ev["error"]        = "backfill"

                elif terminal_type == "workflow.timed_out":
                    ev["current_step"] = None
                    ev["suspended_at"] = None
                    ev["next_open"]    = None

                elif terminal_type == "workflow.cancelled":
                    ev["cancelled_by"] = "backfill"
                    ev["reason"]       = None

                events.append(ev)

            instances_processed += 1

        offset += len(page)
        if len(page) < limit_per_page:
            break

    emitted = await emit_events_batch(producer, settings.kafka_topic, events)

    logger.info(
        "backfill-events: tenant=%s instances_processed=%d events_emitted=%d",
        tenant_id, instances_processed, emitted,
    )
    return {
        "instances_processed": instances_processed,
        "events_emitted":      emitted,
    }
