"""
data_subject.py — dossiê de ACESSO do titular (AUD-03, LGPD art. 18, II).

Monta, para UMA pessoa, o que a plataforma guarda dela, loja por loja. A rota mora em
`audit.py` (mesmo portão e mesma trilha das outras `/v1/audit/*`); aqui fica o
percurso, separado para ser testável sem HTTP.

O percurso, e por que nesta ordem:
  1. **Quem é a pessoa.** Telefone, e-mail ou CPF viram `customer_id` no resolvedor de
     identidade do channel-gateway, SEM provisionar (consultar não pode criar cliente).
  2. **O registro de identidade**, que traz os `customer_id` fundidos no canônico —
     sem eles, as sessões anteriores à fusão ficariam de fora.
  3. **As sessões** (ClickHouse `sessions`, por `customer_id`). É o pivô: quase toda
     loja da plataforma é chaveada por sessão, não por pessoa.
  4. **O que cada loja guarda**, pelas sessões ou pelos ids: mensagens (mascaradas) e
     insights do ClickHouse, anexos e gravações (metadado), outbound e pesquisas.

Cada seção diz o seu `status` — `ok`, `not_found`, `unavailable: <motivo>`. Um dossiê
que omite uma loja EM SILÊNCIO afirma ao titular que ela não guarda nada dele, e esse
é o erro que esta rota não pode cometer (§ Postura: degradação nunca é silenciosa).
Pelo mesmo motivo, `not_covered` lista as lojas que este dossiê AINDA não percorre.

⚠️ `sessions.ani` nunca é preenchido (medido em 2026-09-29: 0 de 2 945). Na voz SIP e
no webhook o `customer_id` da sessão é o próprio número de quem ligou, então o telefone
informado é procurado ali direto, além do `customer_id` resolvido. E `sessions.customer_id`
cai no `contact_id` quando não há cliente resolvido — essas sessões não voltam à pessoa.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger("plughub.analytics.data_subject")

MAX_SESSIONS = 500

# Lojas com dado do titular que este dossiê AINDA não percorre, e por quê. Faz parte da
# resposta: o DPO precisa saber o que NÃO foi olhado.
NOT_COVERED = [
    {"store": "postgres.session_stream_events",
     "why": "stream durável com original_content desmascarado; sem API de leitura por "
            "sessão fora do session-replayer (AUD-01 trata o desmascarado)"},
    {"store": "postgres.session_pipeline_state / session_context_snapshot",
     "why": "estado de execução do fluxo; sem API de leitura"},
    {"store": "postgres.evaluation instances/results",
     "why": "avaliação do ATENDIMENTO, não do titular; chaveada por sessão"},
    {"store": "redis (contexto de sessão 4 h, journey 30 d, cliente 90 d)",
     "why": "efêmero; expira sozinho"},
    {"store": "kafka",
     "why": "retenção do broker; não é loja consultável por pessoa"},
]


async def _post(url: str, token: str, body: dict, headers: dict | None = None,
                timeout: float = 15.0) -> tuple[int, Any]:
    h = {"X-Service-Token": token, "X-Service-Name": "analytics-api"}
    h.update(headers or {})
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=body, headers=h)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text


def _unavailable(what: str, detail: Any) -> str:
    msg = f"unavailable: {what}"
    if detail:
        msg += f" ({str(detail)[:200]})"
    return msg


async def resolve_customer(settings: Any, tenant_id: str, anchors: list[dict]) -> tuple[str, str]:
    """(customer_id, status). Nunca provisiona."""
    if not settings.channel_gateway_url or not settings.channel_gateway_service_token:
        return "", _unavailable("identity resolver",
                                "PLUGHUB_CHANNEL_GATEWAY_URL/SERVICE_TOKEN not set")
    try:
        code, data = await _post(
            f"{settings.channel_gateway_url}/v1/channels/webhook/identity/resolve",
            settings.channel_gateway_service_token,
            {"tenant_id": tenant_id, "anchors": anchors, "provision": False},
        )
    except Exception as exc:
        return "", _unavailable("identity resolver", exc)
    if code != 200 or not isinstance(data, dict):
        return "", _unavailable("identity resolver", f"HTTP {code}: {data}")
    cid = str(data.get("customer_id") or "")
    return cid, ("ok" if cid else "not_found")


async def gateway_export(settings: Any, tenant_id: str, customer_id: str,
                         session_ids: list[str]) -> dict:
    if not settings.channel_gateway_url or not settings.channel_gateway_service_token:
        why = _unavailable("channel-gateway", "PLUGHUB_CHANNEL_GATEWAY_URL/SERVICE_TOKEN not set")
        return {"customer": None, "customer_status": why, "attachments": [], "attachments_status": why}
    try:
        code, data = await _post(
            f"{settings.channel_gateway_url}/v1/channels/webhook/identity/subject-export",
            settings.channel_gateway_service_token,
            {"tenant_id": tenant_id, "customer_id": customer_id, "session_ids": session_ids},
        )
    except Exception as exc:
        code, data = 0, exc
    if code != 200 or not isinstance(data, dict):
        why = _unavailable("channel-gateway", f"HTTP {code}: {data}")
        return {"customer": None, "customer_status": why, "attachments": [], "attachments_status": why}
    return data


async def mailing_export(settings: Any, tenant_id: str, customer_ids: list[str],
                         contact_values: list[str]) -> dict:
    if not settings.mailing_api_url or not settings.mailing_service_token:
        return {"status": _unavailable("mailing-api", "PLUGHUB_MAILING_API_URL/SERVICE_TOKEN not set")}
    try:
        code, data = await _post(
            f"{settings.mailing_api_url}/v1/data-subject/export",
            settings.mailing_service_token,
            {"customer_ids": customer_ids, "contact_values": contact_values},
            headers={"X-Tenant-ID": tenant_id},
        )
    except Exception as exc:
        return {"status": _unavailable("mailing-api", exc)}
    if code != 200 or not isinstance(data, dict):
        return {"status": _unavailable("mailing-api", f"HTTP {code}: {data}")}
    return {"status": "ok", **data}


async def survey_export(settings: Any, tenant_id: str, customer_ids: list[str],
                        session_ids: list[str]) -> dict:
    if not settings.evaluation_api_url or not settings.evaluation_service_token:
        return {"status": _unavailable("evaluation-api", "PLUGHUB_EVALUATION_API_URL/SERVICE_TOKEN not set"),
                "surveys": []}
    try:
        code, data = await _post(
            f"{settings.evaluation_api_url}/v1/evaluation/data-subject/surveys",
            settings.evaluation_service_token,
            {"tenant_id": tenant_id, "customer_keys": customer_ids, "session_ids": session_ids},
        )
    except Exception as exc:
        return {"status": _unavailable("evaluation-api", exc), "surveys": []}
    if code != 200 or not isinstance(data, dict):
        return {"status": _unavailable("evaluation-api", f"HTTP {code}: {data}"), "surveys": []}
    return {"status": "ok", "surveys": data.get("surveys") or []}


def _iso(ts: Any) -> str | None:
    if ts is None:
        return None
    return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)


def fetch_clickhouse(client: Any, db: str, tenant_id: str, customer_ids: list[str]) -> dict:
    """Sessões da pessoa e, delas, mensagens (mascaradas) e insights."""
    rows = client.query(
        f"""
        SELECT session_id, channel, pool_id, customer_id, opened_at, closed_at,
               close_reason, outcome, origin, spawn_reason, root_session_id
        FROM {db}.sessions FINAL
        WHERE tenant_id = {{t:String}} AND customer_id IN {{ids:Array(String)}}
        ORDER BY opened_at
        LIMIT {MAX_SESSIONS + 1}
        """,
        parameters={"t": tenant_id, "ids": customer_ids},
    ).result_rows
    truncated = len(rows) > MAX_SESSIONS
    rows = rows[:MAX_SESSIONS]
    cols = ("session_id", "channel", "pool_id", "customer_id", "opened_at", "closed_at",
            "close_reason", "outcome", "origin", "spawn_reason", "root_session_id")
    sessions = [
        {k: (_iso(v) if k in ("opened_at", "closed_at") else v) for k, v in zip(cols, r)}
        for r in rows
    ]
    sids = [s["session_id"] for s in sessions]
    messages: list[dict] = []
    insights: list[dict] = []
    if sids:
        for r in client.query(
            f"""
            SELECT session_id, message_id, author_role, content_type, visibility, content, timestamp
            FROM {db}.messages FINAL
            WHERE tenant_id = {{t:String}} AND session_id IN {{s:Array(String)}}
            ORDER BY session_id, timestamp
            """,
            parameters={"t": tenant_id, "s": sids},
        ).result_rows:
            sid, mid, role, ctype, vis, content, ts = r
            try:
                parsed = json.loads(content) if content else ""
                if isinstance(parsed, dict) and "text" in parsed:
                    parsed = parsed["text"]
                content = parsed if isinstance(parsed, str) else content
            except (ValueError, TypeError):
                pass
            messages.append({"session_id": sid, "message_id": str(mid), "author_role": role,
                             "content_type": ctype, "visibility": vis, "content": content,
                             "timestamp": _iso(ts)})
        for r in client.query(
            f"""
            SELECT session_id, insight_type, category, value, timestamp
            FROM {db}.contact_insights FINAL
            WHERE tenant_id = {{t:String}} AND session_id IN {{s:Array(String)}}
            ORDER BY session_id, timestamp
            """,
            parameters={"t": tenant_id, "s": sids},
        ).result_rows:
            sid, itype, cat, val, ts = r
            insights.append({"session_id": sid, "insight_type": itype, "category": cat,
                             "value": val, "timestamp": _iso(ts)})
    return {"sessions": sessions, "truncated": truncated, "messages": messages, "insights": insights}


async def build_access_dossier(settings: Any, store: Any, tenant_id: str, *,
                               customer_id: str, anchors: list[dict]) -> dict:
    """Monta o dossiê. `anchors` = [{kind, value}] — phone/email/cpf."""
    dossier: dict[str, Any] = {"tenant_id": tenant_id, "not_covered": NOT_COVERED}

    resolved_by = "customer_id" if customer_id else ""
    if not customer_id and anchors:
        customer_id, status = await resolve_customer(settings, tenant_id, anchors)
        dossier["resolution"] = {"status": status, "by": [a["kind"] for a in anchors]}
        resolved_by = "anchors" if customer_id else ""
    else:
        dossier["resolution"] = {"status": "ok" if customer_id else "not_requested",
                                 "by": ["customer_id"] if customer_id else []}

    identity = await gateway_export(settings, tenant_id, customer_id, []) if customer_id else \
        {"customer": None, "customer_status": "not_found"}
    dossier["identity"] = {"status": identity.get("customer_status"), "record": identity.get("customer")}
    rec = identity.get("customer") or {}
    ids = [i for i in dict.fromkeys(
        [customer_id, rec.get("customer_id") or "", *(rec.get("merged_from") or [])]) if i]
    dossier["customer_ids"] = ids
    dossier["resolved_by"] = resolved_by

    contact_values = [a["value"] for a in anchors if a.get("kind") in ("phone", "email")]
    # O telefone/e-mail informado entra DIRETO na busca de sessões, além dos ids: na voz
    # SIP e no webhook o `customer_id` da sessão É o número de quem ligou (medido em
    # 2026-09-29 — `ani` nunca é preenchido, e a gravação da chamada fica numa sessão
    # cujo `customer_id` é `+55…`). Sem isto, pedir pelo telefone perdia as chamadas.
    session_keys = list(dict.fromkeys([*ids, *contact_values]))

    ch: dict = {"sessions": [], "truncated": False, "messages": [], "insights": []}
    ch_status = "ok"
    if session_keys:
        try:
            ch = await asyncio.to_thread(fetch_clickhouse, store.new_client(), store._database,
                                         tenant_id, session_keys)
        except Exception as exc:
            logger.error("data_subject: ClickHouse indisponivel — %s: %s", type(exc).__name__, exc)
            ch_status = _unavailable("clickhouse", exc)
    sids = [s["session_id"] for s in ch["sessions"]]
    dossier["sessions"] = {"status": ch_status, "count": len(sids), "truncated": ch["truncated"],
                           "items": ch["sessions"]}
    dossier["messages"] = {"status": ch_status, "note": "conteúdo MASCARADO (o desmascarado é AUD-01)",
                           "items": ch["messages"]}
    dossier["insights"] = {"status": ch_status, "items": ch["insights"]}

    att, mail, surv = await asyncio.gather(
        gateway_export(settings, tenant_id, "", sids) if sids else
        asyncio.sleep(0, result={"attachments": [], "attachments_status": "ok"}),
        mailing_export(settings, tenant_id, ids, contact_values),
        survey_export(settings, tenant_id, ids, sids),
    )
    dossier["attachments"] = {"status": att.get("attachments_status"),
                              "note": "metadado; os arquivos não são entregues por aqui",
                              "items": att.get("attachments") or []}
    dossier["outbound"] = mail
    dossier["surveys"] = {"status": surv["status"], "items": surv["surveys"]}
    return dossier
