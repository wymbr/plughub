"""
evaluation_consumer.py
Routing Engine — Evaluation Consumer.
Spec: PlugHub v24.0 section 10.2 (Execução de Avaliações)

Consumes evaluation.events (event: evaluation.requested) and triggers the
SkillFlowEngine via the skill-flow-service HTTP API.

Each evaluation.requested maps to an independent SkillFlow session:
  session_id  = evaluation_id   (unique per evaluation)
  customer_id = agent_id        (the agent being evaluated)
  skill_id    = agente_avaliacao_v1  (generic evaluation orchestrator)
  flow        = loaded from skill registry at startup
  session_context = full evaluation.requested payload

The flow is fully managed by the SkillFlowEngine (pipeline_state in Redis).
This consumer is a fire-and-forget dispatcher — it publishes the run request
and the SkillFlow takes care of transcript_get, evaluation_context_resolve,
reason, and evaluation_publish steps.
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
import yaml
from aiokafka import AIOKafkaConsumer

from .config import get_settings

logger = logging.getLogger("plughub.routing.evaluation_consumer")


class EvaluationConsumer:
    """
    Kafka consumer for evaluation.events.
    Filters for event == "evaluation.requested" and dispatches to the
    skill-flow-service via HTTP POST /execute.
    """

    def __init__(
        self,
        http_client:           httpx.AsyncClient,
        skill_flow_service_url: str,
        evaluation_skill_id:   str,
        skill_flow:            dict | None = None,
    ) -> None:
        self._http                  = http_client
        self._skill_flow_service    = skill_flow_service_url.rstrip("/")
        self._evaluation_skill_id   = evaluation_skill_id
        # The SkillFlow YAML parsed as dict — loaded once from skill registry at startup.
        # If None, the consumer skips execution until a flow is available.
        self._flow: dict | None     = skill_flow

    def set_flow(self, flow: dict) -> None:
        """Updates the cached SkillFlow. Called after loading from skill registry."""
        self._flow = flow
        logger.info(
            "Evaluation SkillFlow loaded: skill_id=%s steps=%d",
            self._evaluation_skill_id, len(flow.get("steps", [])),
        )

    async def run(self, kafka_topic: str, kafka_brokers: str, kafka_group_id: str) -> None:
        consumer = AIOKafkaConsumer(
            kafka_topic,
            bootstrap_servers  = kafka_brokers,
            group_id           = kafka_group_id + "-evaluation",
            value_deserializer = lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset  = "latest",
        )
        await consumer.start()
        logger.info("✅ Evaluation Consumer started — topic: %s", kafka_topic)

        try:
            async for msg in consumer:
                asyncio.create_task(self._dispatch(msg.value))
        finally:
            await consumer.stop()

    async def _dispatch(self, payload: dict) -> None:
        event_type = payload.get("event", "")
        if event_type != "evaluation.requested":
            return

        evaluation_id = payload.get("evaluation_id", "?")
        try:
            await self._trigger_skill_flow(payload)
        except Exception as exc:
            logger.error(
                "Failed to trigger SkillFlow evaluation_id=%s: %s",
                evaluation_id, exc,
            )

    async def _trigger_skill_flow(self, payload: dict) -> None:
        if self._flow is None:
            logger.warning(
                "SkillFlow not loaded yet — skipping evaluation_id=%s",
                payload.get("evaluation_id"),
            )
            return

        evaluation_id = payload["evaluation_id"]
        tenant_id     = payload["tenant_id"]
        agent_id      = payload.get("agent", {}).get("agent_id", evaluation_id)

        body: dict[str, Any] = {
            "tenant_id":       tenant_id,
            "session_id":      evaluation_id,   # evaluation_id serves as session_id
            "customer_id":     agent_id,         # agent_id serves as customer_id
            "skill_id":        self._evaluation_skill_id,
            "flow":            self._flow,
            "session_context": payload,          # full evaluation.requested payload
        }

        url = f"{self._skill_flow_service}/execute"
        response = await self._http.post(url, json=body, timeout=30.0)

        if response.status_code == 412:
            # PRECONDITION_FAILED — another engine instance is already running this evaluation
            logger.warning(
                "Evaluation already running: evaluation_id=%s active_job_id=%s",
                evaluation_id, response.json().get("active_job_id"),
            )
            return

        response.raise_for_status()
        result = response.json()

        logger.info(
            "SkillFlow evaluation dispatched: evaluation_id=%s outcome=%s",
            evaluation_id, result.get("outcome"),
        )


def _skills_dir() -> Path:
    """
    Resolves the canonical skills directory: packages/skill-flow-engine/skills/
    Path from this file:
      src/plughub_routing/ → src/ → routing-engine/ → packages/ → skill-flow-engine/skills/
    """
    return Path(__file__).parent.parent.parent.parent / "skill-flow-engine" / "skills"


def load_evaluation_flow_from_disk(evaluation_skill_id: str) -> dict | None:
    """
    Loads the evaluation SkillFlow from the bundled YAML file.
    Primary source: packages/routing-engine/skills/{skill_id}.yaml
    Returns the parsed flow dict, or None if the file is not found.
    """
    skill_path = _skills_dir() / f"{evaluation_skill_id}.yaml"
    if not skill_path.exists():
        logger.warning(
            "Bundled SkillFlow not found at %s", skill_path,
        )
        return None
    try:
        with open(skill_path, "r", encoding="utf-8") as f:
            flow = yaml.safe_load(f)
        logger.info(
            "Evaluation SkillFlow loaded from disk: skill_id=%s steps=%d",
            evaluation_skill_id, len((flow or {}).get("steps", [])),
        )
        return flow
    except Exception as exc:
        logger.error("Failed to parse SkillFlow YAML %s: %s", skill_path, exc)
        return None


async def load_evaluation_flow(
    skill_flow_service_url: str,
    evaluation_skill_id: str,
    http_client: httpx.AsyncClient,
) -> dict | None:
    """
    Loads the evaluation SkillFlow definition.

    Priority:
    1. Bundled YAML file on disk (packages/routing-engine/skills/)
    2. Skill registry HTTP endpoint (GET /skills/{skill_id}) — optional override
       that allows hot-reload without a service restart.

    Returns the parsed flow dict, or None if not available.
    """
    # 1. Disk (primary — always available, no network dependency)
    flow = load_evaluation_flow_from_disk(evaluation_skill_id)
    if flow is not None:
        return flow

    # 2. HTTP skill registry (fallback)
    url = f"{skill_flow_service_url.rstrip('/')}/skills/{evaluation_skill_id}"
    try:
        response = await http_client.get(url, timeout=10.0)
        if response.status_code == 200:
            data = response.json()
            return data.get("flow") or data
        logger.warning(
            "Skill registry returned %d for skill_id=%s — consumer will start without flow",
            response.status_code, evaluation_skill_id,
        )
    except Exception as exc:
        logger.warning(
            "Could not load evaluation SkillFlow from registry: %s — consumer will start without it",
            exc,
        )
    return None
