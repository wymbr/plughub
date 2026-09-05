"""
reports.py
FastAPI router for the four /reports/* endpoints.

Routes:
  GET /reports/sessions   — session list with filters
  GET /reports/quality    — sentiment event list with filters
  GET /reports/usage      — usage event list with filters

Common query params (all endpoints):
  tenant_id     string   required
  from_dt       ISO8601  optional, default: 7 days ago
  to_dt         ISO8601  optional, default: now
  page          int      optional, default: 1
  page_size     int      optional, default: 100; max 1000 (JSON) / 10000 (CSV)
  format        json|csv optional, default: json

Endpoint-specific filter params are documented per endpoint below.

CSV response:
  Content-Type: text/csv
  Content-Disposition: attachment; filename="{report}_{date}.csv"
  Body: RFC 4180 CSV (header row + data rows)
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from .config import get_settings
from .pool_auth import PoolPrincipal, optional_pool_principal
from .pricing_client import get_configured_agent_capacity
from .reports_query import (
    _ch_fmt,
    _clamp_page_size,
    _default_from,
    _default_to,
    _to_csv,
    query_agent_performance_daily,
    query_agent_performance_report,
    query_campaigns_report,
    query_contact_insights_report,
    query_agents_compare,
    query_agents_cross,
    query_evaluations_report,
    query_evaluations_summary,
    query_sentiment_events_report,
    query_participation_report,
    query_quality_report,
    query_segments_report,
    query_wrapup_summary,
    query_agent_availability,
    query_agent_timeline,
    query_pools_volume,
    query_pools_queue,
    query_pools_occupancy,
    query_events,
    query_session_complexity,
    query_sessions_report,
    query_contacts_series_report,
    query_token_breakdown_report,
    query_journeys_report,
    query_customer_360,
    query_customer_voice,
    CV_INSTRUMENTS,
    query_session_trace,
    query_usage_report,
    query_workflow_summary,
    query_workflows_report,
    query_agent_events_series,
    query_agent_events_summary,
    query_agent_events_categories,
    query_agent_events_tree,
    query_agent_events_epochs,
    query_evaluator_calibration,
)
from .timeseries_query import (
    query_handle_time_timeseries,
    query_score_timeseries,
    query_volume_timeseries,
    timeseries_to_csv,
)

logger = logging.getLogger("plughub.analytics.reports")

router = APIRouter(prefix="/reports")


# ══════════════════════════════════════════════════════════════════════════════
# EXIGIR CREDENCIAL e RECORTAR LINHA são DOIS fatos, e só o primeiro está fechado
# ══════════════════════════════════════════════════════════════════════════════
#
# `Depends(optional_pool_principal)` responde UMA pergunta: *"quem está chamando?"*.
# Ele 401 sem `Authorization` (desde 2026-08-27) e devolve `accessible_pools`. Quem
# decide se aquela lista vira `WHERE` é o HANDLER, passando-a adiante — e a maioria
# das rotas deste arquivo passa.
#
# ── O que mudou em 2026-08-29, e o que NÃO mudou ─────────────────────────────
# Doze rotas daqui não declaravam principal algum. Quatro foram medidas ao vivo
# respondendo **200 anônimo**, entre elas `/reports/customers/{id}/360` — dado de
# CLIENTE. Elas passaram intactas pelos dois censos que já existiam porque ambos
# contam QUEM DECIDE (C1: quem decodifica JWT · C4: quem resolve escopo de pool), e
# uma rota sem dependência nenhuma não tem decisor para contar. Agora todas exigem
# credencial, e o eixo tem censo próprio: `probe_route_credential_coverage.sh`.
#
# O que NÃO mudou: nenhuma delas RECORTA LINHA. As `query_*` que servem estas doze
# (`query_usage_report`, `query_workflows_report`, `query_campaigns_report`,
# `query_evaluations_*`, `query_customer_360`, `query_agent_events_*`,
# `query_evaluator_calibration`) **não aceitam `accessible_pools`** — não é um
# argumento que alguém esqueceu de passar, é filtro que não existe. Fabricá-lo aqui
# seria inventar, por rota, qual coluna é "o pool desta agregação", e o precedente
# está medido: a F2 do ADR de relatórios encontrou um filtro de canal que não
# filtrava, ESVAZIAVA — subconsulta que o ClickHouse recusava, `except` devolvendo
# `data_unavailable`, endpoint respondendo 200 com zero linha, 683 testes verdes.
# Recorte inventado quebra para o lado que ninguém vê.
#
# Consequência ACEITA e nomeada: um operador escopado a um pool, autenticado, lê
# estes agregados INTEIROS. É estritamente melhor que o anônimo lê-los inteiros, e
# estritamente pior que o alvo. A dívida está contada no `TODO.md`
# (§ "Recorte de linha nas rotas recém-gateadas"), com a lista das doze e o critério
# por rota — nunca como "por enquanto".
#
# Ao escrever rota NOVA aqui: `pool_principal` é obrigatório e o gate reprova sem
# ele; passar `accessible_pools` à query é decisão sua, e a resposta default é SIM.


# ─── helpers ──────────────────────────────────────────────────────────────────

def _today_label() -> str:
    from datetime import datetime
    return datetime.utcnow().strftime("%Y-%m-%d")


def _respond(data: dict, fmt: str, filename: str) -> Response:
    if fmt == "csv":
        csv_body = _to_csv(data.get("data", []))
        return Response(
            content=csv_body,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    status_code = 503 if data.get("error") else 200
    # Include meta at top level alongside data
    return JSONResponse(content=data, status_code=status_code)


# ─── GET /reports/sessions ────────────────────────────────────────────────────



# ─── AUT-01 · isencoes DECLARADAS de recorte de pool ──────────────────────────
#
# Toda rota deste arquivo cai em exatamente uma de tres classes, e o portao
# `infra/test/probe_report_row_scope.sh` reprova a que nao cair em nenhuma:
#
#   ESCOPADA  passa `accessible_pools` a query               (35 rotas)
#   ISENTA    `_SCOPE_EXEMPT` — decidido, sem gatilho          (2)
#   DIVIDA    `_SCOPE_DEBT`   — nao expressavel, COM gatilho   (2)
#
# **A isencao e DECLARADA, nunca deduzida da ausencia.** Foi a ausencia — 13 rotas que
# simplesmente nao passavam o argumento — que deixou o vazamento invisivel por meses:
# uma rota sem recorte e indistinguivel de uma rota que decidiu nao recortar, e so uma
# das duas e defeito. O mesmo padrao do `/v1/health` no censo de credencial: uma
# isenta, NOMEADA, nunca omitida.
#
# E sao DUAS tabelas, porque "decidimos nao recortar" e "ainda nao sabemos recortar"
# sao fatos diferentes: a primeira nao tem gatilho, a segunda tem, e junta-las faria a
# divida herdar a tranquilidade da decisao.
_SCOPE_EXEMPT: dict[str, str] = {
    "/resources/tokens":
        "O gasto de uma conta LLM e fato do TENANT e nao se reparte por pool — decisao "
        "de 2026-08-29, ja escrita no docstring da rota, que inclusive RECUSA `?pool_id=` "
        "com 422 para nao devolver a soma de um subconjunto sob o rotulo do todo. "
        "Recortar por `accessible_pools` aqui seria a mesma mentira pela porta de tras.",
    "/customer-voice/instruments":
        "Catalogo estatico (`CV_INSTRUMENTS`): metrica -> {source, rollup, graos, label}. "
        "Nao le tenant nem ClickHouse, entao nao ha linha para recortar.",
}

# Divida: o recorte NAO e expressavel hoje, e o gatilho esta nomeado.
#
# ⚠️ A primeira versao deste arco RECUSAVA (403) o chamador escopado nestas duas rotas,
# e a medicao derrubou a decisao no mesmo dia: **o admin tambem e um chamador
# escopado** — `admin@plughub.local` carrega uma lista de 36 pools, nao a ausencia de
# restricao (`accessible_pools is None` so acontece para principal de SERVICO). A
# recusa devolvia 403 ao administrador de verdade, para defender **zero linha** — as
# duas tabelas estao vazias nesta instalacao. E a D14.1 do `CLAUDE.md` ao contrario:
# publicar uma defesa contra um dano que nao existe, causando um que existe.
#
# O discriminador que a recusa PRECISARIA ("o escopo deste chamador cobre o tenant
# inteiro?") exige o universo de pools do registry — dependencia nova, com caminho de
# degradacao proprio, por duas tabelas sem produtor. Nao se paga isso agora. O que
# torna a recusa viavel depois e o claim `unrestricted` chegar ao admin (AUT-15).
_SCOPE_DEBT: dict[str, str] = {
    "/campaigns":
        "`collect_events` nao tem `session_id` nem `pool_id` (so `collect_token` e "
        "`instance_id`). Ha um caminho concebivel — `instance_id` -> "
        "`workflow_events.pool_id` — e ele NAO foi construido: as duas tabelas estao "
        "VAZIAS aqui, entao o join entraria sem nunca ter sido visto funcionar, que e "
        "como nasceu o filtro de canal da F2 (o que filtrava esvaziando, 683 testes "
        "verdes). GATILHO: quando `collect_events` tiver produtor, o join vira "
        "exercivel e o recorte e obrigatorio.",
    "/evaluator-calibration":
        "`calibration_events` so carrega `evaluator_id`; o eixo do relatorio e o "
        "AVALIADOR. Pode ser que a resposta certa seja isencao DECIDIDA (como "
        "`/resources/tokens`) — mas isso e decisao do dono, e presumi-la aqui seria "
        "converter uma divida em politica sem ninguem ter escolhido. GATILHO: quando "
        "a tabela tiver produtor, decidir entre recorte e isencao explicita.",
}


@router.get("/sessions")
async def report_sessions(
    request:          Request,
    tenant_id:        str           = Query(...,    description="Tenant identifier"),
    from_dt:          Optional[str] = Query(None,   description="ISO8601 start (default: 7d ago)"),
    to_dt:            Optional[str] = Query(None,   description="ISO8601 end (default: now)"),
    channel:          Optional[str] = Query(None,   description="Filter by channel"),
    outcome:          Optional[str] = Query(None,   description="Filter by session outcome"),
    close_reason:     Optional[str] = Query(None,   description="Filter by close_reason"),
    pool_id:          Optional[str] = Query(None,   description="\"Atendido por\": sessões em que ALGUM segmento pertenceu a este pool (subconsulta em segments, D12)"),
    entry_pool_id:    Optional[str] = Query(None,   description="\"Entrou por\": o pool que RECEBEU o contato (sessions.pool_id, first-write-wins desde a F1b). Compõe por AND com pool_id — as duas perguntas são diferentes."),
    direction:        Optional[str] = Query(None,   pattern="^(inbound|outbound|internal)$", description="Direção do ACESSO (ADR D8), derivada de spawn_reason + canal: inbound=o cliente procurou · outbound=a plataforma procurou (collect) · internal=maquinaria (trigger/delegate/webhook). Sessão de spawn_reason DESCONHECIDO não é reivindicada por nenhuma das três — Σ das três ≤ total, e a diferença é a população não classificada."),
    session_id:       Optional[str] = Query(None,   description="Filter by exact session_id"),
    root_session_id:  Optional[str] = Query(None,   description="Journey J2 drill: all member sessions of a journey (root)"),
    spawned_from_root: Optional[str] = Query(None,  description="Journey T5: sessions BORN from this journey but belonging to ANOTHER (journey: new) — the edges that cross the boundary"),
    origin_session_id: Optional[str] = Query(None,  description="Timeline do contato (S1): filhas de UM SALTO desta sessão (origin_session_id = valor). NÃO é journey — é a aresta direta; use root_session_id para o processo inteiro. Isento do filtro de contato (não é listagem)."),
    agent_id:         Optional[str] = Query(None,   description="Filter by agent participant_id (any segment)"),
    insight_category: Optional[str] = Query(None,   description="Filter: sessions with this insight category"),
    insight_tags:     Optional[str] = Query(None,   description="Comma-separated insight tags (AND logic)"),
    ani:              Optional[str] = Query(None,   description="Filter by ANI/source identifier (partial match)"),
    dnis:             Optional[str] = Query(None,   description="Filter by DNIS/destination identifier (partial match)"),
    status:           Optional[str] = Query(None,   description="Filter by session status (active|suspended|closed) — Arc 19"),
    origin:           str           = Query("live",  pattern="^(live|import|reeval)$", description="Substrate origin (ADR): live=produção (default), import, reeval"),
    scope:            str           = Query("contacts", pattern="^(contacts|all)$", description="contacts=só contatos de cliente (default, = E2f); all=inclui sessões de pool interno (wrap-up, dispatch) como linhas extras. NÃO existe em endpoints de agregado."),
    page:             int           = Query(1,       ge=1),
    page_size:        int           = Query(100,     ge=1),
    format:           str           = Query("json",  pattern="^(json|csv)$"),
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Session list for the given tenant and time window.

    Columns: session_id, tenant_id, channel, pool_id, customer_id,
             opened_at, closed_at, close_reason, outcome,
             wait_time_ms, handle_time_ms, ani, dnis, segment_count

    `scope` (ADR wrapup-detached-pull §7) — **visibilidade, nunca contagem**. Este é
    o único endpoint que o aceita: agregados (TMA, ocupação, `/reports/agents/*`,
    `/reports/pools/*`) e a listagem topo de `/reports/journeys` seguem excluindo
    pool interno incondicionalmente. Mesmo aqui, `meta.total_contacts` é sempre o
    número do domínio de contato — a tela mostra "N contatos · M internas", nunca
    um total somado.
    """
    ps = _clamp_page_size(page_size, format == "csv")
    tags_list = [t.strip() for t in insight_tags.split(",") if t.strip()] if insight_tags else None
    data = await query_sessions_report(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        channel          = channel,
        outcome          = outcome,
        close_reason     = close_reason,
        pool_id          = pool_id,
        entry_pool_id    = entry_pool_id,
        direction        = direction,
        session_id       = session_id,
        root_session_id  = root_session_id,
        spawned_from_root = spawned_from_root,
        origin_session_id = origin_session_id,
        agent_id         = agent_id,
        insight_category = insight_category,
        insight_tags     = tags_list,
        accessible_pools        = pool_principal.accessible_pools,
        supervised_agent_types  = pool_principal.supervised_agent_types,
        ani              = ani,
        dnis             = dnis,
        status           = status,
        origin           = origin,
        scope            = scope,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"sessions_{_today_label()}.csv")


# ─── GET /reports/agents — REMOVIDO (2026-07-28) ─────────────────────────────
#
# Servia linhas cruas de `agent_events`, tabela descontinuada (substrato derivado
# que duplicava `segments` com menos campos). O endpoint não tinha nenhum chamador
# em todo o repo.
#
# Substitutos, ambos sobre `segments`:
#   GET /reports/agents/performance        — performance agregada por agente
#   GET /reports/agent-performance/daily   — tendência diária
#
# Não confundir com GET /reports/agent-events/* — esses são Arc 12
# (`agent_business_events`, KPIs que o agente declara pela tool `agent_event`)
# e permanecem. A semelhança de nome é histórica e já induziu erro no TODO.md.


# ─── GET /reports/contacts/series ────────────────────────────────────────────
#
# A SÉRIE da superfície A (F2 do `adr-relatorios-duas-superficies-e-lentes.md`).
#
# Aceita o MESMO conjunto de filtros de `GET /reports/sessions` — não por simetria
# estética, mas porque as duas respostas aparecem lado a lado na mesma tela, sob a
# mesma barra de filtro. Um endpoint de série com menos filtros que a lista produz o
# defeito mais barato desta superfície: dois números certos para perguntas diferentes,
# sem nada dizendo qual foi respondida.
#
# Por que não estender `/reports/timeseries/volume`: ele tem chamadores vivos
# (dashboards, página de avaliação) com contrato mais simples — `{bucket, value}` e um
# só recorte de pool. Alargá-lo mudaria a resposta deles.

@router.get("/contacts/series")
async def report_contacts_series(
    request:          Request,
    tenant_id:        str           = Query(...,    description="Tenant identifier"),
    metric:           str           = Query("volume", pattern="^(volume|duration|resources|tokens)$",
                                            description="volume=contatos · duration=avg(handle_time_ms) do PRÓPRIO contato · resources=os dois números da D4 (instâncias distintas × trocas de mão) mais o pico simultâneo · tokens=consumo de LLM atribuído ao contato (T3; entrada e saída NÃO se somam)"),
    interval:         int           = Query(60,     ge=1, le=1440, description="Bucket em minutos"),
    from_dt:          Optional[str] = Query(None,   description="ISO8601 start (default: 7d ago)"),
    to_dt:            Optional[str] = Query(None,   description="ISO8601 end (default: now)"),
    channel:          Optional[str] = Query(None),
    outcome:          Optional[str] = Query(None),
    close_reason:     Optional[str] = Query(None),
    pool_id:          Optional[str] = Query(None,   description='"Atendido por" — igual ao de /reports/sessions'),
    entry_pool_id:    Optional[str] = Query(None,   description='"Entrou por" — igual ao de /reports/sessions'),
    direction:        Optional[str] = Query(None,   pattern="^(inbound|outbound|internal)$"),
    session_id:       Optional[str] = Query(None),
    root_session_id:  Optional[str] = Query(None),
    agent_id:         Optional[str] = Query(None),
    insight_category: Optional[str] = Query(None),
    insight_tags:     Optional[str] = Query(None),
    ani:              Optional[str] = Query(None),
    dnis:             Optional[str] = Query(None),
    status:           Optional[str] = Query(None),
    origin:           str           = Query("live", pattern="^(live|import|reeval)$"),
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Série temporal sobre a MESMA população que `GET /reports/sessions` listaria.

    Resposta: `{buckets: [{bucket, values: {...}, sample}], meta: {...}}`.

    `sample` por bucket é obrigatório e é a razão de a resposta não ser
    `{bucket, value}`: sem ele, média zero e ausência de contato chegam idênticas à
    tela. `meta.series` declara o que foi computado — a resposta se descreve, em vez
    de deixar a UI adivinhar pelo nome da chave.

    **`scope` não existe aqui**, de propósito: agregado nunca conta pool interno
    (guardrail §7.2 do `adr-wrapup-detached-pull`). A lista o oferece porque é
    visibilidade; a série é contagem.
    """
    tags_list = [t.strip() for t in insight_tags.split(",") if t.strip()] if insight_tags else None
    data = await query_contacts_series_report(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        metric    = metric,
        interval  = interval,
        channel          = channel,
        outcome          = outcome,
        close_reason     = close_reason,
        pool_id          = pool_id,
        entry_pool_id    = entry_pool_id,
        direction        = direction,
        session_id       = session_id,
        root_session_id  = root_session_id,
        agent_id         = agent_id,
        insight_category = insight_category,
        insight_tags     = tags_list,
        accessible_pools       = pool_principal.accessible_pools,
        supervised_agent_types = pool_principal.supervised_agent_types,
        ani              = ani,
        dnis             = dnis,
        status           = status,
        origin           = origin,
    )
    return JSONResponse(content=data, status_code=503 if data.get("error") else 200)


# ─── GET /reports/contacts/tokens/breakdown ──────────────────────────────────
#
# A metade da pergunta que a série não responde (T3): *quem* gastou, *de qual conta*
# e *com qual modelo*. Endpoint PRÓPRIO, e não um `breakdown_by` da série, porque a
# forma da resposta é outra — linhas, não buckets — e espremer as duas no mesmo
# envelope obrigaria a UI a adivinhar qual veio.
#
# Mesmos filtros da lista, pelo mesmo motivo da série: a tabela aparece embaixo do
# gráfico, sob a mesma barra.

@router.get("/contacts/tokens/breakdown")
async def report_token_breakdown(
    request:          Request,
    tenant_id:        str           = Query(..., description="Tenant identifier"),
    from_dt:          Optional[str] = Query(None),
    to_dt:            Optional[str] = Query(None),
    limit:            int           = Query(100, ge=1, le=500),
    channel:          Optional[str] = Query(None),
    outcome:          Optional[str] = Query(None),
    close_reason:     Optional[str] = Query(None),
    pool_id:          Optional[str] = Query(None),
    entry_pool_id:    Optional[str] = Query(None),
    direction:        Optional[str] = Query(None, pattern="^(inbound|outbound|internal)$"),
    session_id:       Optional[str] = Query(None),
    root_session_id:  Optional[str] = Query(None),
    agent_id:         Optional[str] = Query(None),
    insight_category: Optional[str] = Query(None),
    insight_tags:     Optional[str] = Query(None),
    ani:              Optional[str] = Query(None),
    dnis:             Optional[str] = Query(None),
    status:           Optional[str] = Query(None),
    origin:           str           = Query("live", pattern="^(live|import|reeval)$"),
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Consumo de LLM por (conta × modelo × origem da chamada), com o pool que atendeu.

    Linhas: `pool_id`, `account_config_id`, `model_id`, `model_profile`, `source`,
    `tokens_in`, `tokens_out`, `sessions`, `events`.

    **Atribuição por `segment_id` (D1)** — o pool da SESSÃO é o de ENTRADA (D10), então
    creditar por ele daria o gasto do especialista de IA ao pool onde o contato começou.
    Recortado em `USAGE_ATTRIBUTION_EPOCH`: agrupamento por atribuição não pode misturar
    "não media" com "não informado".

    `meta.rows_without_pool` conta as linhas cujo `segment_id` não casou com segmento
    algum — sintoma de chave não propagada, nomeado em vez de escondido num travessão.
    """
    tags_list = [t.strip() for t in insight_tags.split(",") if t.strip()] if insight_tags else None
    data = await query_token_breakdown_report(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        limit     = limit,
        channel = channel, outcome = outcome, close_reason = close_reason,
        pool_id = pool_id, entry_pool_id = entry_pool_id, direction = direction,
        session_id = session_id, root_session_id = root_session_id,
        agent_id = agent_id,
        insight_category = insight_category, insight_tags = tags_list,
        accessible_pools       = pool_principal.accessible_pools,
        supervised_agent_types = pool_principal.supervised_agent_types,
        ani = ani, dnis = dnis, status = status, origin = origin,
    )
    return JSONResponse(content=data, status_code=503 if data.get("error") else 200)


# ─── GET /reports/contact-insights ───────────────────────────────────────────

@router.get("/contact-insights")
async def report_contact_insights(
    request:      Request,
    tenant_id:    str           = Query(...,    description="Tenant identifier"),
    from_dt:      Optional[str] = Query(None,   description="ISO8601 start (default: 7d ago)"),
    to_dt:        Optional[str] = Query(None,   description="ISO8601 end (default: now)"),
    session_id:   Optional[str] = Query(None,   description="Filter by session_id"),
    category:     Optional[str] = Query(None,   description="Filter by insight category"),
    tags:         Optional[str] = Query(None,   description="Comma-separated tags (AND logic)"),
    insight_type: Optional[str] = Query(None,   description="Filter by full insight_type (e.g. insight.registered)"),
    page:         int           = Query(1,       ge=1),
    page_size:    int           = Query(100,     ge=1),
    format:       str           = Query("json",  pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Business events registered via insight_register MCP tool during agent flows.

    Examples: service executed (cancelamento, portabilidade), errors (erro_consulta_saldo).

    Filter by category + tags to find all contacts where a given business event occurred.

    Columns: insight_id, tenant_id, session_id, insight_type, category, value, tags, agent_id, timestamp
    """
    ps = _clamp_page_size(page_size, format == "csv")
    tags_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    data = await query_contact_insights_report(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        session_id   = session_id,
        category     = category,
        tags         = tags_list,
        insight_type = insight_type,
        accessible_pools = pool_principal.accessible_pools,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"contact_insights_{_today_label()}.csv")


# ─── GET /reports/quality ─────────────────────────────────────────────────────

@router.get("/quality")
async def report_quality(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_dt:        Optional[str] = Query(None,  description="ISO8601 start (default: 7d ago)"),
    to_dt:          Optional[str] = Query(None,  description="ISO8601 end (default: now)"),
    pool_id:        Optional[str] = Query(None,  description="Filter by pool_id"),
    category:       Optional[str] = Query(None,  description="Filter by sentiment category"),
    page:           int           = Query(1,      ge=1),
    page_size:      int           = Query(100,    ge=1),
    format:         str           = Query("json", pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Per-turn sentiment event list. Useful for CSAT quality analysis.

    category filter: satisfied | neutral | frustrated | angry

    Columns: event_id, tenant_id, session_id, pool_id, score, category, timestamp
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_sentiment_events_report(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        pool_id          = pool_id,
        category         = category,
        accessible_pools = pool_principal.accessible_pools,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"quality_{_today_label()}.csv")


# ─── GET /reports/usage ───────────────────────────────────────────────────────

@router.get("/usage")
async def report_usage(
    request:          Request,
    tenant_id:        str           = Query(...,   description="Tenant identifier"),
    from_dt:          Optional[str] = Query(None,  description="ISO8601 start (default: 7d ago)"),
    to_dt:            Optional[str] = Query(None,  description="ISO8601 end (default: now)"),
    dimension:        Optional[str] = Query(None,  description="Filter by dimension"),
    source_component: Optional[str] = Query(None,  description="Filter by source_component"),
    page:             int           = Query(1,      ge=1),
    page_size:        int           = Query(100,    ge=1),
    format:           str           = Query("json", pattern="^(json|csv)$"),
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Raw usage event list. Useful for billing and metering BI exports.

    dimension filter: sessions | messages | llm_tokens_input | llm_tokens_output |
                      webchat_attachments | whatsapp_conversations | ...

    Columns: event_id, tenant_id, session_id, dimension, quantity, source_component, timestamp
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_usage_report(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        dimension        = dimension,
        source_component = source_component,
        page      = page,
        page_size = ps,
        accessible_pools = pool_principal.accessible_pools,
    )
    return _respond(data, format, f"usage_{_today_label()}.csv")


# ─── GET /reports/workflows ───────────────────────────────────────────────────

@router.get("/workflows")
async def report_workflows(
    request:     Request,
    tenant_id:   str           = Query(...,    description="Tenant identifier"),
    from_dt:     Optional[str] = Query(None,   description="ISO8601 start (default: 7d ago)"),
    to_dt:       Optional[str] = Query(None,   description="ISO8601 end (default: now)"),
    flow_id:     Optional[str] = Query(None,   description="Filter by flow_id"),
    status:      Optional[str] = Query(None,   description="Filter by workflow status"),
    campaign_id: Optional[str] = Query(None,   description="Filter by campaign_id"),
    page:        int           = Query(1,       ge=1),
    page_size:   int           = Query(100,     ge=1),
    format:      str           = Query("json",  pattern="^(json|csv)$"),
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Workflow lifecycle event list.

    status filter: active | suspended | completed | failed | timed_out | cancelled

    Columns: event_id, tenant_id, instance_id, flow_id, campaign_id,
             event_type, status, current_step, suspend_reason, decision,
             outcome, duration_ms, wait_duration_ms, error, timestamp
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_workflows_report(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        flow_id     = flow_id,
        status      = status,
        campaign_id = campaign_id,
        page      = page,
        page_size = ps,
        accessible_pools = pool_principal.accessible_pools,
    )
    return _respond(data, format, f"workflows_{_today_label()}.csv")


# ─── GET /reports/campaigns ───────────────────────────────────────────────────

@router.get("/campaigns")
async def report_campaigns(
    request:     Request,
    tenant_id:   str           = Query(...,    description="Tenant identifier"),
    from_dt:     Optional[str] = Query(None,   description="ISO8601 start (default: 7d ago)"),
    to_dt:       Optional[str] = Query(None,   description="ISO8601 end (default: now)"),
    campaign_id: Optional[str] = Query(None,   description="Filter by campaign_id"),
    channel:     Optional[str] = Query(None,   description="Filter by channel"),
    status:      Optional[str] = Query(None,   description="Filter by collect status"),
    page:        int           = Query(1,       ge=1),
    page_size:   int           = Query(100,     ge=1),
    format:      str           = Query("json",  pattern="^(json|csv)$"),
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Campaign collect event list + per-campaign aggregate summary.

    status filter: requested | sent | responded | timed_out

    Response includes:
      data    — individual collect_event rows
      summary — per-campaign aggregate (total, responded, timed_out, response_rate_pct, avg_elapsed_ms)
      meta    — page / total / date range

    Columns: collect_token, tenant_id, instance_id, flow_id, campaign_id,
             step_id, target_type, channel, interaction, status,
             send_at, responded_at, elapsed_ms, timestamp
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_campaigns_report(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        campaign_id = campaign_id,
        channel     = channel,
        status      = status,
        page      = page,
        page_size = ps,
    )
    # For CSV export, flatten summary into data
    if format == "csv":
        return _respond({"data": data.get("data", [])}, format, f"campaigns_{_today_label()}.csv")
    return _respond(data, format, f"campaigns_{_today_label()}.csv")


# ─── GET /reports/resources/tokens ────────────────────────────────────────────
#
# A metade B da lente de token (F3 · D2): **quanto cada CONTA gastou**, não quanto os
# contatos custaram. É rota própria, e não um parâmetro do breakdown da superfície A,
# porque a POPULAÇÃO é outra — ver o cabeçalho de `resources_query.py` para a medição
# que decidiu isso (reusar o endpoint de lá publicaria 47% do consumo, em silêncio).

@router.get("/resources/tokens")
async def report_resource_tokens(
    request:        Request,
    tenant_id:      str           = Query(..., description="Tenant identifier"),
    from_dt:        Optional[str] = Query(None),
    to_dt:          Optional[str] = Query(None),
    limit:          int           = Query(100, ge=1, le=500),
    format:         str           = Query("json", pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Consumo de LLM agregado por conta × modelo × origem.

    **Não aceita filtro de pool, e isso é decisão declarada**: a conta é recurso de
    TENANT, e o gasto dela não se reparte por pool. Um `?pool_id=` aqui devolveria a
    soma de um subconjunto sob o rótulo do todo. A lente declara
    `honors: 'period_only'` e a tela o diz — o campo do contrato existe para essa
    afirmação não viver só aqui.
    """
    from .resources_query import query_account_tokens

    # RECUSA explícita, em vez do silêncio default do FastAPI. Parâmetro desconhecido
    # é ignorado sem aviso, e `pool_id` não é desconhecido: ele EXISTE em todas as
    # rotas vizinhas. Quem o manda aqui está pedindo um recorte, e receberia o total do
    # tenant acreditando que veio filtrado — a mentira mais cara desta superfície,
    # porque o número é plausível. Descoberto pelo teste que eu escrevi para provar o
    # contrário (`test_pool_id_nao_e_parametro`), que reprovou com 200.
    if "pool_id" in request.query_params or "entry_pool_id" in request.query_params:
        raise HTTPException(
            status_code=422,
            detail="pool_scope_not_applicable: o gasto de uma conta LLM é do TENANT e "
                   "não se reparte por pool; filtrar aqui devolveria a soma de um "
                   "subconjunto sob o rótulo do todo",
        )

    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt, upper=True) if to_dt else _default_to()
    data = await query_account_tokens(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        since     = since,
        until     = until,
        limit     = limit,
    )
    return _respond(data, format, f"resource_tokens_{_today_label()}.csv")


# ─── GET /reports/participation ───────────────────────────────────────────────

@router.get("/participation")
async def report_participation(
    request:        Request,
    tenant_id:      str           = Query(...,    description="Tenant identifier"),
    from_dt:        Optional[str] = Query(None,   description="ISO8601 start (default: 7d ago)"),
    to_dt:          Optional[str] = Query(None,   description="ISO8601 end (default: now)"),
    session_id:     Optional[str] = Query(None,   description="Filter by session_id"),
    pool_id:        Optional[str] = Query(None,   description="Filter by pool_id"),
    agent_type_id:  Optional[str] = Query(None,   description="Filter by agent_type_id"),
    role:           Optional[str] = Query(None,   description="Filter by participant role (primary|specialist|supervisor)"),
    page:           int           = Query(1,       ge=1),
    page_size:      int           = Query(100,     ge=1),
    format:         str           = Query("json",  pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Participant interval list — who joined which session, when, and for how long.

    Uses participation_intervals (ReplacingMergeTree) — deduplicated via FINAL at query time.
    A row without left_at means the participant is still active (or the left event
    hasn't been processed yet).

    role filter: primary | specialist | supervisor | evaluator | reviewer

    Columns: event_id, session_id, tenant_id, participant_id, pool_id,
             agent_type_id, role, agent_type, conference_id,
             joined_at, left_at, duration_ms, timestamp
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_participation_report(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        session_id       = session_id,
        pool_id          = pool_id,
        agent_type_id    = agent_type_id,
        role             = role,
        accessible_pools = pool_principal.accessible_pools,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"participation_{_today_label()}.csv")


# ─── /reports/segments (Arc 5 — ContactSegment) ─────────────────────────────

@router.get("/segments")
async def get_segments_report(
    request:        Request,
    tenant_id:      str,
    from_dt:        str | None    = None,
    to_dt:          str | None    = None,
    session_id:     str | None    = None,
    root_session_id: str | None   = Query(None, description="Journey (D10): todos os segmentos das sessões do processo. ISENTA a janela de data."),
    pool_id:        str | None    = None,
    agent_type_id:  str | None    = None,
    role:           str | None    = None,
    outcome:        str | None    = None,
    origin:         str           = Query("live", pattern="^(live|import|reeval)$", description="Substrate origin (ADR): live=produção (default), import, reeval"),
    page:           int           = 1,
    page_size:      int           = 100,
    format:         str | None    = None,
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Returns ContactSegment rows — one per agent participation window in a session.

    Each row represents a single agent's contiguous presence in a session:
    - sequence_index  : order among primary (sequential) segments (0, 1, 2…)
    - parent_segment_id: non-null for conference/parallel specialist segments
    - ended_at        : null if the participant is still active
    - duration_ms     : populated on participant_left event

    Filters: session_id, root_session_id, pool_id, agent_type_id, role, outcome
    role:    primary | specialist | supervisor | evaluator | reviewer
    outcome: resolved | escalated | transferred | abandoned | timeout

    `root_session_id` (D10) devolve os segmentos de TODAS as sessões do processo
    (proveniência ∪ alias, resolvido por union-find) e **isenta a janela de data** —
    uma journey que atravessa semanas voltava truncada em silêncio. Mesma decisão já
    tomada em `/reports/journeys`.

    Columns: segment_id, session_id, tenant_id, participant_id, pool_id,
             agent_type_id, instance_id, role, agent_type,
             parent_segment_id, sequence_index,
             started_at, ended_at, duration_ms,
             outcome, close_reason, handoff_reason, issue_status, conference_id
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_segments_report(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        session_id       = session_id,
        root_session_id  = root_session_id,
        pool_id          = pool_id,
        agent_type_id    = agent_type_id,
        role             = role,
        outcome          = outcome,
        origin           = origin,
        accessible_pools       = pool_principal.accessible_pools,
        supervised_agent_types = pool_principal.supervised_agent_types,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"segments_{_today_label()}.csv")


# ─── /reports/wrapup-summary (I5 / ADR § D7b, fatia 2) ──────────────────────

@router.get("/wrapup-summary")
async def get_wrapup_summary(
    request:        Request,
    tenant_id:      str,
    from_dt:        str | None    = None,
    to_dt:          str | None    = None,
    group_by:       str           = Query("agent", pattern="^(agent|pool)$"),
    pool_id:        str | None    = None,
    origin:         str           = Query("live", pattern="^(live|import|reeval)$", description="Substrate origin (ADR): live=produção (default), import, reeval"),
    format:         str | None    = None,
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Desfecho das pendências de wrap-up (trabalho author-bound) no período.

    Contraparte RETROSPECTIVA do Monitor › Pendências, que mostra só o vivo — e
    que tem horizonte de ~25 h (o ledger no Redis expira com o prazo do delegate).
    Aqui o histórico é permanente, porque vive em `segments`.

    Escopo: pools com sufixo `-int`. O trio de `close_reason` sozinho não serve de
    filtro — `task_submitted` também é escrito por claimante de APROVAÇÃO, que é
    trabalho pooled num pool de contato.

    Uma linha por agente (`user_login`) ou por pool, conforme `group_by`:
      total, submitted, expired, supervisor_closed, avg_fill_ms, last_seen
    `avg_fill_ms` é a média só dos SUBMETIDOS — é o tempo de ACW real; a duração de
    um item expirado mede abandono, não trabalho.

    `totals.unfilled_rate` = (expired + supervisor_closed) / total — a "% de
    contatos sem disposição" que a D4 nomeia como ganho.
    """
    data = await query_wrapup_summary(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        group_by  = group_by,
        pool_id   = pool_id,
        origin    = origin,
        accessible_pools = pool_principal.accessible_pools,
    )
    return _respond(data, format, f"wrapup_summary_{_today_label()}.csv")


# ─── /reports/agents/performance (Arc 5 — aggregate per agent) ──────────────

@router.get("/agents/performance")
async def get_agent_performance_report(
    request:        Request,
    tenant_id:      str,
    from_dt:        str | None    = None,
    to_dt:          str | None    = None,
    pool_id:        str | None    = None,
    agent_type_id:  str | None    = None,
    role:           str | None    = None,
    origin:         str           = Query("live", pattern="^(live|import|reeval)$", description="Substrate origin (ADR): live=produção (default), import, reeval"),
    format:         str | None    = None,
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Returns aggregate performance metrics per (agent_type_id, pool_id, role).

    One row per distinct agent × pool × role combination observed in the
    segments table (Arc 5 ContactSegment). No pagination — the cardinality
    is bounded by the number of registered agent types × pools.

    Metrics per group:
      - total_sessions     : number of participation windows
      - avg_duration_ms    : mean handle time (null if no completed segments)
      - escalation_rate    : fraction of sessions with outcome='escalated'
      - handoff_rate       : fraction of sessions with a non-null handoff_reason
      - resolved_count / escalated_count / transferred_count /
        abandoned_count / timeout_count / handoff_count : raw breakdowns

    Filters: pool_id, agent_type_id, role, from_dt, to_dt
    role:    primary | specialist | supervisor | evaluator | reviewer
    """
    data = await query_agent_performance_report(
        client           = request.app.state.store.new_client(),
        database         = request.app.state.store._database,
        tenant_id        = tenant_id,
        from_dt          = from_dt,
        to_dt            = to_dt,
        pool_id          = pool_id,
        agent_type_id    = agent_type_id,
        role                   = role,
        origin                 = origin,
        accessible_pools       = pool_principal.accessible_pools,
        supervised_agent_types = pool_principal.supervised_agent_types,
    )
    return _respond(data, format, f"agent_performance_{_today_label()}.csv")


# ─── /reports/evaluations ─────────────────────────────────────────────────────

@router.get("/evaluations")
async def get_evaluations_report(
    request:      Request,
    tenant_id:    str            = Query(...),
    from_dt:      Optional[str]  = Query(None),
    to_dt:        Optional[str]  = Query(None),
    campaign_id:  Optional[str]  = Query(None),
    form_id:      Optional[str]  = Query(None),
    evaluator_id: Optional[str]  = Query(None),
    eval_status:  Optional[str]  = Query(None),
    page:         int            = Query(1, ge=1),
    page_size:    int            = Query(100, ge=1),
    format:       str            = Query("json"),
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Individual evaluation results per session.

    Filters: campaign_id, form_id, evaluator_id, eval_status
    eval_status: submitted | approved | rejected | contested | locked
    format: json | csv
    """
    page_size = _clamp_page_size(page_size, format == "csv")
    data = await query_evaluations_report(
        client       = request.app.state.store.new_client(),
        database     = request.app.state.store._database,
        tenant_id    = tenant_id,
        from_dt      = from_dt,
        to_dt        = to_dt,
        campaign_id  = campaign_id,
        form_id      = form_id,
        evaluator_id = evaluator_id,
        eval_status  = eval_status,
        page         = page,
        page_size    = page_size,
        accessible_pools = pool_principal.accessible_pools,
    )
    return _respond(data, format, f"evaluations_{_today_label()}.csv")


@router.get("/evaluations/summary")
async def get_evaluations_summary(
    request:     Request,
    tenant_id:   str            = Query(...),
    from_dt:     Optional[str]  = Query(None),
    to_dt:       Optional[str]  = Query(None),
    campaign_id: Optional[str]  = Query(None),
    form_id:     Optional[str]  = Query(None),
    group_by:    str            = Query("campaign_id"),
    format:      str            = Query("json"),
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Aggregated evaluation summary: avg score, score distribution, status counts.

    group_by: campaign_id | evaluator_id | form_id | date | agent_key | pool_id
    F2 (bancada de agentes): agent_key/pool_id agrupam pelo agente AVALIADO —
    join com segments (último primary não-sintético da sessão); group_key '' =
    avaliação sem segmento atribuível.
    Includes per-group breakdowns: score_excellent (≥0.9), score_good (0.7-0.9),
    score_fair (0.5-0.7), score_poor (<0.5), with_compliance_flags count.
    format: json | csv
    """
    data = await query_evaluations_summary(
        client     = request.app.state.store.new_client(),
        database   = request.app.state.store._database,
        tenant_id  = tenant_id,
        from_dt    = from_dt,
        to_dt      = to_dt,
        campaign_id = campaign_id,
        form_id    = form_id,
        group_by   = group_by,
        accessible_pools = pool_principal.accessible_pools,
    )
    return _respond(data, format, f"evaluations_summary_{_today_label()}.csv")


# ─── GET /reports/evaluations/quality — T11: Oficial × Operacional (§17.3) ────

@router.get("/evaluations/quality")
async def get_evaluations_quality(
    request:         Request,
    tenant_id:       str           = Query(...),
    from_dt:         Optional[str] = Query(None),
    to_dt:           Optional[str] = Query(None),
    mode:            str           = Query("oficial"),   # oficial | operacional
    group_by:        str           = Query("campaign_id"),
    campaign_id:     Optional[str] = Query(None),
    finalize_reason: Optional[str] = Query(None),
    segment_id:      Optional[str] = Query(None),
    form_version:    Optional[int] = Query(None),
    format:          str           = Query("json"),
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """T11 — relatório de qualidade em DOIS modos (nunca blendados):
      - **oficial** (default): só avaliações FINALIZADAS (`evaluation_finalized`) — o invariante.
      - **operacional**: finalized ∪ provisório (em andamento), rotulado por `provisional`.
    group_by: campaign_id | finalize_reason | segment_id | form_version | evaluated_agent_type | date.
    Fatiável por finalize_reason/segment_id/form_version. format: json | csv."""
    data = await query_quality_report(
        client          = request.app.state.store.new_client(),
        database        = request.app.state.store._database,
        tenant_id       = tenant_id,
        from_dt         = from_dt,
        to_dt           = to_dt,
        mode            = mode,
        group_by        = group_by,
        campaign_id     = campaign_id,
        finalize_reason = finalize_reason,
        segment_id      = segment_id,
        form_version    = form_version,
        accessible_pools = pool_principal.accessible_pools,
    )
    return _respond(data, format, f"evaluations_quality_{_today_label()}.csv")


# ─── GET /reports/agents/compare — F3 bancada de agentes ─────────────────────

@router.get("/agents/compare")
async def get_agents_compare(
    request:         Request,
    tenant_id:       str            = Query(...),
    from_dt:         Optional[str]  = Query(None),
    to_dt:           Optional[str]  = Query(None),
    lens:            str            = Query("resolution",
                                            description="resolution | sessions_aht | availability | pause_reason | quality | deploy (ai)"),
    mode:            str            = Query("daily",
                                            description="daily (default) | epoch (só lens=deploy: série por deploy_version, R15a)"),
    pool_id:         Optional[str]  = Query(None),
    entities:        Optional[str]  = Query(None,
                                            description="agent_keys separados por vírgula (vazio = só a média do escopo)"),
    include_average: bool           = Query(True),
    origin:          str            = Query("live", pattern="^(live|import|reeval)$",
                                            description="Substrate origin (ADR): live=produção (default), import, reeval"),
    pool_principal:  PoolPrincipal  = Depends(optional_pool_principal),
) -> Response:
    """
    Bancada de comparação de agentes (analytics-agents-workbench §11).

    Devolve, numa chamada, as séries diárias de todas as entidades pedidas +
    a "média dos agentes" de referência (aritmética por bucket, N visível,
    gap quando o agente não tem dado no dia). agent_key: user_id (humano) /
    flow_id (IA). Lentes pendentes (nps/wrapup → F5; quality_criteria) retornam
    error=lens_not_available.
    """
    entity_list = [e.strip() for e in entities.split(",") if e.strip()] if entities else []
    data = await query_agents_compare(
        client     = request.app.state.store.new_client(),
        database   = request.app.state.store._database,
        tenant_id  = tenant_id,
        from_dt    = from_dt,
        to_dt      = to_dt,
        lens       = lens,
        mode       = mode,
        pool_id    = pool_id,
        entities   = entity_list,
        include_average = include_average,
        origin                 = origin,
        accessible_pools       = pool_principal.accessible_pools,
        supervised_agent_types = pool_principal.supervised_agent_types,
    )
    status = 400 if data.get("error") in ("invalid_lens", "lens_not_available") else 200
    return JSONResponse(content=data, status_code=status)


# ─── Customer Voice (Fatia 1) — lente genérica grain × metric + overlay SLA ──────

@router.get("/customer-voice/instruments")
async def get_customer_voice_instruments(
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """Catálogo de instrumentos: métrica → {source, rollup, grãos suportados, label}.
    A UI usa isto para montar os seletores (grão × instrumento)."""
    return JSONResponse(content={"instruments": CV_INSTRUMENTS}, status_code=200)


@router.get("/customer-voice")
async def get_customer_voice(
    request:        Request,
    tenant_id:      str            = Query(...),
    grain:          str            = Query("journey", description="segment | session | journey"),
    metric:         str            = Query("nps",     description="nps | csat | ces | pmf | fcr | sla"),
    from_dt:        Optional[str]  = Query(None),
    to_dt:          Optional[str]  = Query(None),
    # F4 — repetível (`?pool_id=a&pool_id=b`). Virou lista quando `/analise/surveys` foi
    # absorvido como o nível de RESPOSTAS desta superfície: aquela tela tinha
    # `PoolMultiSelect`, e unificar a barra com um parâmetro escalar teria REDUZIDO uma
    # capacidade que funcionava. O escalar continua válido (link antigo).
    pool_id:        Optional[List[str]] = Query(None),
    pool_principal: PoolPrincipal  = Depends(optional_pool_principal),
) -> Response:
    """Voz do Cliente: série diária do instrumento (roll-up do catálogo) no grão pedido +
    overlay de SLA no mesmo eixo. Fonte: session_signal (survey) + sessions (SLA)."""
    since = from_dt or "2000-01-01"
    until = to_dt or "2100-01-01"
    try:
        data = query_customer_voice(
            client           = request.app.state.store.new_client(),
            db               = request.app.state.store._database,
            tenant_id        = tenant_id,
            grain            = grain,
            metric           = metric,
            since            = since,
            until            = until,
            pool_id          = pool_id,
            accessible_pools = pool_principal.accessible_pools,
        )
    except ValueError as e:
        return JSONResponse(content={"error": str(e)}, status_code=400)
    return JSONResponse(content=data, status_code=200)


# ─── GET /reports/agents/cross — F6 cruzamentos (§8) ─────────────────────────

@router.get("/agents/cross")
async def get_agents_cross(
    request:        Request,
    tenant_id:      str            = Query(...),
    from_dt:        Optional[str]  = Query(None),
    to_dt:          Optional[str]  = Query(None),
    pool_id:        Optional[str]  = Query(None),
    origin:         str            = Query("live", pattern="^(live|import|reeval)$",
                                           description="Substrate origin (ADR): live=produção (default), import, reeval"),
    pool_principal: PoolPrincipal  = Depends(optional_pool_principal),
) -> Response:
    """
    Cruzamento das 3 vantagens por agente (§8): resolução × qualidade × NPS +
    sessões. Uma linha por agent_key. O realce de divergência (perception gap,
    acurácia de disposição, estrela) e o quadrante são da camada de apresentação.
    """
    data = await query_agents_cross(
        client     = request.app.state.store.new_client(),
        database   = request.app.state.store._database,
        tenant_id  = tenant_id,
        from_dt    = from_dt,
        to_dt      = to_dt,
        pool_id    = pool_id,
        origin                 = origin,
        accessible_pools       = pool_principal.accessible_pools,
        supervised_agent_types = pool_principal.supervised_agent_types,
    )
    return JSONResponse(content=data)


# ─── GET /reports/timeseries/volume ──────────────────────────────────────────

@router.get("/timeseries/volume")
async def report_timeseries_volume(
    request:       Request,
    tenant_id:     str           = Query(...,   description="Tenant identifier"),
    from_dt:       Optional[str] = Query(None,  description="ISO8601 start (default: 7d ago)"),
    to_dt:         Optional[str] = Query(None,  description="ISO8601 end (default: now)"),
    interval:      int           = Query(60,    ge=1, le=1440, description="Bucket size in minutes"),
    breakdown_by:  Optional[str] = Query(None,  description="pool_id | channel"),
    pool_id:       Optional[str] = Query(None,  description="Filter by pool_id"),
    format:        str           = Query("json", pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Session volume (count) bucketed by time interval.

    buckets[].value = number of sessions opened in the bucket window.
    breakdown_by=pool_id|channel splits each bucket by that dimension.
    meta.total = total sessions across all buckets.
    """
    data = await query_volume_timeseries(
        client     = request.app.state.store.new_client(),
        database   = request.app.state.store._database,
        tenant_id  = tenant_id,
        from_dt    = from_dt,
        to_dt      = to_dt,
        interval   = interval,
        breakdown_by = breakdown_by,
        pool_id    = pool_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    if format == "csv":
        return Response(
            content=timeseries_to_csv(data.get("buckets", [])),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="volume_timeseries_{_today_label()}.csv"'},
        )
    return JSONResponse(content=data, status_code=503 if data.get("error") else 200)


# ─── GET /reports/timeseries/handle_time ─────────────────────────────────────

@router.get("/timeseries/handle_time")
async def report_timeseries_handle_time(
    request:       Request,
    tenant_id:     str           = Query(...,   description="Tenant identifier"),
    from_dt:       Optional[str] = Query(None,  description="ISO8601 start (default: 7d ago)"),
    to_dt:         Optional[str] = Query(None,  description="ISO8601 end (default: now)"),
    interval:      int           = Query(60,    ge=1, le=1440, description="Bucket size in minutes"),
    breakdown_by:  Optional[str] = Query(None,  description="pool_id | channel"),
    pool_id:       Optional[str] = Query(None,  description="Filter by pool_id"),
    format:        str           = Query("json", pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Average handle time (ms) bucketed by time interval.

    buckets[].value = avg handle_time_ms of sessions opened in the bucket window.
    (Era `duration_ms` até 2026-07-29 — coluna de `segments`, inexistente em
    `sessions`; a query falhava mudo e o gráfico ficava vazio. Ver CHANGELOG.)
    meta.total = overall avg across all buckets.
    Tip: divide by 60000 in the UI to display minutes.
    """
    data = await query_handle_time_timeseries(
        client     = request.app.state.store.new_client(),
        database   = request.app.state.store._database,
        tenant_id  = tenant_id,
        from_dt    = from_dt,
        to_dt      = to_dt,
        interval   = interval,
        breakdown_by = breakdown_by,
        pool_id    = pool_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    if format == "csv":
        return Response(
            content=timeseries_to_csv(data.get("buckets", [])),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="handle_time_timeseries_{_today_label()}.csv"'},
        )
    return JSONResponse(content=data, status_code=503 if data.get("error") else 200)


# ─── GET /reports/timeseries/score ───────────────────────────────────────────

@router.get("/timeseries/score")
async def report_timeseries_score(
    request:      Request,
    tenant_id:    str           = Query(...,   description="Tenant identifier"),
    from_dt:      Optional[str] = Query(None,  description="ISO8601 start (default: 7d ago)"),
    to_dt:        Optional[str] = Query(None,  description="ISO8601 end (default: now)"),
    interval:     int           = Query(60,    ge=1, le=1440, description="Bucket size in minutes"),
    breakdown_by: Optional[str] = Query(None,  description="campaign_id | form_id"),
    campaign_id:  Optional[str] = Query(None,  description="Filter by campaign_id"),
    format:       str           = Query("json", pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Average evaluation score (0–1) bucketed by time interval.

    buckets[].value = avg overall_score of evaluations submitted in the bucket.
    breakdown_by=campaign_id|form_id splits each bucket by that dimension.
    meta.total = overall avg across all buckets.
    """
    data = await query_score_timeseries(
        client     = request.app.state.store.new_client(),
        database   = request.app.state.store._database,
        tenant_id  = tenant_id,
        from_dt    = from_dt,
        to_dt      = to_dt,
        interval   = interval,
        breakdown_by = breakdown_by,
        campaign_id  = campaign_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    if format == "csv":
        return Response(
            content=timeseries_to_csv(data.get("buckets", [])),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="score_timeseries_{_today_label()}.csv"'},
        )
    return JSONResponse(content=data, status_code=503 if data.get("error") else 200)


# ─── /reports/agent-performance/daily (Arc 5 MV — v_agent_performance) ──────

@router.get("/agent-performance/daily")
async def get_agent_performance_daily(
    request:        Request,
    tenant_id:      str           = Query(...,    description="Tenant identifier"),
    from_dt:        Optional[str] = Query(None,   description="Start date (YYYY-MM-DD or ISO8601); default: 7d ago"),
    to_dt:          Optional[str] = Query(None,   description="End date (YYYY-MM-DD or ISO8601); default: today"),
    pool_id:        Optional[str] = Query(None,   description="Filter by pool_id"),
    agent_type_id:  Optional[str] = Query(None,   description="Filter by agent_type_id"),
    origin:         str           = Query("live",  pattern="^(live|import|reeval)$", description="Substrate origin (ADR): live=produção (default), import, reeval"),
    format:         str           = Query("json",  pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Daily pre-aggregated performance metrics from the mv_agent_performance_daily
    materialized view (AggregatingMergeTree), read via the v_agent_performance
    readable SQL view.

    One row per (agent_type_id, pool_id, period_date). Suitable for trend charts
    and time-series dashboards — much faster than querying segments FINAL.

    Columns:
      agent_type_id, pool_id, period_date,
      total_sessions, avg_duration_ms,
      resolution_rate, escalation_rate, transfer_rate, human_rate

    Rates are in [0.0, 1.0]. total_sessions reflects sessions handled in that day
    for the given agent × pool combination (MIN_SESSIONS threshold NOT applied here —
    use the routing-engine's performance_job for throttle-safe scores).
    """
    data = await query_agent_performance_daily(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        pool_id          = pool_id,
        agent_type_id          = agent_type_id,
        origin                 = origin,
        accessible_pools       = pool_principal.accessible_pools,
        supervised_agent_types = pool_principal.supervised_agent_types,
    )
    return _respond(data, format, f"agent_performance_daily_{_today_label()}.csv")


# ─── /reports/sessions/complexity (Arc 5 MV — v_segment_summary) ─────────────

@router.get("/sessions/complexity")
async def get_session_complexity(
    request:        Request,
    tenant_id:      str           = Query(...,    description="Tenant identifier"),
    from_dt:        Optional[str] = Query(None,   description="ISO8601 start (default: 7d ago)"),
    to_dt:          Optional[str] = Query(None,   description="ISO8601 end (default: now)"),
    pool_id:        Optional[str] = Query(None,   description="Filter sessions by originating pool_id"),
    min_handoffs:   int           = Query(0,       ge=0, description="Minimum handoff_count to include"),
    origin:         str           = Query("live",  pattern="^(live|import|reeval)$", description="Substrate origin (ADR): live=produção (default), import, reeval"),
    page:           int           = Query(1,       ge=1),
    page_size:      int           = Query(100,     ge=1),
    format:         str           = Query("json",  pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Session complexity metrics from the mv_segment_summary materialized view
    (AggregatingMergeTree), read via the v_segment_summary readable SQL view.
    Joined with the sessions table for date-range and pool_id filtering.

    One row per session. Suitable for identifying complex interactions (high
    handoffs, multi-agent conferences) and escalation pattern analysis.

    Columns:
      session_id, pool_id,
      segment_count, primary_segments, specialist_segments, human_segments,
      total_duration_ms,
      handoff_count, escalation_count, resolved_count

    Use min_handoffs=1 to find sessions that were transferred at least once.
    Use min_handoffs=2 to find sessions with multiple escalation steps.
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_session_complexity(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        pool_id          = pool_id,
        min_handoffs     = min_handoffs,
        origin           = origin,
        accessible_pools = pool_principal.accessible_pools,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"session_complexity_{_today_label()}.csv")


# ─── /reports/agent-availability (Arc 8 — pause intervals) ───────────────────

@router.get("/agent-availability")
async def get_agent_availability(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_dt:        Optional[str] = Query(None,  description="ISO8601 start (default: 7d ago)"),
    to_dt:          Optional[str] = Query(None,  description="ISO8601 end (default: now)"),
    pool_id:        Optional[str] = Query(None,  description="Filter by pool_id"),
    agent_type_id:  Optional[str] = Query(None,  description="Filter by agent_type_id"),
    page:           int           = Query(1,      ge=1),
    page_size:      int           = Query(100,    ge=1),
    format:         str           = Query("json", pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Agent availability report (Arc 8 pauses + Fase 1b logged time).

    Merges completed login intervals (agent_login_intervals) with completed
    pause intervals (agent_pause_intervals) per (instance_id, pool_id,
    period_date) — i.e. per identity, so humans are no longer collapsed into
    human_agent_{pool}.

    Each row includes:
      instance_id, user_login, user_id, agent_type_id, pool_id, period_date,
      logged_ms         — total logged-in time (ms),
      total_logins      — number of completed login intervals,
      total_pauses      — number of completed pause intervals,
      total_pause_ms    — sum of all pause durations (ms),
      available_ms      — logged_ms − total_pause_ms (clamped at 0),
      reason_breakdown  — list of {reason_id, reason_label, count, total_ms}

    Pool scoping (Arc 7c): if the caller JWT carries accessible_pools the
    result is restricted to those pool_ids automatically.

    Use format=csv for bulk exports (flattens reason_breakdown as JSON string).
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_agent_availability(
        client            = request.app.state.store.new_client(),
        database          = request.app.state.store._database,
        tenant_id         = tenant_id,
        from_dt           = from_dt,
        to_dt             = to_dt,
        pool_id           = pool_id,
        agent_type_id          = agent_type_id,
        accessible_pools       = pool_principal.accessible_pools,
        supervised_agent_types = pool_principal.supervised_agent_types,
        page                   = page,
        page_size              = ps,
    )
    return _respond(data, format, f"agent_availability_{_today_label()}.csv")


@router.get("/agent-timeline")
async def get_agent_timeline(
    request:        Request,
    tenant_id:      str           = Query(...,  description="Tenant identifier"),
    instance_id:    str           = Query(...,  description="Agent instance_id (e.g. human-{userId})"),
    from_dt:        Optional[str] = Query(None, description="ISO8601 start (default: 7d ago)"),
    to_dt:          Optional[str] = Query(None, description="ISO8601 end (default: now)"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Timeline swimlanes for a single agent (Fase 1b / timeline).

    Returns, for one instance_id over [from_dt, to_dt]:
      login_intervals — total logged-in bars,
      pause_intervals — pause blocks (agent-level, overlaid on every lane),
      pool_intervals  — per-pool presence bars (one lane per pool).

    Pool scoping (Arc 7c) restricts pool_intervals to the caller's accessible_pools.
    """
    data = await query_agent_timeline(
        client           = request.app.state.store.new_client(),
        database         = request.app.state.store._database,
        tenant_id        = tenant_id,
        instance_id      = instance_id,
        from_dt          = from_dt,
        to_dt            = to_dt,
        accessible_pools = pool_principal.accessible_pools,
    )
    return _respond(data, "json", "agent_timeline.csv")


@router.get("/pools/volume")
async def get_pools_volume(
    request:        Request,
    tenant_id:      str           = Query(...,  description="Tenant identifier"),
    from_dt:        Optional[str] = Query(None, description="ISO8601 start (default: 7d ago)"),
    to_dt:          Optional[str] = Query(None, description="ISO8601 end (default: now)"),
    pool_id:        Optional[str] = Query(None, description="Filter by pool_id"),
    channel:        Optional[str] = Query(None, description="Filter by channel"),
    bucket:         Optional[str] = Query(None, pattern="^(hour|day)$", description="Time bucket"),
    origin:         str           = Query("live", pattern="^(live|import|reeval)$", description="Substrate origin (ADR): live=produção (default), import, reeval"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Fase 2 — volumetria de contatos por (bucket, pool, canal, endpoint=DNIS).
    series (no tempo) + by_channel + by_endpoint + totals + rejected (demanda
    reprimida, Fase D queue-attended-model). Escopo por accessible_pools.
    """
    data = await query_pools_volume(
        client           = request.app.state.store.new_client(),
        database         = request.app.state.store._database,
        tenant_id        = tenant_id,
        from_dt          = from_dt,
        to_dt            = to_dt,
        pool_id          = pool_id,
        channel          = channel,
        bucket           = bucket,
        origin           = origin,
        accessible_pools = pool_principal.accessible_pools,
    )
    return _respond(data, "json", "pools_volume.csv")


@router.get("/pools/queue")
async def get_pools_queue(
    request:        Request,
    tenant_id:      str           = Query(...,  description="Tenant identifier"),
    from_dt:        Optional[str] = Query(None, description="ISO8601 start (default: 7d ago)"),
    to_dt:          Optional[str] = Query(None, description="ISO8601 end (default: now)"),
    pool_id:        Optional[str] = Query(None, description="Filter by pool_id"),
    bucket:         Optional[str] = Query(None, pattern="^(hour|day)$", description="Time bucket"),
    origin:         str           = Query("live", pattern="^(live|import|reeval)$", description="Substrate origin (ADR): live=produção (default), import, reeval"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Fase D (queue-attended-model) — fila + SLA por pool derivados dos segments
    role='queue': espera = duration_ms do segmento de fila; abandono =
    outcome='abandoned'; handoff = fila→primary. series + by_pool.
    Escopo por accessible_pools.
    """
    data = await query_pools_queue(
        client           = request.app.state.store.new_client(),
        database         = request.app.state.store._database,
        tenant_id        = tenant_id,
        from_dt          = from_dt,
        to_dt            = to_dt,
        pool_id          = pool_id,
        bucket           = bucket,
        origin           = origin,
        accessible_pools = pool_principal.accessible_pools,
    )
    return _respond(data, "json", "pools_queue.csv")


@router.get("/pools/occupancy")
async def get_pools_occupancy(
    request:        Request,
    tenant_id:      str           = Query(...,  description="Tenant identifier"),
    from_dt:        Optional[str] = Query(None, description="ISO8601 start (default: 7d ago)"),
    to_dt:          Optional[str] = Query(None, description="ISO8601 end (default: now)"),
    pool_id:        Optional[str] = Query(None, description="Filter by pool_id"),
    # P3 — `15min` é leitura pura sobre o grão de 1 minuto que já é gravado: retroativo,
    # sem escritor novo. Só neste endpoint: os demais agregam grandezas somáveis, onde
    # um bucket mais fino não responde a mesma pergunta de dimensionamento.
    bucket:         Optional[str] = Query(None, pattern="^(15min|hour|day)$", description="Time bucket"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Fase 2 — pico de concorrência vs capacidade por pool (+ total do tenant).
    series/total_series (no tempo) + by_pool (headroom/utilization) + total.
    Per-pool: capacidade provisionada (flashada pelo occupancy sampler).
    Total: teto = capacidade configurada no pricing quando disponível
    (decisão 2026-06-04); fallback gracioso para a provisionada.
    """
    data = await query_pools_occupancy(
        client           = request.app.state.store.new_client(),
        database         = request.app.state.store._database,
        tenant_id        = tenant_id,
        from_dt          = from_dt,
        to_dt            = to_dt,
        pool_id          = pool_id,
        bucket           = bucket,
        accessible_pools = pool_principal.accessible_pools,
    )

    total = (data.get("data") or {}).get("total")
    if total:
        total["provisioned_capacity"] = total.get("capacity", 0)
        total["capacity_source"]      = "provisioned"
        configured = await get_configured_agent_capacity(get_settings().pricing_api_url, tenant_id)
        if configured is not None:
            peak = int(total.get("peak_concurrency") or 0)
            total["capacity"]        = configured
            total["headroom"]        = max(configured - peak, 0)
            total["utilization"]     = round(peak / configured, 4) if configured else None
            total["capacity_source"] = "pricing"

    return _respond(data, "json", "pools_occupancy.csv")


# ─── /reports/events — unified event stream ───────────────────────────────────

@router.get("/events")
async def get_events(
    request:        Request,
    tenant_id:      str           = Query(...,    description="Tenant identifier"),
    from_dt:        Optional[str] = Query(None,   description="Start datetime (YYYY-MM-DD or ISO8601); default: 7d ago"),
    to_dt:          Optional[str] = Query(None,   description="End datetime (YYYY-MM-DD or ISO8601); default: now"),
    session_id:     Optional[str] = Query(None,   description="Exact session_id filter"),
    pool_id:        Optional[str] = Query(None,   description="Pool filter"),
    channel:        Optional[str] = Query(None,   description="Channel filter (webchat, whatsapp, voice, …)"),
    event_type:     Optional[str] = Query(None,   description="Event type filter (session_opened, message_sent, agent_done, …)"),
    page:           int           = Query(1,      ge=1),
    page_size:      int           = Query(100,    ge=1, le=1000),
    format:         str           = Query("json", pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Unified event stream for debugging and audit.

    Sources (UNION ALL):
      sessions            → session_opened, session_closed
      messages            → message_sent (visibility=all only)
      segments            → routed (started_at), agent_done (ended_at)
      agent_pause_intervals → agent_pause, agent_ready
      workflow_events     → workflow_{event_type}

    All events share the common shape:
      event_id, session_id, tenant_id, type, timestamp,
      channel, pool_id, author_id, author_role, content

    Escopo de substrato: só produção (`origin = 'live'`). Sessões importadas ou
    reavaliadas (quality-ingest / quality-export) não aparecem aqui.
    """
    ps   = _clamp_page_size(page_size, is_csv=(format == "csv"))
    data = await query_events(
        client           = request.app.state.store.new_client(),
        database         = request.app.state.store._database,
        tenant_id        = tenant_id,
        from_dt          = from_dt,
        to_dt            = to_dt,
        session_id       = session_id,
        pool_id          = pool_id,
        channel          = channel,
        event_type       = event_type,
        accessible_pools = pool_principal.accessible_pools,
        page             = page,
        page_size        = ps,
    )
    return _respond(data, format, f"events_{_today_label()}.csv")


# ─── /reports/workflow-summary — aggregated workflow analytics ────────────────

@router.get("/workflow-summary")
async def get_workflow_summary(
    request:        Request,
    tenant_id:      str           = Query(...,          description="Tenant identifier"),
    from_dt:        Optional[str] = Query(None,         description="Start date (YYYY-MM-DD or ISO8601); default: 7d ago"),
    to_dt:          Optional[str] = Query(None,         description="End date; default: now"),
    group_by:       str           = Query("pool_id",    pattern="^(pool_id|flow_id|campaign_id)$"),
    pool_id:        Optional[str] = Query(None,         description="Filter by specific pool_id"),
    flow_id:        Optional[str] = Query(None,         description="Filter by specific flow_id"),
    campaign_id:    Optional[str] = Query(None,         description="Filter by specific campaign_id"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Aggregated workflow metrics grouped by pool_id, flow_id, or campaign_id.

    One row per group with: total_triggered, total_completed, total_failed,
    total_timeout, total_cancelled, total_suspended,
    completion_rate, failure_rate, avg_duration_ms.
    """
    data = await query_workflow_summary(
        client           = request.app.state.store.new_client(),
        database         = request.app.state.store._database,
        tenant_id        = tenant_id,
        from_dt          = from_dt,
        to_dt            = to_dt,
        group_by         = group_by,
        pool_id          = pool_id,
        flow_id          = flow_id,
        campaign_id      = campaign_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    return JSONResponse(content=data)


# ─── GET /reports/journeys (Journey J2 — proveniência-only) ──────────────────
# Reintroduz o endpoint removido no Arc 19 Fase F — agora como LENTE por
# proveniência (agrupa analytics.sessions por root_session_id), NÃO como a entidade
# Journey do Arc 10. Sem alias/merge (isso é J3). Drill:
#   /reports/sessions?root_session_id=<journey_id>  → sessões-membro
#   /reports/segments?session_id=<id>               → segmentos

@router.get("/journeys")
async def report_journeys(
    request:          Request,
    tenant_id:        str           = Query(...,   description="Tenant identifier"),
    from_dt:          Optional[str] = Query(None,  description="ISO8601 start (default: 7d ago)"),
    to_dt:            Optional[str] = Query(None,  description="ISO8601 end (default: now)"),
    channel:          Optional[str] = Query(None,  description="Filter: journeys com sessão-membro neste canal"),
    pool_id:          Optional[str] = Query(None,  description="Filter: journeys com um segmento neste pool"),
    customer_id:      Optional[str] = Query(None,  description="Cliente 360/HJ: journeys com ≥1 sessão-membro deste cliente"),
    root_session_id:  Optional[str] = Query(None,  description="Fetch direcionado de UMA journey (deep-link ao drill): resolve ao canônico, ignora janela+significant"),
    significant_only: bool          = Query(True,  description="Esconde journeys de 1 sessão sem workflow (default de UX)"),
    origin:           str           = Query("live", pattern="^(live|import|reeval)$"),
    page:             int           = Query(1,     ge=1),
    page_size:        int           = Query(100,   ge=1),
    format:           str           = Query("json", pattern="^(json|csv)$"),
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Lista de journeys (J2, proveniência-only): agrupa `sessions` por `root_session_id`.
    Colunas: journey_id, session_count, started_at, last_activity_at, channels[],
             pool_ids[], open_count, significant. ABAC via accessible_pools
             (journey aparece se ≥1 sessão-membro é visível).
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_journeys_report(
        client           = request.app.state.store.new_client(),
        database         = request.app.state.store._database,
        tenant_id        = tenant_id,
        from_dt          = from_dt,
        to_dt            = to_dt,
        channel          = channel,
        pool_id          = pool_id,
        customer_id      = customer_id,
        root_session_id  = root_session_id,
        significant_only = significant_only,
        accessible_pools = pool_principal.accessible_pools,
        origin           = origin,
        page             = page,
        page_size        = ps,
    )
    return _respond(data, format, f"journeys_{_today_label()}.csv")


# ─── GET /reports/customers/{customer_id}/360 (Cliente 360 — C1b, ADR §D4) ────
# Agregado por cliente: contatos + quality (evaluation_finalized, Oficial) + surveys
# (session_signal). Superfície do Console (aba Cliente); origin=live por default.

@router.get("/customers/{customer_id}/360")
async def report_customer_360(
    request:     Request,
    customer_id: str,
    tenant_id:   str = Query(..., description="Tenant identifier"),
    origin:      str = Query("live", pattern="^(live|import|reeval)$"),
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """360 agregado do cliente: resumo de contatos, qualidade (modo Oficial —
    `evaluation_finalized`) e voz do cliente (`session_signal`), tudo vinculado por
    `session_id` ao conjunto de sessões do `customer_id`. Fail-soft por bloco."""
    data = await query_customer_360(
        client      = request.app.state.store.new_client(),
        database    = request.app.state.store._database,
        tenant_id   = tenant_id,
        customer_id = customer_id,
        origin      = origin,
        accessible_pools = pool_principal.accessible_pools,
    )
    return _respond(data, "json", "")


# ─── GET /reports/sessions/{session_id}/trace (Journey T6 — rastro forense) ───
# Rastro de proveniência BIDIRECIONAL a partir de uma sessão: ancestrais (sobe por
# origin_session_id) + descendentes (BFS), ATRAVESSANDO fronteiras de journey. É a
# superfície de RASTREIO (§6 da spec) — distinta da Vista Processos, que MEDE uma
# journey. Cada nó cuja journey canônica difere da do foco vem com `journey_boundary`
# (a fronteira que o `journey: new` cria); a UI a exibe como link, não expansão.

@router.get("/sessions/{session_id}/trace")
async def report_session_trace(
    request:        Request,
    session_id:     str,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    origin:         str           = Query("live", pattern="^(live|import|reeval)$"),
    max_depth:      int           = Query(25,    ge=1, le=100),
    max_nodes:      int           = Query(200,   ge=1, le=1000),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Árvore de proveniência em volta de `session_id`. Retorna `focus`, `nodes[]`
    (com `depth` relativo ao foco: <0 ancestral, 0 foco, >0 descendente),
    `journey_id` canônico + `journey_boundary` por nó, e `focus_journey_id`.
    ABAC via accessible_pools (nós fora do escopo são omitidos).
    """
    data = await query_session_trace(
        client           = request.app.state.store.new_client(),
        database         = request.app.state.store._database,
        tenant_id        = tenant_id,
        focus_session_id = session_id,
        accessible_pools = pool_principal.accessible_pools,
        origin           = origin,
        max_depth        = max_depth,
        max_nodes        = max_nodes,
    )
    return _respond(data, "json", "")


# ─── GET /reports/agent-events/series ────────────────────────────────────────

@router.get("/agent-events/series")
async def get_agent_events_series(
    request:     Request,
    tenant_id:   str           = Query(...,    description="Tenant identifier"),
    from_dt:     Optional[str] = Query(None,   description="ISO8601 start (default: 7d ago)"),
    to_dt:       Optional[str] = Query(None,   description="ISO8601 end (default: now)"),
    category:    Optional[str] = Query(None,   description="Category prefix filter (e.g. 'pool.skill')"),
    pool_id:     Optional[str] = Query(None,   description="Filter by pool_id"),
    skill_id:    Optional[str] = Query(None,   description="Filter by skill_id"),
    granularity: str           = Query("day",  pattern="^(hour|day|week)$"),
    format:      str           = Query("json", pattern="^(json|csv)$"),
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Time-series of agent business events (Arc 12).

    Returns one row per (period × category) with count, total_value, avg_value,
    min_value, max_value.  Period resolution is controlled by granularity:
      hour — toStartOfHour(emitted_at)
      day  — toDate(emitted_at)  [default]
      week — toMonday(emitted_at)

    category filter supports prefix matching: ?category=retencao_humano.skill_retencao_v2
    matches all metric_key values under that skill.
    """
    data = await query_agent_events_series(
        client      = request.app.state.store.new_client(),
        database    = request.app.state.store._database,
        tenant_id   = tenant_id,
        from_dt     = from_dt,
        to_dt       = to_dt,
        category    = category,
        pool_id     = pool_id,
        skill_id    = skill_id,
        granularity = granularity,
        accessible_pools = pool_principal.accessible_pools,
    )
    return _respond(data, format, f"agent_events_series_{_today_label()}.csv")


# ─── GET /reports/agent-events/summary ───────────────────────────────────────

@router.get("/agent-events/summary")
async def get_agent_events_summary(
    request:   Request,
    tenant_id: str           = Query(...,         description="Tenant identifier"),
    from_dt:   Optional[str] = Query(None,         description="ISO8601 start (default: 7d ago)"),
    to_dt:     Optional[str] = Query(None,         description="ISO8601 end (default: now)"),
    category:  Optional[str] = Query(None,         description="Category prefix filter"),
    pool_id:   Optional[str] = Query(None,         description="Filter by pool_id"),
    group_by:  str           = Query("category",   pattern="^(category|skill_id|pool_id|agent_type_id)$"),
    page:      int           = Query(1,            ge=1),
    page_size: int           = Query(100,          ge=1),
    format:    str           = Query("json",       pattern="^(json|csv)$"),
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Aggregated summary of agent business events (Arc 12).

    One row per distinct value of group_by dimension with count, total/avg/min/max value,
    first_seen, last_seen.  Sorted by count DESC.

    group_by: category (default) | skill_id | pool_id | agent_type_id
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_agent_events_summary(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        category  = category,
        pool_id   = pool_id,
        group_by  = group_by,
        page      = page,
        page_size = ps,
        accessible_pools = pool_principal.accessible_pools,
    )
    return _respond(data, format, f"agent_events_summary_{_today_label()}.csv")


# ─── GET /reports/agent-events/epochs ────────────────────────────────────────

@router.get("/agent-events/epochs")
async def get_agent_events_epochs(
    request:   Request,
    tenant_id: str           = Query(...,  description="Tenant identifier"),
    root:      str           = Query(...,  description="Raiz da árvore (prefixo de categoria, sem ponto final)"),
    from_dt:   Optional[str] = Query(None, description="ISO8601 start (default: 7d ago)"),
    to_dt:     Optional[str] = Query(None, description="ISO8601 end (default: now)"),
    pool_id:   Optional[str] = Query(None, description="Filter by pool_id"),
    format:    str           = Query("json", pattern="^(json|csv)$"),
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    ÉPOCAS de vocabulário sob `root` — um bloco por **run contíguo** de formulário.

    Existe porque repontar o hook de um pool troca o vocabulário **sob a mesma série**:
    o mesmo item do mundo real passa a ser contado em dois caminhos, e nenhuma regra de
    exibição conserta isso (medido em 2026-09-05: `servico.segunda_via` da forma plana
    convivendo com `servico.cadastro.segunda_via` da forma em árvore). Recortar o
    período por época faz o conflito **deixar de existir** em vez de ser tratado.

    **Run, não forma:** rollback do hook faz a mesma forma valer em dois períodos
    separados; agrupar por forma funde os dois e apaga a fase do meio.

    **Dos eventos, não da configuração** — e não por conveniência: o registry tem
    `skill_deployments` para promotes e **nada equivalente para hooks**, então o
    `dialog_form_id` anterior deixa de existir no instante do `PUT`. Derivar do evento
    também é melhor: troca sem tráfego não produz época, e não deve.

    A run **sem carimbo** (`form_id: ""`) é uma run legítima — dado anterior ao carimbo
    de 2026-09-05. Não é descartada nem atribuída a forma alguma.
    """
    data = await query_agent_events_epochs(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        root      = root,
        from_dt   = from_dt,
        to_dt     = to_dt,
        pool_id   = pool_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    return _respond(data, format, f"agent_events_epochs_{_today_label()}.csv")


# ─── GET /reports/agent-events/tree ──────────────────────────────────────────

@router.get("/agent-events/tree")
async def get_agent_events_tree(
    request:   Request,
    tenant_id: str           = Query(...,  description="Tenant identifier"),
    root:      str           = Query(...,  description="Raiz da árvore (prefixo de categoria, sem ponto final) — ex.: retencao_humano.wrapup.motivo"),
    from_dt:   Optional[str] = Query(None, description="ISO8601 start (default: 7d ago)"),
    to_dt:     Optional[str] = Query(None, description="ISO8601 end (default: now)"),
    pool_id:   Optional[str] = Query(None, description="Filter by pool_id"),
    form_id:   Optional[str] = Query(None, description="Recorte por ÉPOCA: id do DialogForm. String VAZIA = a época anterior ao carimbo. Ausente = sem filtro"),
    format:    str           = Query("json", pattern="^(json|csv)$"),
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Árvore de taxonomia sob `root`, com a contagem subindo da folha para as pastas.

    **`root` é obrigatório e não tem default.** Uma árvore precisa de raiz: sem ela a
    resposta seria o conjunto de TODAS as taxonomias do tenant enraizadas em nada, o
    que não é uma árvore — é uma floresta somada. Ausente ⇒ 422 do FastAPI, que é
    melhor que um default que ninguém pediu.

    Cada nó traz TRÊS contagens, e são três porque só duas seriam mentira:

      * `own`             — eventos gravados exatamente neste nó. Em PASTA isto é
                            sintoma (pasta não é selecionável), e fica em coluna
                            própria para não sumir dentro do total do ramo;
      * `branch_marks`    — marcações no ramo. **Soma.**
      * `branch_contacts` — atendimentos distintos no ramo. **Não soma** — medido em
                            2026-09-05: 2 marcações para 1 atendimento em
                            `servico.cadastro`. Não é derivável no cliente
                            (`uniqExact` não soma), e é por isso que esta rota existe.

    `meta.vocabularies` diz quantas FORMAS caíram na janela. Com mais de uma, a árvore
    mistura vocabulários e quem desenha tem de recusar os totais em vez de somar em
    silêncio (`comparability: 'same_form'`). `meta.unstamped_events` conta o que é
    anterior ao carimbo, sem atribuí-lo a forma nenhuma.
    """
    data = await query_agent_events_tree(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        root      = root,
        from_dt   = from_dt,
        to_dt     = to_dt,
        pool_id   = pool_id,
        form_id   = form_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    return _respond(data, format, f"agent_events_tree_{_today_label()}.csv")


# ─── GET /reports/agent-events/categories ────────────────────────────────────

@router.get("/agent-events/categories")
async def get_agent_events_categories(
    request:   Request,
    tenant_id: str           = Query(...,   description="Tenant identifier"),
    from_dt:   Optional[str] = Query(None,  description="ISO8601 start (default: 7d ago)"),
    to_dt:     Optional[str] = Query(None,  description="ISO8601 end (default: now)"),
    pool_id:   Optional[str] = Query(None,  description="Filter by pool_id"),
    skill_id:  Optional[str] = Query(None,  description="Filter by skill_id"),
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Catalogue of distinct category values active in the time window (Arc 12).

    Used by the Dashboard AddCardModal to populate the dynamic category selector.
    Returns up to 500 entries sorted by category ASC.

    Response shape:
      data: list of {category, category_l1..l4, event_count, last_seen}
      meta: {from_dt, to_dt, pool_id, skill_id}
    """
    data = await query_agent_events_categories(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        pool_id   = pool_id,
        skill_id  = skill_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    return _respond(data, "json", "")


# ─── GET /reports/evaluator-calibration (Arc 13) ──────────────────────────────

@router.get("/evaluator-calibration")
async def get_evaluator_calibration(
    request:       Request,
    tenant_id:     str           = Query(...,   description="Tenant identifier"),
    from_dt:       Optional[str] = Query(None,  description="ISO8601 start (default: 7d ago)"),
    to_dt:         Optional[str] = Query(None,  description="ISO8601 end (default: now)"),
    campaign_id:   Optional[str] = Query(None,  description="Filter by campaign_id"),
    evaluator_id:  Optional[str] = Query(None,  description="Filter by evaluator agent_type_id"),
    skill_version: Optional[str] = Query(None,  description="Filter by skill version string"),
    granularity:   str           = Query("day", description="Time bucket: day | week"),
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Calibration Score time-series for the AI evaluator (Arc 13).

    calibration_score = (approved / total_reviewed) × 100, per time bucket + skill version.

    Response shape:
      data:    list of {period, skill_version, evaluator_id, total, approved,
                        recalibrated, bias_flagged, calibration_score}
      summary: {total, approved, recalibrated, bias_flagged, calibration_score}
      meta:    {from_dt, to_dt, campaign_id, evaluator_id, skill_version, granularity}
    """
    # R8b — limiar de divergência + N mínimo do namespace `evaluation` (config-api,
    # override tenant → global → default 0.25 / 30). Degrada para os defaults se o
    # config-api estiver fora.
    from .config import get_settings
    from .config_client import get_config_value
    _cfg_url = get_settings().config_api_url
    threshold = await get_config_value(
        _cfg_url, tenant_id, "evaluation", "calibration_divergence_threshold", 0.25)
    min_n = await get_config_value(
        _cfg_url, tenant_id, "evaluation", "calibration_min_sample_n", 30)

    data = await query_evaluator_calibration(
        client        = request.app.state.store.new_client(),
        database      = request.app.state.store._database,
        tenant_id     = tenant_id,
        from_dt       = from_dt,
        to_dt         = to_dt,
        campaign_id   = campaign_id,
        evaluator_id  = evaluator_id,
        skill_version = skill_version,
        granularity   = granularity,
        divergence_threshold = float(threshold),
        min_sample_n         = int(min_n),
    )
    return _respond(data, "json", "")

