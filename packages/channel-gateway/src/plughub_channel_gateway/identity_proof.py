"""
identity_proof.py — a prova de posse FORA DE BANDA (AAS-19; adr-a2a-server-binding D12 item 2).

Quando o skill exige que a pessoa prove quem é e quem conversa NÃO é a pessoa — o assistente dela
pelo canal `a2a`, um parceiro —, o código de OTP não pode passar pela conversa: o agente nunca o vê
(a costura do segredo do Dialog Primitive, valendo para agente de fora). O fluxo pede um LINK
(`identity_proof_link`, mcp-server → `POST /v1/channels/webhook/identity/proof-link`), a pessoa o
abre no navegador, e TUDO do código acontece aqui:

    GET  /a2a/proof/{código}                      explica e oferece enviar o código — não gasta nada
    POST /a2a/proof/{código}  action=send         desafia a âncora AUTORITATIVA do cliente (OTP)
    POST /a2a/proof/{código}  action=verify code  confere; no sucesso a evidência vai ao escritor
                                                  único (`/internal/identity-evidence`) e o menu que
                                                  espera é acordado por SINAL da plataforma

O link NÃO é credencial: quem o tem só consegue pedir que um código vá ao telefone/e-mail do
cadastro. Por isso ele pode viajar pelo agente e ficar no transcrito. O que se protege:
  · o GET não envia código (preview de link de mensageiro buscaria a URL sozinho);
  · a página nunca mostra o código, nem no modo dev (ele vai ao log do OtpService);
  · só a prova CONCLUÍDA é gravada — `pending`/`failed` por pedido apagaria uma prova de pé, e
    quem tem o link não é necessariamente a pessoa;
  · a resposta não fica em cache, não manda `Referer`, não é indexada (headers da AAS-09).

Estado, em Redis, com o TTL do link:
    {t}:proof:code:{sha256(código)}  → a quem e onde (sessão, cliente, âncora)
    {t}:proof:session:{sid}          → o FATO "esta sessão espera prova" que o adapter A2A lê para
                                       responder `TASK_STATE_AUTH_REQUIRED` (com o link)

O tenant da página é o da INSTALAÇÃO, como em toda porta pública deste app. Inexistente, vencido e
já usado têm a MESMA página.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional, Protocol

import httpx

from .a2a_customer_token import HEADERS, _page
from .identity.index import PROVENANCE_AUTHORITATIVE

logger = logging.getLogger("plughub.channel-gateway.identity-proof")

PROOF_TTL_S   = 600            # validade do link; o menu que o espera deve ter timeout ≤ isto
PROOF_PREFIX  = "prf_"
MECHANISM     = "otp"
Publisher     = Callable[[str, dict, str], Awaitable[None]]


def code_key(tenant_id: str, code: str) -> str:
    return f"{tenant_id}:proof:code:{hashlib.sha256(code.encode()).hexdigest()}"


def session_key(tenant_id: str, session_id: str) -> str:
    return f"{tenant_id}:proof:session:{session_id}"


def anchor_hint(kind: str, value: str) -> str:
    """O bastante para a pessoa reconhecer o destino, nunca o bastante para identificá-la."""
    v = (value or "").strip()
    if kind == "email" and "@" in v:
        nome, dominio = v.split("@", 1)
        return f"{nome[:1]}•••@{dominio}"
    digitos = "".join(c for c in v if c.isdigit())
    return f"•••• {digitos[-2:]}" if len(digitos) >= 2 else "••••"


class _Adapter(Protocol):
    async def otp_challenge(self, tenant_id: str, customer_id: str, kind: str, value: str) -> dict: ...
    async def otp_verify(self, tenant_id: str, customer_id: str, kind: str, value: str, code: str) -> dict: ...


class _Identity(Protocol):
    async def anchor_provenance(self, tenant_id: str, customer_id: str, kind: str, value: str) -> str | None: ...


@dataclass
class ProofDeps:
    redis:         Any
    adapter:       _Adapter
    identity:      _Identity
    otp_refusal:   Callable[[str], Optional[dict]]
    publish:       Publisher
    base_url:      str
    mcp_url:       str
    service_token: str
    transport:     Optional[httpx.AsyncBaseTransport] = None


def _s(v: Any) -> str:
    return v.decode() if isinstance(v, bytes) else (v or "")


async def create_link(deps: ProofDeps, *, tenant_id: str, session_id: str, customer_id: str,
                      kind: str, value: str) -> dict:
    """Cria (ou SUBSTITUI) o link de prova desta sessão. `{created: false, reason}` nomeia a recusa."""
    meta_raw = await deps.redis.get(f"session:{session_id}:meta")
    try:
        meta = json.loads(_s(meta_raw)) if meta_raw else None
    except ValueError:
        meta = None
    if not meta or str(meta.get("tenant_id") or "") != tenant_id:
        return {"created": False, "reason": "session_unknown"}
    recusa = deps.otp_refusal(kind)
    if recusa is not None:
        return {"created": False, "reason": recusa.get("reason", "undeliverable_kind")}
    # PID-10 — o mesmo portão do desafio: só âncora AUTORITATIVA deste cliente. Conferido já aqui
    # para o fluxo ouvir a recusa agora, e não a pessoa descobri-la no navegador.
    if await deps.identity.anchor_provenance(tenant_id, customer_id, kind, value) != PROVENANCE_AUTHORITATIVE:
        return {"created": False, "reason": "anchor_not_authoritative"}
    base = (deps.base_url or "").strip().rstrip("/")
    if not base:
        logger.error("proof: PLUGHUB_A2A_PUBLIC_BASE_URL vazia — nenhum link de prova pode ser montado")
        return {"created": False, "reason": "not_configured"}

    anterior = await deps.redis.get(session_key(tenant_id, session_id))
    if anterior:
        try:
            await deps.redis.delete(f"{tenant_id}:proof:code:{json.loads(_s(anterior)).get('code_hash')}")
        except ValueError:
            pass
    code = PROOF_PREFIX + secrets.token_urlsafe(24)
    agora = datetime.now(timezone.utc)
    vence = (agora + timedelta(seconds=PROOF_TTL_S)).isoformat()
    url = f"{base}/a2a/proof/{code}"
    dica = anchor_hint(kind, value)
    # O valor da âncora fica aqui pelo tempo do link (é para onde o código vai); nunca em log.
    await deps.redis.set(code_key(tenant_id, code), json.dumps({
        "session_id": session_id, "customer_id": customer_id, "kind": kind, "value": value,
        "channel": meta.get("channel") or "", "created_at": agora.isoformat(), "expires_at": vence,
    }), ex=PROOF_TTL_S)
    await deps.redis.set(session_key(tenant_id, session_id), json.dumps({
        "code_hash": hashlib.sha256(code.encode()).hexdigest(), "url": url, "expires_at": vence,
        "mechanism": MECHANISM, "anchor_hint": dica, "customer_id": customer_id,
    }), ex=PROOF_TTL_S)
    logger.info("proof: link criado session=%s customer=%s kind=%s (vale %ss)",
                session_id, customer_id, kind, PROOF_TTL_S)
    return {"created": True, "url": url, "expires_at": vence, "expires_in_s": PROOF_TTL_S,
            "anchor_hint": dica}


async def _lookup(deps: ProofDeps, tenant_id: str, code: str) -> Optional[dict]:
    if not code.startswith(PROOF_PREFIX):
        return None
    raw = await deps.redis.get(code_key(tenant_id, code))
    try:
        return json.loads(_s(raw)) if raw else None
    except ValueError:
        return None


async def _record_evidence(deps: ProofDeps, tenant_id: str, rec: dict, provenance: str | None) -> bool:
    if not deps.mcp_url or not deps.service_token:
        logger.error("proof: PLUGHUB_MCP_SERVER_URL/PLUGHUB_MCP_INTERNAL_SERVICE_TOKEN vazio — a prova "
                     "conferida NÃO vira evidência session=%s", rec["session_id"])
        return False
    corpo = {"tenant_id": tenant_id, "session_id": rec["session_id"], "mechanism": MECHANISM,
             "status": "verified", "anchor_kind": rec["kind"], "customer_id": rec["customer_id"],
             "source": provenance or ""}
    try:
        async with httpx.AsyncClient(timeout=5.0, transport=deps.transport) as c:
            r = await c.post(f"{deps.mcp_url.rstrip('/')}/internal/identity-evidence", json=corpo,
                             headers={"x-service-token": deps.service_token})
    except httpx.HTTPError as exc:
        logger.error("proof: mcp-server inalcançável ao gravar a evidência session=%s (%s)",
                     rec["session_id"], type(exc).__name__)
        return False
    if r.status_code != 200:
        logger.error("proof: mcp-server RECUSOU a evidência session=%s HTTP %s: %s",
                     rec["session_id"], r.status_code, r.text[:200])
        return False
    return True


async def _wake(deps: ProofDeps, tenant_id: str, rec: dict) -> None:
    """Acorda o menu que espera a prova: um SINAL pelo caminho de sempre (`conversations.inbound`,
    que o bridge converte em `menu:signal`), nunca uma fala do cliente."""
    sid = rec["session_id"]
    await deps.publish("conversations.inbound", {
        "message_id": str(uuid.uuid4()), "contact_id": sid, "session_id": sid,
        "timestamp": datetime.now(timezone.utc).isoformat(), "direction": "inbound",
        "channel": rec.get("channel") or "", "content_type": "menu_result",
        "author": {"type": "customer"},
        "content": {"type": "menu_result", "payload": {"proof": "settled"}}, "context_snapshot": {},
    }, sid)


@dataclass(frozen=True)
class Page:
    html:    str
    status:  int = 200
    headers: Optional[dict] = None


async def page_get(deps: ProofDeps, tenant_id: str, code: str) -> Page:
    rec = await _lookup(deps, tenant_id, code)
    if rec is None:
        return Page(page_gone(), 410)
    return Page(page_offer(anchor_hint(rec["kind"], rec["value"])))


async def page_post(deps: ProofDeps, tenant_id: str, code: str, form: dict[str, str]) -> Page:
    rec = await _lookup(deps, tenant_id, code)
    if rec is None:
        return Page(page_gone(), 410)
    dica = anchor_hint(rec["kind"], rec["value"])
    action = form.get("action", "")
    if action == "send":
        res = await deps.adapter.otp_challenge(tenant_id, rec["customer_id"], rec["kind"], rec["value"])
        if res.get("sent") is True:
            return Page(page_code(dica))
        motivo = str(res.get("reason") or "unknown")
        logger.warning("proof: código NÃO enviado session=%s reason=%s", rec["session_id"], motivo)
        if motivo == "rate_limited":
            return Page(page_offer(dica, aviso="Muitos códigos pedidos. Aguarde alguns minutos e tente de novo."), 429)
        return Page(page_unavailable(), 503, {"Retry-After": "30"})
    if action == "verify":
        codigo = "".join(ch for ch in form.get("code", "") if ch.isdigit())
        if not codigo:
            return Page(page_code(dica, aviso="Digite o código que você recebeu."), 400)
        res = await deps.adapter.otp_verify(tenant_id, rec["customer_id"], rec["kind"], rec["value"], codigo)
        if res.get("verified") is not True:
            motivo = str(res.get("reason") or "")
            if motivo == "wrong_code":
                return Page(page_code(dica, aviso="O código não confere. Confira e digite de novo."), 400)
            # sem desafio vivo (venceu) ou tentativas esgotadas: pedir outro
            return Page(page_offer(dica, aviso="Este código não vale mais. Peça um novo."), 400)
        if not await _record_evidence(deps, tenant_id, rec, res.get("provenance")):
            return Page(page_unavailable(), 503, {"Retry-After": "5"})
        await deps.redis.delete(code_key(tenant_id, code), session_key(tenant_id, rec["session_id"]))
        try:
            await _wake(deps, tenant_id, rec)
        except Exception as exc:  # noqa: BLE001 — a prova está gravada; o menu vence pelo prazo
            logger.error("proof: evidência gravada mas o menu NÃO foi acordado session=%s (%s) — ele "
                         "segue pelo próprio prazo", rec["session_id"], type(exc).__name__)
        logger.info("proof: posse provada pelo link session=%s customer=%s", rec["session_id"], rec["customer_id"])
        return Page(page_done())
    return Page(page_offer(dica), 400)


# ── Páginas ──────────────────────────────────────────────────────────────────

def _aviso(aviso: str) -> str:
    return f'<p class="warn">{html.escape(aviso)}</p>' if aviso else ""


def page_offer(dica: str, aviso: str = "") -> str:
    return _page("Confirme sua identidade", f"""
<h1>Confirme que é você</h1>
<p>Um atendimento pediu que você confirme sua identidade. Vamos enviar um código para
<strong>{html.escape(dica)}</strong>, o contato que temos no seu cadastro.</p>
<p>Digite o código <strong>só nesta página</strong>. Ninguém do atendimento — nem o seu assistente —
vai pedir esse código.</p>
{_aviso(aviso)}
<form method="post"><input type="hidden" name="action" value="send">
<button type="submit">Enviar código</button></form>""")


def page_code(dica: str, aviso: str = "") -> str:
    return _page("Digite o código", f"""
<h1>Digite o código</h1>
<p>Enviamos um código para <strong>{html.escape(dica)}</strong>.</p>
{_aviso(aviso)}
<form method="post"><input type="hidden" name="action" value="verify">
<p><input name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="12"
 style="font-size:18px;padding:8px;width:10em" aria-label="código"></p>
<button type="submit">Confirmar</button></form>
<form method="post"><input type="hidden" name="action" value="send">
<p><button type="submit" style="background:#6b7280">Enviar outro código</button></p></form>""")


def page_done() -> str:
    return _page("Identidade confirmada", """
<h1>Pronto, identidade confirmada</h1>
<p>Pode voltar ao atendimento: ele continua sozinho. Esta página pode ser fechada.</p>""")


def page_gone() -> str:
    return _page("Link indisponível", """
<h1>Este link não está mais disponível</h1>
<p>Ele já foi usado ou venceu. Se ainda precisar confirmar sua identidade, peça um novo link no
atendimento.</p>""")


def page_unavailable() -> str:
    return _page("Tente de novo", """
<h1>Não foi possível concluir agora</h1>
<p>Tente de novo em instantes. O link continua valendo enquanto não vencer.</p>""")


__all__ = ["HEADERS", "PROOF_TTL_S", "ProofDeps", "Page", "anchor_hint", "create_link",
           "page_get", "page_post", "code_key", "session_key"]
