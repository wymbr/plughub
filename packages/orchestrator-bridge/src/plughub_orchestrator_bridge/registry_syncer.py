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

Prune (REGISTRY_SYNC_PRUNE=true, default):
  After upsert, list all agent_types in the registry for the tenant.
  Any entry present in the registry but NOT declared in the YAML is deleted.
  This makes the YAML the single source of truth — stale registrations from
  old seeds or manual API calls are removed automatically on each startup.

  Set REGISTRY_SYNC_PRUNE=false to disable deletion (e.g. multi-tenant
  production environments where some agents are registered outside YAML).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
import yaml

# Skill IDs must follow the canonical format defined in SkillSchema:
#   skill_{name}_v{n}   e.g. skill_copilot_sac_v1
_SKILL_ID_RE = re.compile(r"^skill_[a-z0-9_]+_v\d+$")

logger = logging.getLogger("plughub.registry-syncer")


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class SyncReport:
    tenant_id:        str
    pools_created:    int = 0
    pools_skipped:    int = 0   # already existed and no change needed
    pools_errors:     int = 0
    types_created:    int = 0
    types_updated:    int = 0   # PATCH applied
    types_skipped:    int = 0   # already existed and identical (PATCH returned 200 with no change)
    types_errors:     int = 0
    types_deleted:    int = 0   # pruned — present in registry but absent from YAML
    skills_upserted:  int = 0   # created or updated in Agent Registry
    skills_skipped:   int = 0   # no valid id: field — using YAML fallback at runtime
    skills_errors:    int = 0
    errors:           list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"tenant={self.tenant_id} "
            f"pools(created={self.pools_created} skip={self.pools_skipped} err={self.pools_errors}) "
            f"agent_types(created={self.types_created} updated={self.types_updated} "
            f"skip={self.types_skipped} deleted={self.types_deleted} err={self.types_errors}) "
            f"skills(upserted={self.skills_upserted} skip={self.skills_skipped} err={self.skills_errors})"
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

    def __init__(
        self,
        registry_url: str,
        config_path:  str | None = None,
        skills_dir:   str | None = None,
    ) -> None:
        self._registry_url  = registry_url.rstrip("/")
        self._config_path   = config_path
        self._skills_dir    = skills_dir

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

        # ── Sync skills FIRST (agent types reference skill_ids) ───────────
        await self._sync_skills(http, headers, report)

        # ── Sync pools (agent types reference them) ────────────────────
        for pool in cfg.get("pools", []):
            await self._sync_pool(http, headers, pool, report)

        # ── Sync agent types ───────────────────────────────────────────────
        declared_ids: set[str] = set()
        for at in cfg.get("agent_types", []):
            await self._sync_agent_type(http, headers, at, report)
            atid = at.get("agent_type_id")
            if atid:
                declared_ids.add(atid)

        # ── Prune stale agent types not present in YAML ────────────────
        prune = os.getenv("REGISTRY_SYNC_PRUNE", "true").lower() == "true"
        if prune and declared_ids:
            await self._prune_agent_types(http, headers, declared_ids, report)

        return report

    # ── Skill sync ────────────────────────────────────────────────────────────

    async def _sync_skills(
        self,
        http:    aiohttp.ClientSession,
        headers: dict,
        report:  SyncReport,
    ) -> None:
        """
        Reads all *.yaml files from SKILLS_DIR and upserts those with a valid
        id: field (matching ^skill_[a-z0-9_]+_v\d+$) into the Agent Registry.

        YAMLs without a valid id: field are silently skipped — the bridge will
        fall back to loading them from disk at runtime (YAML fallback path).

        Skill structure extracted from YAML:
          Top-level metadata → skill payload (skill_id, name, version, description,
                               classification, mention_commands)
          entry + steps      → skill.flow (SkillFlowSchema)

        Validation failures (HTTP 422) are logged as warnings and skipped — the
        RegistrySyncer never blocks startup on skill sync errors.
        """
        if not self._skills_dir:
            return

        skills_path = Path(self._skills_dir)
        if not skills_path.exists():
            logger.debug("RegistrySyncer: SKILLS_DIR not found — skipping skill sync: %s", skills_path)
            return

        for yaml_file in sorted(skills_path.glob("*.yaml")):
            try:
                raw = yaml.safe_load(yaml_file.read_text())
            except Exception as exc:
                logger.warning("RegistrySyncer: failed to parse skill YAML %s — %s", yaml_file.name, exc)
                report.skills_errors += 1
                continue

            if not isinstance(raw, dict):
                continue

            skill_id = raw.get("id", "")
            if not skill_id or not _SKILL_ID_RE.match(str(skill_id)):
                logger.debug(
                    "RegistrySyncer: skipping %s (no valid id: field) — will use YAML fallback",
                    yaml_file.name,
                )
                report.skills_skipped += 1
                continue

            entry = raw.get("entry")
            steps = raw.get("steps")
            if not entry or not steps:
                logger.warning(
                    "RegistrySyncer: skill %s has no entry/steps — skipping", skill_id
                )
                report.skills_skipped += 1
                continue

            # Build flow object — only SkillFlowSchema fields
            flow: dict = {"entry": entry, "steps": steps}
            if raw.get("required_context"):
                flow["required_context"] = raw["required_context"]

            # Build skill payload — supply defaults for optional metadata
            description = raw.get("description") or raw.get("name") or skill_id
            if isinstance(description, str):
                description = description.strip()

            payload: dict = {
                "skill_id":       skill_id,
                "name":           raw.get("name", skill_id),
                "version":        raw.get("version", "1.0"),
                "description":    description,
                "classification": raw.get("classification", {"type": "orchestrator"}),
                "flow":           flow,
            }
            # mention_commands lives at skill level (not inside flow)
            if raw.get("mention_commands"):
                payload["mention_commands"] = raw["mention_commands"]

            await self._upsert_skill(http, headers, skill_id, payload, report)

    async def _upsert_skill(
        self,
        http:     aiohttp.ClientSession,
        headers:  dict,
        skill_id: str,
        payload:  dict,
        report:   SyncReport,
    ) -> None:
        """PUT /v1/skills/{skill_id} — upsert semantics (create or update)."""
        url = f"{self._registry_url}/v1/skills/{skill_id}"
        try:
            async with http.put(url, headers=headers, json=payload,
                                timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status in (200, 201):
                    action = "created" if resp.status == 201 else "updated"
                    logger.info("  skill %s %s", skill_id, action)
                    report.skills_upserted += 1
                else:
                    body = await _safe_json(resp)
                    msg  = f"skill {skill_id}: PUT returned {resp.status} — {body}"
                    logger.warning("  %s", msg)
                    report.skills_errors += 1
                    # Non-fatal: bridge falls back to YAML at runtime
        except Exception as exc:
            msg = f"skill {skill_id}: PUT exception — {exc}"
            logger.error("  %s", msg)
            report.skills_errors += 1

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
                    return
                elif resp.status != 409:
                    body = await _safe_json(resp)
                    msg  = f"pool {pid}: POST returned {resp.status} — {body}"
                    logger.error("  %s", msg)
                    report.pools_errors += 1
                    report.errors.append(msg)
                    return
        except Exception as exc:
            msg = f"pool {pid}: POST exception — {exc}"
            logger.error("  %s", msg)
            report.pools_errors += 1
            report.errors.append(msg)
            return

        # 409 → already exists, PUT to apply any config drift (e.g. mentionable_pools)
        # Send only mutable fields (everything except pool_id)
        patch_body = {k: v for k, v in pool.items() if k != "pool_id"}
        patch_url  = f"{self._registry_url}/v1/pools/{pid}"
        try:
            async with http.put(patch_url, headers=headers, json=patch_body,
                                timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status in (200, 204):
                    logger.info("  pool %s updated (config drift)", pid)
                    report.pools_skipped += 1  # not an error — skipped creation but updated
                else:
                    body = await _safe_json(resp)
                    msg  = f"pool {pid}: PUT returned {resp.status} — {body}"
                    logger.warning("  %s", msg)
                    report.pools_skipped += 1  # non-fatal — pool exists, just couldn't update
        except Exception as exc:
            msg = f"pool {pid}: PUT exception — {exc}"
            logger.warning("  %s", msg)
            report.pools_skipped += 1  # non-fatal

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


    # ── Prune stale agent types ───────────────────────────────────────────────

    async def _prune_agent_types(
        self,
        http:         aiohttp.ClientSession,
        headers:      dict,
        declared_ids: set[str],
        report:       SyncReport,
    ) -> None:
        """
        List all agent_types registered for the tenant and DELETE any that are
        not present in the YAML declaration.  This makes the YAML the single
        source of truth: stale entries from old seeds or manual API calls are
        removed automatically on every startup.

        The DELETE endpoint in agent-registry publishes registry.changed to
        Kafka, which triggers InstanceBootstrap to remove the stale Redis
        instances automatically — no extra cleanup needed here.
        """
        url = f"{self._registry_url}/v1/agent-types"
        try:
            async with http.get(url, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    body = await _safe_json(resp)
                    logger.warning(
                        "RegistrySyncer: could not list agent_types for prune "
                        "(status=%s) — %s", resp.status, body
                    )
                    return
                payload = await resp.json(content_type=None)
                # GET /v1/agent-types returns { agent_types: [...], total: N }
                if isinstance(payload, dict):
                    registry_types: list[dict] = payload.get("agent_types", [])
                elif isinstance(payload, list):
                    registry_types = payload
                else:
                    registry_types = []
        except Exception as exc:
            logger.warning("RegistrySyncer: exception listing agent_types for prune — %s", exc)
            return

        for entry in registry_types:
            atid = entry.get("agent_type_id") or entry.get("id")
            if not atid or atid in declared_ids:
                continue
            # Stale entry — delete it
            del_url = f"{self._registry_url}/v1/agent-types/{atid}"
            try:
                async with http.delete(del_url, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status in (200, 204):
                        logger.info("  agent_type %s PRUNED (not in YAML)", atid)
                        report.types_deleted += 1
                    elif resp.status == 404:
                        # Already gone — count as deleted
                        logger.debug("  agent_type %s already absent (404 on DELETE)", atid)
                        report.types_deleted += 1
                    else:
                        body = await _safe_json(resp)
                        msg  = f"agent_type {atid}: DELETE returned {resp.status} — {body}"
                        logger.error("  %s", msg)
                        report.types_errors += 1
                        report.errors.append(msg)
            except Exception as exc:
                msg = f"agent_type {atid}: DELETE exception — {exc}"
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
