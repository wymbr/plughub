"""
a2a_customer_token.py — a RETIRADA do token do próprio cliente (AAS-09; adr-a2a-server-binding D6).

A tool `customer_agent_grant` (mcp-server) devolve ao fluxo um link
`{base}/a2a/customer-token/{código}`, e o fluxo o manda à pessoa. Esta página é o outro lado:

    GET  /a2a/customer-token/{código}   explica e mostra UM botão — não gasta o código
    POST /a2a/customer-token/{código}   retira: o auth-api cria a credencial AGORA e ela é mostrada
                                         uma vez, aqui, no navegador da pessoa

Por que o GET não revela: o link viaja por WhatsApp, e-mail e chat, onde o PREVIEW de link busca a
URL sozinho. Um GET que retirasse queimaria o código no robô do mensageiro, e a pessoa abriria um
link "já usado". Só o POST do botão retira.

O que protege a credencial, sabendo que o link fica no transcrito:
  · ela NÃO EXISTE antes da retirada (o auth-api guarda só o hash do código);
  · o código é de uso ÚNICO e vale minutos (`PICKUP_TTL_S` lá);
  · a resposta não fica em cache, não manda `Referer`, não é indexada, e a página não carrega
    nada de fora (CSP `default-src 'none'`).

O tenant é o da INSTALAÇÃO, como em toda porta pública deste app. Inexistente, vencido e já
retirado têm a MESMA página: quem tem só o código não aprende qual.
"""
from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from typing import Any, Literal, Optional

import httpx

logger = logging.getLogger("plughub.channel-gateway.a2a-customer-token")

HEADERS = {
    "Cache-Control":           "no-store",
    "Referrer-Policy":         "no-referrer",
    "X-Robots-Tag":            "noindex, nofollow",
    "X-Content-Type-Options":  "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
}

RedeemOutcome = Literal["ok", "gone", "unavailable"]


@dataclass(frozen=True)
class Redeemed:
    outcome:    RedeemOutcome
    principal:  Optional[dict[str, Any]] = None
    reason:     str = ""


async def redeem(code: str, *, tenant_id: str, auth_api_url: str, service_token: str,
                 timeout_s: float = 5.0) -> Redeemed:
    if not auth_api_url or not service_token:
        logger.error("a2a: retirada de token sem PLUGHUB_AUTH_API_URL/PLUGHUB_AUTH_API_SERVICE_TOKEN — recusada")
        return Redeemed("unavailable", reason="retirada não configurada no gateway")
    url = f"{auth_api_url.rstrip('/')}/auth/v1/agent-principals/customer-grants/redeem"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.post(url, json={"tenant_id": tenant_id, "pickup_code": code},
                             headers={"x-service-token": service_token})
    except httpx.HTTPError as exc:
        return Redeemed("unavailable", reason=f"auth-api inalcançável: {type(exc).__name__}")
    if r.status_code == 410:
        return Redeemed("gone")
    if r.status_code != 200:
        return Redeemed("unavailable", reason=f"auth-api respondeu {r.status_code}: {r.text[:200]}")
    return Redeemed("ok", principal=r.json())


_STYLE = """
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f5f7fa;color:#1f2937;
     margin:0;padding:16px}
main{max-width:560px;margin:24px auto;background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:24px}
h1{font-size:20px;margin:0 0 12px}
p,li{line-height:1.5}
code{display:block;word-break:break-all;background:#f3f4f6;border-radius:6px;padding:12px;font-size:14px;margin:8px 0}
button{background:#1B4F8A;color:#fff;border:0;border-radius:6px;padding:10px 16px;font-size:15px;cursor:pointer}
.warn{background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;padding:12px}
"""


def _page(title: str, body: str) -> str:
    return (f'<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="robots" content="noindex,nofollow"><title>{html.escape(title)}</title>'
            f'<style>{_STYLE}</style></head><body><main>{body}</main></body></html>')


def page_offer() -> str:
    return _page("Token para o seu assistente", """
<h1>Token para o seu assistente</h1>
<p>Este link gera o token que o <strong>seu</strong> assistente de IA usa para falar com o nosso
atendimento em seu nome.</p>
<p class="warn">O token aparece <strong>uma única vez</strong>. Gere quando puder copiá-lo para o
seu assistente. Se outra pessoa já tiver aberto este link, ele não funciona mais — nesse caso,
peça para revogar os seus tokens no atendimento.</p>
<form method="post"><button type="submit">Gerar meu token</button></form>""")


def page_token(p: dict[str, Any], card_urls: list[str]) -> str:
    cred = html.escape(str(p.get("credential") or ""))
    validade = html.escape(str(p.get("expires_at") or ""))
    mandato = ", ".join(html.escape(str(m)) for m in (p.get("mandate") or [])) or "—"
    cards = "".join(f"<code>{html.escape(u)}</code>" for u in card_urls)
    return _page("Seu token", f"""
<h1>Seu token</h1>
<p class="warn">Copie agora: ele <strong>não será mostrado de novo</strong>. Quem tiver este token
fala com o nosso atendimento em seu nome até {validade}.</p>
<code>{cred}</code>
<p>Cole no seu assistente como <em>Bearer token</em>. O endereço do agente:</p>
{cards}
<p>O que você autorizou o assistente a fazer: {mandato}</p>""")


def page_gone() -> str:
    return _page("Link indisponível", """
<h1>Este link não está mais disponível</h1>
<p>Ele já foi usado ou venceu. Peça um novo no atendimento. Se você não gerou o token, peça para
revogar os seus tokens.</p>""")


def page_unavailable() -> str:
    return _page("Tente de novo", """
<h1>Não foi possível gerar agora</h1>
<p>Tente de novo em instantes. O link continua valendo enquanto não vencer.</p>""")
