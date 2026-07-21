"""
identity_client.py
Thin async client for the channel-gateway Identity Resolver (Fase 3b — opt-out global).

The customer cadastro is the single source of `do_not_contact` (opt-out por canal /
total). The outbound engine never stores opt-out itself — it READS the customer's
attributes here and WRITES them (global unsubscribe) through the same resolver.

Degradation on read is graceful and LOUD: on error `get_do_not_contact` returns None
and logs; the eligibility engine then treats the customer as NOT opted-out (allow) —
consistent with the other gates' "degrade to fireable". (Consent-strict tenants that
prefer block-on-outage = a future config; documented in docs/arcos/outbound.md.)
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("plughub.mailing.identity")


class IdentityClient:
    def __init__(self, base_url: str, timeout_s: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    async def get_do_not_contact(self, tenant_id: str, customer_id: str) -> dict | None:
        """Returns the customer's `do_not_contact` attribute ({all?, channels?}) or
        None (no customer / not set / error → treated as NOT opted-out)."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                r = await client.get(
                    f"{self.base_url}/v1/channels/webhook/identity/customers/{customer_id}",
                    params={"tenant_id": tenant_id},
                )
            if r.status_code == 404:
                return None
            r.raise_for_status()
            attrs = (r.json() or {}).get("attributes") or {}
            dnc = attrs.get("do_not_contact")
            return dnc if isinstance(dnc, dict) else None
        except Exception as exc:
            logger.warning(
                "identity get_do_not_contact failed (customer=%s) — degrading to NOT opted-out: %s",
                customer_id, exc,
            )
            return None

    async def set_do_not_contact(
        self, tenant_id: str, customer_id: str, dnc: dict[str, Any],
    ) -> bool:
        """Global opt-out write (mailing_unsubscribe scope=global): merges
        `{do_not_contact: dnc}` into the customer's cadastro attributes."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                r = await client.post(
                    f"{self.base_url}/v1/channels/webhook/identity/attributes",
                    json={"tenant_id": tenant_id, "customer_id": customer_id,
                          "attributes": {"do_not_contact": dnc}},
                )
                r.raise_for_status()
                return bool((r.json() or {}).get("updated"))
        except Exception as exc:
            logger.warning(
                "identity set_do_not_contact failed (customer=%s): %s", customer_id, exc,
            )
            return False
