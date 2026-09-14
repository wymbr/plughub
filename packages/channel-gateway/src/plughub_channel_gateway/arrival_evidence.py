"""
arrival_evidence.py — PID-09 (2026-09-14)

A CHEGADA pelo WhatsApp é evidência de posse do telefone (ADR D9). A Meta assina o webhook,
e o `from` de uma mensagem é o número que a enviou: quando esse número é a âncora
AUTORITATIVA de um cliente no cadastro, a sessão aberta por ele provou a mesma posse que o
OTP provaria.

Este módulo decide o que a chegada afirma e pede ao mcp-server que grave — o escritor único
da evidência é lá (`POST /internal/identity-evidence`), nunca uma escrita direta no
ContextStore daqui.

    assinatura não conferida     → `not_run`  (sem segredo configurado a chegada não prova nada)
    número sem dono autoritativo → `failed`   (declarado, de canal, ou sem cadastro)
    dono autoritativo            → `verified` com `customer_id` e `source=authoritative`

Nada disto bloqueia a mensagem do cliente: falhar aqui só LOGA, e o cliente volta ao OTP.
Uma falha ao CONSULTAR o cadastro não vira `failed` — apagaria uma prova válida por um erro
nosso; não grava nada e diz por quê.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx

logger = logging.getLogger("plughub.channel_gateway.arrival_evidence")


class _OwnerLookup(Protocol):
    async def authoritative_owner(self, tenant_id: str, kind: str, value: str) -> tuple[str, str] | None: ...


def whatsapp_from_as_phone(from_field: str) -> str:
    """O `from` da Meta é o número internacional SEM `+`; a âncora é E.164."""
    v = (from_field or "").strip()
    return v if v.startswith("+") else "+" + v


class ArrivalEvidenceRecorder:
    def __init__(
        self,
        *,
        identity:      _OwnerLookup,
        mcp_url:       str,
        service_token: str,
        timeout_s:     float = 3.0,
        transport:     httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._identity  = identity
        self._url       = (mcp_url or "").rstrip("/")
        self._token     = service_token or ""
        self._timeout   = timeout_s
        self._transport = transport

    def configured(self) -> str | None:
        """None quando pronto; senão, o que falta — dito no boot, não descoberto no cliente."""
        if not self._url:
            return "PLUGHUB_MCP_SERVER_URL vazio"
        if not self._token:
            return "PLUGHUB_MCP_INTERNAL_SERVICE_TOKEN vazio"
        return None

    async def record_whatsapp(
        self, *, tenant_id: str, session_id: str, from_field: str, authenticated: bool,
    ) -> dict[str, Any]:
        """Registra a chegada. Devolve `{status}` gravado, ou `{skipped: motivo}`."""
        falta = self.configured()
        if falta:
            logger.warning("arrival: evidência da chegada NÃO registrada session=%s — %s", session_id, falta)
            return {"skipped": "not_configured"}

        corpo: dict[str, Any] = {
            "tenant_id": tenant_id, "session_id": session_id,
            "mechanism": "whatsapp", "anchor_kind": "phone",
        }
        if not authenticated:
            logger.warning(
                "arrival: assinatura da Meta NÃO conferida (whatsapp_app_secret vazio) session=%s "
                "— a chegada não prova posse", session_id,
            )
            corpo["status"] = "not_run"
        else:
            try:
                dono = await self._identity.authoritative_owner(
                    tenant_id, "phone", whatsapp_from_as_phone(from_field),
                )
            except Exception as exc:  # noqa: BLE001 — degradar logando, nunca apagar prova
                logger.error(
                    "arrival: cadastro inacessível ao conferir o número session=%s (%s: %s) "
                    "— nada gravado, o cliente volta ao OTP", session_id, type(exc).__name__, exc,
                )
                return {"skipped": "lookup_failed"}
            if dono:
                # a procedência RELATADA é a que o cadastro devolveu — este módulo não a afirma
                cliente, procedencia = dono
                corpo.update(status="verified", customer_id=cliente, source=procedencia)
            else:
                corpo["status"] = "failed"

        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
                r = await c.post(
                    f"{self._url}/internal/identity-evidence",
                    json=corpo, headers={"x-service-token": self._token},
                )
        except Exception as exc:  # noqa: BLE001
            logger.error("arrival: mcp-server inalcançável session=%s (%s: %s)", session_id, type(exc).__name__, exc)
            return {"skipped": "mcp_unreachable"}
        if r.status_code != 200:
            logger.error(
                "arrival: mcp-server RECUSOU a evidência session=%s status=%s HTTP %s: %s",
                session_id, corpo["status"], r.status_code, r.text[:200],
            )
            return {"skipped": f"http_{r.status_code}"}
        logger.info(
            "arrival: whatsapp=%s session=%s%s", corpo["status"], session_id,
            f" customer={corpo['customer_id']}" if "customer_id" in corpo else "",
        )
        return {"status": corpo["status"]}
