"""
llm_accounts_catalog.py
LLM Accounts — loads the account catalog from the Config API and resolves
each account's secret from an environment variable, by naming convention.

Design (2026-07-02): "env only for secrets, everything else in Configuration"
(CLAUDE.md § Configuration — Single Source Invariants). The catalog entry
(provider, display_name, rpm/tpm limits, active) is UI-editable via the
Config API namespace `llm_accounts` (Configuration > Resources > LLM
Accounts page) — never the secret itself.

    GET /config/llm_accounts?tenant_id=<t>
    →   {
          "realtime_primary": {
            "provider":     "anthropic" | "openai",
            "display_name": "Realtime — conta principal",
            "rpm_limit":    60,
            "tpm_limit":    100000,
            "active":       true
          },
          ...
        }

The matching API key is resolved from:

    PLUGHUB_LLM_ACCOUNT_<ID_UPPER_SNAKE>_API_KEY

e.g. account id "realtime_primary" → PLUGHUB_LLM_ACCOUNT_REALTIME_PRIMARY_API_KEY.
No stored/free-typed env-var-name field — the id IS the convention, so a
misconfigured account is simply absent (never silently points at the wrong key).

Graceful by design: Config API unreachable, namespace empty, or an entry with
no matching env var → that account is skipped (logged), never blocks ai-gateway
boot. When the catalog is entirely empty/unreachable, callers fall back to the
legacy env-only mechanism (PLUGHUB_ANTHROPIC_API_KEYS / PLUGHUB_ANTHROPIC_CONFIG_IDS)
— zero regression for installations that haven't adopted the catalog yet.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger("plughub.ai_gateway.llm_accounts_catalog")


@dataclass
class CatalogAccount:
    account_id:   str
    provider:     str
    api_key:      str
    display_name: str = ""
    rpm_limit:    int = 60
    tpm_limit:    int = 100_000


def _env_var_name(account_id: str) -> str:
    """id → PLUGHUB_LLM_ACCOUNT_<ID_UPPER_SNAKE>_API_KEY."""
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", account_id).strip("_").upper()
    return f"PLUGHUB_LLM_ACCOUNT_{normalized}_API_KEY"


async def load_llm_accounts_catalog(
    config_api_url: str,
    tenant_id:      str,
    *,
    timeout_s:      float = 3.0,
) -> list[CatalogAccount]:
    """
    Fetches the `llm_accounts` namespace and resolves each entry's secret from
    its conventional env var. Returns [] on any failure (unreachable, empty,
    malformed) — callers must fall back to the legacy env-only mechanism.
    """
    url = f"{config_api_url.rstrip('/')}/config/llm_accounts"
    raw: dict = {}
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(url, params={"tenant_id": tenant_id})
        if resp.status_code == 200:
            body = resp.json()
            if isinstance(body, dict):
                raw = body
        elif resp.status_code != 404:
            logger.warning("llm_accounts catalog fetch %s → %s", url, resp.status_code)
    except Exception as exc:  # noqa: BLE001 — graceful: empty catalog on any error
        logger.warning("llm_accounts catalog fetch failed (%s) — falling back to legacy env mechanism: %s", url, exc)
        return []

    accounts: list[CatalogAccount] = []
    for account_id, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("active") is False:
            continue
        provider = entry.get("provider")
        if provider not in ("anthropic", "openai"):
            logger.warning("llm_accounts: account %s has invalid/missing provider — skipped", account_id)
            continue
        env_var = _env_var_name(account_id)
        api_key = os.environ.get(env_var, "").strip()
        if not api_key:
            logger.warning(
                "llm_accounts: account %s declared in Config API but %s is not set — skipped",
                account_id, env_var,
            )
            continue
        accounts.append(CatalogAccount(
            account_id=   account_id,
            provider=     provider,
            api_key=      api_key,
            display_name= str(entry.get("display_name") or account_id),
            rpm_limit=    int(entry.get("rpm_limit") or 60),
            tpm_limit=    int(entry.get("tpm_limit") or 100_000),
        ))

    if raw and not accounts:
        logger.warning(
            "llm_accounts: catalog has %d entries but none resolved to a usable "
            "account (check env vars) — falling back to legacy env mechanism",
            len(raw),
        )

    return accounts
