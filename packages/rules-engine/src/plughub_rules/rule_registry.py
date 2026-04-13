"""
rule_registry.py
Full lifecycle management for rules in Redis.
Spec: PlugHub v24.0 section 3.2b
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Any

from .lifecycle import validate_transition
from .models import Rule, RuleCreateRequest

logger = logging.getLogger("plughub.rules")


class RuleRegistry:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    # ─── key helpers ───────────────────────────────────────────────────

    @staticmethod
    def _rule_key(tenant_id: str, rule_id: str) -> str:
        return f"{tenant_id}:rule:{rule_id}"

    @staticmethod
    def _index_key(tenant_id: str) -> str:
        return f"{tenant_id}:rules:ids"

    @staticmethod
    def _active_cache_key(tenant_id: str) -> str:
        """New-style active cache key."""
        return f"{tenant_id}:rules:active"

    @staticmethod
    def _active_cache_key_legacy(tenant_id: str) -> str:
        """Legacy key used by RuleStore."""
        return f"rules:{tenant_id}:active"

    # ─── CRUD ──────────────────────────────────────────────────────────

    async def create(self, req: RuleCreateRequest) -> Rule:
        """Creates a rule with status=draft. Raises ValueError if rule_id already exists."""
        existing = await self.get(req.tenant_id, req.rule_id)
        if existing is not None:
            raise ValueError(f"Rule already exists: {req.rule_id}")

        now = datetime.now(timezone.utc).isoformat()
        rule = Rule(
            rule_id=     req.rule_id,
            tenant_id=   req.tenant_id,
            name=        req.name,
            status=      "draft",
            conditions=  req.conditions,
            logic=       req.logic,
            target_pool= req.target_pool,
            priority=    req.priority,
            created_at=  now,
            updated_at=  now,
        )

        # Persist rule JSON
        await self._redis.set(
            self._rule_key(req.tenant_id, req.rule_id),
            rule.model_dump_json(),
        )
        # Add to tenant index
        await self._redis.sadd(self._index_key(req.tenant_id), req.rule_id)

        return rule

    async def get(self, tenant_id: str, rule_id: str) -> Rule | None:
        raw = await self._redis.get(self._rule_key(tenant_id, rule_id))
        if raw is None:
            return None
        try:
            return Rule.model_validate_json(raw)
        except Exception:
            return None

    async def list_rules(
        self, tenant_id: str, status: str | None = None
    ) -> list[Rule]:
        rule_ids = await self._redis.smembers(self._index_key(tenant_id))
        if not rule_ids:
            return []

        rules: list[Rule] = []
        for rid in rule_ids:
            rid_str = rid.decode() if isinstance(rid, bytes) else rid
            rule = await self.get(tenant_id, rid_str)
            if rule is None:
                continue
            if status is not None and rule.status != status:
                continue
            rules.append(rule)

        return sorted(rules, key=lambda r: r.priority, reverse=True)

    async def update_status(
        self, tenant_id: str, rule_id: str, new_status: str
    ) -> Rule:
        """
        Transitions rule status.
        Validates lifecycle — raises ValueError on invalid transition.
        Updates active cache after transition.
        """
        rule = await self.get(tenant_id, rule_id)
        if rule is None:
            raise KeyError(f"Rule not found: {rule_id}")

        validate_transition(rule.status, new_status)

        updated = rule.model_copy(update={
            "status":     new_status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

        await self._redis.set(
            self._rule_key(tenant_id, rule_id),
            updated.model_dump_json(),
        )

        await self._refresh_active_cache(tenant_id)

        return updated

    # ─── cache helpers ─────────────────────────────────────────────────

    async def _refresh_active_cache(self, tenant_id: str) -> None:
        """
        Rebuilds both active cache keys from current rules.

        Writes:
        - {tenant_id}:rules:active        (new-style)
        - rules:{tenant_id}:active        (RuleStore backward-compat)
        """
        all_rules = await self.list_rules(tenant_id)
        active_rules = [
            r for r in all_rules if r.status in ("active", "shadow")
        ]
        payload = json.dumps([r.model_dump() for r in active_rules])

        await self._redis.set(self._active_cache_key(tenant_id), payload)
        await self._redis.set(self._active_cache_key_legacy(tenant_id), payload)
