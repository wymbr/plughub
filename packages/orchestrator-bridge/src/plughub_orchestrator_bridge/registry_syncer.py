"""
registry_syncer.py
Reads a declarative YAML config file (or a directory of per-tenant YAML files)
and ensures the Agent Registry (PostgreSQL via REST) contains the correct
pools and agent-type definitions.

Runs at orchestrator-bridge startup, BEFORE InstanceBootstrap.
This eliminates the external seed dependency: a fresh environment is fully
self-configuring from the YAML declarations alone.

YAML format (one file per tenant, or a single file):

    tenant_id: tenant_demo

    pools:
      - pool_id: demo_ia
        description: "Pool IVR de entrada"
        channel_types: [webchat, whatsapp]
        sla_target_ms: 480000

    agent_types:
      - agent_type_id: agente_demo_ia_v1
        framework: plughub-native
        execution_model: stateless
        role: executor
        max_concurrent_sessions: 10
        pools: [demo_ia]
        permissions:
          - mcp-server-plughub:agent_heartbeat
        capabilities:
          channels: webchat,whatsapp

Algorithm per entity:
  POST → 201   created, done
  POST → 409   already exists → PATCH to apply any config drift
  POST → 422   validation error (bad pool ref etc.) → logged as error, skip
  POST → other → logged as error, skip
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
import yaml

logger = logging.getLogger("plughub.registry-syncer")


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class SyncReport:
    tenant_id:      str
    pools_created:  int = 0
    pools_skipped:  int = 0   # already existed and no change needed
    pools_errors:   int = 0
    types_created:  int = 0
    types_updated:  int = 0   # PATCH applied
    types_skipped:  int = 0   # already existed and identical (PATCH returned 200 with no change)
    types_errors:   int = 0
    errors:         list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"tenant={self.tenant_id} "
            f"pools(created={self.pools_created} skip={self.pools_skipped} err={self.pools_errors}) "
            f"agent_types(created={self.types_created} updated={self.types_updated} "
            f"skip={self.types_skipped} err={self.types_errors})"
        )


# ── RegistrySyncer ────────────────────────────────────────────────────────────

class RegistrySyncer:
    """
    Reads one or more YAML declaration files and upserts pools + agent_types
    in the Agent Registry.

    config_path can be:
      - a single YAML file  (one or multiple tenant blocks)
      - a directory         (each *.yaml file is one tenant)

    If config_path is empty / not set, the syncer is a no-op so the bridge can
    run without a config dir (e.g. in integration tests that pre-seed the DB).
    """

    def __init__(self, registry_url: str, config_path: str | None = None) -> None:
        self._registry_url  = registry_url.rstrip("/")
        self._config_path   = config_path

    # ── Public API ────────────────────────────────────────────────────────────

    async def sync(self, http: aiohttp.ClientSession) -> list[SyncReport]:
        if not self._config_path:
            logger.debug("RegistrySyncer: no config path set — skipping registry sync")
            return []

        path = Path(self._config_path)
        if not path.exists():
            logger.warning("RegistrySyncer: config path does not exist: %s", path)
            return []

        configs = self._load_configs(path)
        if not configs:
            logger.info("RegistrySyncer: no tenant configs found in %s", path)
            return []

        reports: list[SyncReport] = []
        for cfg in configs:
            report = await self._sync_tenant(http, cfg)
            reports.append(report)
            level = logging.WARNING if report.errors else logging.INFO
            logger.log(level, "Registry sync: %s", report.summary())
            for err in report.errors:
                logger.warning("  !! %s", err)
        return reports

    # ── Config loading ────────────────────────────────────────────────────────

    def _load_configs(self, path: Path) -> list[dict]:
        if path.is_file():
            return self._load_yaml_file(path)
        # directory — load all *.yaml files
        configs: list[dict] = []
        for f in sorted(path.glob("*.yaml")):
            configs.extend(self._load_yaml_file(f))
        return configs

    @staticmethod
    def _load_yaml_file(path: Path) -> list[dict]:
        try:
            raw = yaml.safe_load(path.read_text())
        except Exception as exc:
            logger.error("RegistrySyncer: failed to load %s — %s", path, exc)
            return []
        if isinstance(raw, dict):
            return [raw]
        if isinstance(raw, list):
            return raw
        logger.warning("RegistrySyncer: unexpected YAML structure in %s", path)
        return []

    # ── Per-tenant sync ───────────────────────────────────────────────────────

    async def _sync_tenant(self, http: aiohttp.ClientSession, cfg: dict) -> SyncReport:
        tenant_id = cfg.get("tenant_id", "")
        if not tenant_id:
            logger.error("RegistrySyncer: tenant_id missing in config block — skipping")
            return SyncReport(tenant_id="<unknown>")

        report  = SyncReport(tenant_id=tenant_id)
        headers = {"x-tenant-id": tenant_id, "x-user-id": "registry-syncer"}

        # ── Sync pools first (agent types reference them) ──────────────────
        for pool in cfg.get("pools", []):
            await self._sync_pool(http, headers, pool, report)

        # ── Sync agent types ───────────────────────────────────────────────
        for at in cfg.get("agent_types", []):
            await self._sync_agent_type(http, headers, at, report)

        return report

    # ── Pool sync ─────────────────────────────────────────────────────────────

    async def _sync_pool(
        self,
        http:    aiohttp.ClientSession,
        headers: dict,
        pool:    dict,
        report:  SyncReport,
    ) -> None:
        pid = pool.get("pool_id", "<unknown>")
        url = f"{self._registry_url}/v1/pools"

        try:
            async with http.post(url, headers=headers, json=pool,
                                 timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 201:
                    logger.info("  pool %s created", pid)
                    report.pools_created += 1
                elif resp.status == 409:
                    # Pool already exists — pools are immutable (structural), skip silently
                    report.pools_skipped += 1
                else:
                    body = await _safe_json(resp)
                    msg  = f"pool {pid}: POST returned {resp.status} — {body}"
                    logger.error("  %s", msg)
                    report.pools_errors += 1
                    report.errors.append(msg)
        except Exception as exc:
            msg = f"pool {pid}: exception — {exc}"
            logger.error("  %s", msg)
            report.pools_errors += 1
            report.errors.append(msg)

    # ── Agent type sync ───────────────────────────────────────────────────────

    async def _sync_agent_type(
        self,
        http:    aiohttp.ClientSession,
        headers: dict,
        at:      dict,
        report:  SyncReport,
    ) -> None:
        atid = at.get("agent_type_id", "<unknown>")
        url  = f"{self._registry_url}/v1/agent-types"

        try:
            async with http.post(url, headers=headers, json=at,
                                 timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 201:
                    logger.info("  agent_type %s created", atid)
                    report.types_created += 1
                    return

                if resp.status != 409:
                    body = await _safe_json(resp)
                    msg  = f"agent_type {atid}: POST returned {resp.status} — {body}"
                    logger.error("  %s", msg)
                    report.types_errors += 1
                    report.errors.append(msg)
                    return

        except Exception as exc:
            msg = f"agent_type {atid}: POST exception — {exc}"
            logger.error("  %s", msg)
            report.types_errors += 1
            report.errors.append(msg)
            return

        # 409 → already exists, PATCH to apply any config drift
        # Send only mutable fields (everything except agent_type_id)
        patch_body = {k: v for k, v in at.items() if k != "agent_type_id"}
        patch_url  = f"{self._registry_url}/v1/agent-types/{atid}"
        try:
            async with http.patch(patch_url, headers=headers, json=patch_body,
                                  timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status in (200, 204):
                    logger.info("  agent_type %s patched (pools/config updated)", atid)
                    report.types_updated += 1
                else:
                    body = await _safe_json(resp)
                    if resp.status == 404:
                        # Edge case: PATCH endpoint not yet deployed — log warn but continue
                        logger.warning(
                            "  agent_type %s already exists but PATCH endpoint returned 404 "
                            "(registry may need rebuild). Config may be stale.", atid
                        )
                        report.types_skipped += 1
                    else:
                        msg = f"agent_type {atid}: PATCH returned {resp.status} — {body}"
                        logger.error("  %s", msg)
                        report.types_errors += 1
                        report.errors.append(msg)
        except Exception as exc:
            msg = f"agent_type {atid}: PATCH exception — {exc}"
            logger.error("  %s", msg)
            report.types_errors += 1
            report.errors.append(msg)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _safe_json(resp: aiohttp.ClientResponse) -> Any:
    try:
        return await resp.json(content_type=None)
    except Exception:
        try:
            return await resp.text()
        except Exception:
            return "<unreadable>"
