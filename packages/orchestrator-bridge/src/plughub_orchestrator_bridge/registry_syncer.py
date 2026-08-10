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
  POST → 409   already exists → see Precedence below
  POST → 422   validation error (bad pool ref etc.) → logged as error, skip
  POST → other → logged as error, skip

Precedence (config-consolidation — seed-if-absent / DB-owned, DEFAULT):
  On 409 (entity already exists) the syncer DOES NOT overwrite it — the DB is
  authoritative, so UI edits (escalation_pools, mentionable_pools, hooks, deploy
  capacity, …) survive restarts/rebuilds. The YAML only SEEDS a fresh/empty
  registry. Set REGISTRY_SYNC_RECONCILE=true for the legacy dev/GitOps behaviour
  where the YAML re-applies its config over existing rows on every startup.

  Applies to pools, deploy-slots AND **skills** (changed 2026-07-13). Skills used
  to be an unconditional upsert ("they are code, not tenant config"), but that
  premise contradicted the existence of the Skills editor: the upsert carried
  `x-skill-publish: true`, which makes the registry write
  `{ flow: <yaml>, flow_draft: DbNull }` — so **every bridge boot overwrote
  production AND WIPED the editor's draft**, silently destroying UI work. Either
  the file owns the skill (and the editor is a lie) or the DB owns it. We chose
  DB-owned, like pools: the YAML seeds, the editor is authoritative.

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

# Skill IDs are stable slugs defined in SkillSchema (Skill Versioning Fase A):
#   skill_{slug}   e.g. skill_copilot_sac   (legacy skill_{name}_v{n} still matches the slug)
_SKILL_ID_RE = re.compile(r"^skill_[a-z0-9_]+$")

# ── Teardown-hook safety guard (dialog-primitive follow-up, 2026-07-06) ─────────
# A pool hook (on_contact_end/on_human_end/post_human) runs its target skill as a
# conference specialist DURING contact/segment teardown. The bridge holds the
# contact open and watches the hook agent's outcome to decide when to close. A step
# that SUSPENDS the flow (delegate/suspend/collect) makes the bridge treat the hook
# as "done" → it closes the contact BEFORE the I/O renders (silent footgun — no
# error today). Teardown-hook skills must do I/O INLINE (form_get + menu), never via
# a suspending step. This guard turns the footgun into a loud config ERROR at sync.
# See docs/product/dialog-primitive-and-runner-design.md.
_TEARDOWN_HOOK_KEYS   = ("on_contact_end", "on_human_end", "post_human")
_SUSPENDING_STEP_TYPES = frozenset({"delegate", "suspend", "collect"})

logger = logging.getLogger("plughub.registry-syncer")


def _runs_as_workflow(entry: dict) -> bool:
    """
    A entrada de hook roda como WORKFLOW destacado (fora da conferência) ou como
    especialista DENTRO dela?

    ⚠️ **Espelho de `_is_workflow_dispatch` em `main.py:fire_pool_hooks`.** Os dois têm
    de concordar: divergir faz o guard acima reprovar configuração que o bridge executa
    sem problema — ou, pior, calar sobre uma que trava de verdade.

        workflow ⇔ dispatch == "detached"  OU  (side == "agent" E dispatch == "inline")

    Só resta **`side=customer` + `inline`** dentro da conferência: o NPS, que precisa do
    WS vivo do cliente. É o único caso em que a premissa do guard — *"o bridge fecha o
    contato antes de o I/O renderizar"* — se aplica, e portanto o único a checar.

    ─── Por que olhar só `dispatch` NÃO basta ───────────────────────────────────
    Foi o erro da primeira tentativa deste conserto (2026-08-10): isentar apenas
    `detached` deixou o ERROR de pé, porque o wrap-up do demo é `dispatch: inline`. No
    **wrap-up unificado** (Phase 0+1+3), `inline` não quer dizer "roda na conferência" —
    quer dizer "o Console AUTO-REIVINDICA o item", sobre a MESMA máquina destacada.
    `dispatch` governa a ENTREGA; é o `side` que decide se há conferência.

    Defaults idênticos aos do bridge (`side=agent`, `dispatch=inline`): entrada antiga
    sem nenhum dos dois campos é `agent`+`inline` ⇒ workflow.
    """
    side     = (entry.get("side", "agent") or "agent")
    dispatch = (entry.get("dispatch", "inline") or "inline")
    return dispatch == "detached" or (side == "agent" and dispatch == "inline")


def _contained_in(subset: object, superset: object) -> bool:
    """
    Todo campo de `subset` existe e é igual em `superset`? (chaves EXTRA no superset são OK)

    Por que contenção e não igualdade: o agent-registry grava o flow **depois de o Zod
    aplicar defaults** (`interaction: "text"`, `timeout_hours: 48`, `urgency: "normal"`,
    …). O DB, portanto, tem legitimamente MAIS chaves que o YAML. Comparar por igualdade
    acusaria drift em todo skill a cada boot — que é exatamente o falso positivo do D4
    ("a linha foi escrita" ≠ "o conteúdo mudou"), só que com outra roupa.
    """
    if isinstance(subset, dict):
        if not isinstance(superset, dict):
            return False
        return all(
            k in superset and _contained_in(v, superset[k])
            for k, v in subset.items()
        )
    if isinstance(subset, list):
        if not isinstance(superset, list) or len(subset) != len(superset):
            return False
        return all(_contained_in(a, b) for a, b in zip(subset, superset))
    return subset == superset


def _flow_differs(db_flow: object, yaml_flow: object) -> bool:
    """O YAML diz algo que o DB não diz? (defaults do Zod no DB não contam como drift)"""
    if not isinstance(db_flow, dict) or not isinstance(yaml_flow, dict):
        return False   # sem base de comparação → não alarma
    return not _contained_in(yaml_flow, db_flow)


def _reconcile_enabled() -> bool:
    """
    Provisioning precedence (config-consolidation):
      default (seed-if-absent / DB-owned): the YAML seeds an EMPTY registry
        (201 on create); once a pool/deploy-slot exists, the DB is authoritative
        and the syncer NEVER overwrites it on restart — UI edits (escalation_pools,
        mentionable_pools, hooks, deploy capacity, …) survive rebuilds.
      REGISTRY_SYNC_RECONCILE=true: legacy dev/GitOps mode — the YAML wins,
        re-applying config drift over existing rows on every startup.
    """
    return os.getenv("REGISTRY_SYNC_RECONCILE", "false").strip().lower() in (
        "1", "true", "yes", "on",
    )


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class SyncReport:
    tenant_id:              str
    pools_created:          int = 0
    pools_skipped:          int = 0   # already existed and no change needed
    pools_errors:           int = 0
    types_created:          int = 0
    types_updated:          int = 0   # PATCH applied
    types_skipped:          int = 0   # already existed and identical (PATCH returned 200 with no change)
    types_errors:           int = 0
    types_deleted:          int = 0   # pruned — present in registry but absent from YAML
    journey_types_created:  int = 0
    journey_types_skipped:  int = 0   # already existed (409) or PATCH no-op
    journey_types_errors:   int = 0
    skills_upserted:        int = 0   # created or updated in Agent Registry
    skills_skipped:         int = 0   # no valid id: field — using YAML fallback at runtime
    skills_errors:          int = 0
    deploy_slots_set:       int = 0   # Fase 3c — PoolSkillSlot.current promoted from YAML
    deploy_slots_skipped:   int = 0   # already matching desired skill+capacity
    deploy_slots_errors:    int = 0
    hook_violations:        int = 0   # teardown-hook guard: suspending step in a hook-target skill
    errors:                 list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"tenant={self.tenant_id} "
            f"pools(created={self.pools_created} skip={self.pools_skipped} err={self.pools_errors}) "
            f"agent_types(created={self.types_created} updated={self.types_updated} "
            f"skip={self.types_skipped} deleted={self.types_deleted} err={self.types_errors}) "
            f"journey_types(created={self.journey_types_created} skip={self.journey_types_skipped} "
            f"err={self.journey_types_errors}) "
            f"skills(upserted={self.skills_upserted} skip={self.skills_skipped} err={self.skills_errors}) "
            f"deploy_slots(set={self.deploy_slots_set} skip={self.deploy_slots_skipped} "
            f"err={self.deploy_slots_errors})"
            + (f" hook_violations={self.hook_violations}" if self.hook_violations else "")
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
        # G-PROBE platform-wide: credencial de serviço p/ as mutações gateadas do
        # agent-registry (pools/skills/channel-endpoints/slots). Omitida se não configurada.
        _svc = os.environ.get("AGENT_REGISTRY_SERVICE_TOKEN", "")
        if _svc:
            headers["x-service-token"] = _svc

        # ── Sync skills FIRST (agent types reference skill_ids) ───────────
        await self._sync_skills(http, headers, report)

        # ── Teardown-hook safety guard (read-only; loud ERROR on violation) ──
        self._validate_teardown_hooks(cfg, report)

        # ── Sync journey types (pools reference them) ──────────────────
        for jt in cfg.get("journey_types", []):
            await self._sync_journey_type(http, headers, jt, report)

        # ── Sync pools (agent types reference them) ────────────────────
        for pool in cfg.get("pools", []):
            await self._sync_pool(http, headers, pool, report)

        # ── Sync channel endpoints (referenciam pools) — config-consolidation F1.1b ──
        # Migrados do antigo infra/seed/seed.py (aposentado). Fonte única = YAML.
        await self._sync_channel_endpoints(http, headers, cfg, report)

        # ── Guard de inventário de webhook (read-only, fail-open) ──────────────
        # ADR adr-webhook-endpoint-single-registry: pool webhook está acionável AGORA;
        # sem linha declarada ele é endereço vivo e invisível. Roda DEPOIS do sync
        # para que o ERROR descreva o que ficou de fora do que acabou de ser aplicado.
        self._validate_webhook_endpoints(cfg, report)

        # AgentType entity retired (Fase 3d/C): no agent_types are synced or
        # pruned. AI provisioning comes from each pool's `deploy:` block (below);
        # human agents are login-driven (no registry agent_type).

        # ── Deploy slots (PoolSkillSlot.current) sourced from each pool's
        # `deploy:` block (skill_id + max_concurrent_sessions). Canonical
        # provisioning source after agent_types retirement (Fase 3c/A):
        # the pool owns its deploy, the bootstrap sources instances from the
        # slot, and the bridge synthesizes a native agent_type from the skill.
        # Idempotent — always runs.
        await self._sync_deploy_slots_from_pools(http, headers, cfg.get("pools", []), report)

        return report

    # ── Teardown-hook safety guard ──────────────────────────────────────────────

    def _load_skill_steps(self) -> dict[str, list]:
        """skill_id → flow.steps, read from SKILLS_DIR (for hook-safety validation)."""
        out: dict[str, list] = {}
        if not self._skills_dir:
            return out
        skills_path = Path(self._skills_dir)
        if not skills_path.exists():
            return out
        for yaml_file in sorted(skills_path.glob("*.yaml")):
            try:
                raw = yaml.safe_load(yaml_file.read_text())
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            sid   = raw.get("id", "")
            steps = raw.get("steps")
            if sid and isinstance(steps, list):
                out[str(sid)] = steps
        return out

    def _validate_teardown_hooks(self, cfg: dict, report: SyncReport) -> None:
        """
        Guard: a skill deployed to a pool that is the TARGET of a teardown hook
        (on_contact_end/on_human_end/post_human) must NOT contain a suspending step
        (delegate/suspend/collect) — the bridge would close the contact before the
        I/O renders. Logs a loud config ERROR naming the offending wiring. Read-only
        and fail-open (does not block startup, consistent with the syncer's policy;
        the cross-reference is the correct signal, not the skill's own profile).

        ⚠️ SÓ ENTRADA QUE FICA NA CONFERÊNCIA É CHECADA (corrigido 2026-08-10). Este
        guard é anterior ao arco de detach e cruzava apenas
        `hook → pool → skill → steps`, sem olhar como a entrada é DESPACHADA. Sua
        premissa — *"o bridge fecha o contato antes de o I/O renderizar"* — só vale para
        o hook que roda como especialista dentro da conferência; o que vira workflow
        fire-and-forget sai da janela segurada e pode suspender à vontade. A condição
        está em `_runs_as_workflow`, espelho de `_is_workflow_dispatch` (main.py), e o
        resultado prático é que **só `side=customer` + `inline` (o NPS) é checado**.

        Sem isso, o guard logava ERROR a cada boot sobre
        `retencao_humano.on_human_end → wrapup_detached_ia`, configuração correta e
        validada ponta a ponta. *Um portão que reprova o que funciona ensina a ignorar o
        vermelho* — mesmo critério que rebaixou a validação de canal a aviso na D8.

        Typo em `dispatch` (ex.: `detatched`) cai no default `inline`; com `side=agent`
        isso ainda é workflow e fica isento — igual ao bridge, que aplica o mesmo
        default. Guard e executor concordarem importa mais aqui do que pegar o typo:
        divergir reintroduziria o falso positivo que este conserto remove.
        """
        pools = cfg.get("pools", []) or []

        # pool_id → deployed skill_id (deploy.skill_id, fallback skill_id/webhook_skill_id).
        pool_skill: dict[str, str] = {}
        for p in pools:
            pid = p.get("pool_id")
            if not pid:
                continue
            sid = (p.get("deploy") or {}).get("skill_id") or p.get("skill_id") or p.get("webhook_skill_id")
            if sid:
                pool_skill[str(pid)] = str(sid)

        skill_steps = self._load_skill_steps()

        # Testemunha da isenção. Sem ela, um dia em que TODA entrada de teardown vire
        # `detached` deixaria este guard sem nada para checar — e "0 violações" sobre
        # 0 entradas checadas é o mesmo verde-sem-amostra que o F4c do probe de webhook
        # já produziu uma vez. Aqui não reprova; apenas se declara em vez de calar.
        checked_detached = 0
        checked_inline   = 0

        for p in pools:
            hooking_pool = p.get("pool_id", "?")
            hooks = p.get("hooks") or {}
            if not isinstance(hooks, dict):
                continue
            for hook_key in _TEARDOWN_HOOK_KEYS:
                for entry in hooks.get(hook_key, []) or []:
                    target_pool = entry.get("pool") if isinstance(entry, dict) else None
                    if not target_pool:
                        continue
                    # Espelha `_is_workflow_dispatch` de `fire_pool_hooks` (main.py).
                    # Entrada que roda como WORKFLOW sai da conferência e pode suspender
                    # à vontade — a premissa do guard não se aplica a ela.
                    if isinstance(entry, dict) and _runs_as_workflow(entry):
                        checked_detached += 1
                        continue
                    target_skill = pool_skill.get(str(target_pool))
                    if not target_skill:
                        continue  # target pool not declared in this YAML → skip (lenient)
                    steps = skill_steps.get(target_skill)
                    if not isinstance(steps, list):
                        continue  # skill flow unavailable (YAML fallback / no id) → skip
                    checked_inline += 1
                    bad = [
                        (s.get("id", "?"), s.get("type"))
                        for s in steps
                        if isinstance(s, dict) and s.get("type") in _SUSPENDING_STEP_TYPES
                    ]
                    if bad:
                        report.hook_violations += 1
                        detail = ", ".join(f"{sid}:{stype}" for sid, stype in bad)
                        logger.error(
                            "RegistrySyncer: CONFIG ERROR — pool '%s' declares hook '%s' → pool "
                            "'%s' (skill '%s'), but that skill has SUSPENDING step(s) [%s]. Teardown "
                            "hooks cannot suspend (delegate/suspend/collect): the bridge closes the "
                            "contact before the I/O renders. Do the I/O INLINE (form_get + menu). "
                            "See docs/product/dialog-primitive-and-runner-design.md.",
                            hooking_pool, hook_key, target_pool, target_skill, detail,
                        )

        if checked_inline == 0:
            logger.info(
                "  teardown-hook guard: NÃO CHECOU nenhuma entrada inline "
                "(%d detached isenta(s)). Ausência de violação aqui é ausência de "
                "amostra, não aprovação.", checked_detached,
            )
        else:
            logger.debug(
                "  teardown-hook guard: %d entrada(s) inline checada(s), %d detached isenta(s)",
                checked_inline, checked_detached,
            )

    # ── Deploy-slot sync (Fase 3c) ──────────────────────────────────────────────

    async def _sync_deploy_slots_from_pools(
        self,
        http:    aiohttp.ClientSession,
        headers: dict,
        pools:   list[dict],
        report:  SyncReport,
    ) -> None:
        """
        Fase 3c/A — ensure each pool with a `deploy:` block has a deploy slot
        `current` matching the declared skill_id + max_concurrent_sessions.
        Canonical provisioning source after agent_types retirement: the pool
        owns its deploy; the bootstrap sources instances from the slot and the
        bridge synthesizes a native agent_type from the skill on activation.

        A pool without a `deploy:` block is skipped (human/login-driven pools,
        or pools provisioned via the Config UI). Idempotent: _ensure_deploy_slot
        only sets+promotes when the current slot does not already match.
        """
        for pool in pools:
            deploy = pool.get("deploy")
            if not isinstance(deploy, dict):
                continue
            pool_id  = pool.get("pool_id", "")
            skill_id = deploy.get("skill_id", "")
            if not pool_id or not skill_id:
                continue
            try:
                max_concurrent = int(deploy.get("max_concurrent_sessions") or 1)
            except (TypeError, ValueError):
                max_concurrent = 1
            max_concurrent = max(1, max_concurrent)
            await self._ensure_deploy_slot(
                http, headers, pool_id, skill_id, max_concurrent, report
            )

    async def _ensure_deploy_slot(
        self,
        http:           aiohttp.ClientSession,
        headers:        dict,
        pool_id:        str,
        skill_id:       str,
        max_concurrent: int,
        report:         SyncReport,
    ) -> None:
        slots_url = f"{self._registry_url}/v1/pools/{pool_id}/slots"
        # Config do slot que já está em produção. O YAML declara APENAS
        # `max_concurrent_sessions`; todo o resto do config_json (ex.: `channel_policy`,
        # que é regra de negócio do tenant e só existe na UI) é DB-owned. Ao re-semear um
        # slot inexecutável preservamos essa config — senão a cura do snapshot cobraria o
        # preço de apagar o deploy-config do operador.
        existing_cfg: dict = {}
        try:
            # Idempotency + seed-if-absent precedence:
            #   - current matches the YAML (e é executável) → skip;
            #   - current is SET, executável e differs → it was edited in the UI (capacity);
            #     under seed-if-absent (default) the slot is DB-owned → skip (do
            #     NOT overwrite). REGISTRY_SYNC_RECONCILE=true re-applies the YAML.
            #   - current is UNSET, ou SET com snapshot nulo (quebrado) → seed from YAML.
            async with http.get(
                slots_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    current = (body.get("slots") or {}).get("current") or {}
                    cfg = current.get("config_json") or {}
                    if isinstance(cfg, dict):
                        existing_cfg = dict(cfg)
                    # `yaml_snapshot` faz parte do critério de "em dia": um slot com
                    # snapshot nulo está SET e bate skill+capacidade, mas é inexecutável
                    # (o bridge roda o snapshot, não o skill_id). Sem esta condição, um
                    # slot quebrado é dado como bom para sempre e nunca se auto-cura.
                    _snapshot = current.get("yaml_snapshot")
                    _runnable = isinstance(_snapshot, dict) and bool(_snapshot)
                    _matches = (
                        current.get("set")
                        and _runnable
                        and current.get("skill_id") == skill_id
                        and int(cfg.get("max_concurrent_sessions") or 0) == max_concurrent
                    )
                    if _matches:
                        report.deploy_slots_skipped += 1
                        return
                    if current.get("set") and _runnable and not _reconcile_enabled():
                        logger.debug(
                            "  deploy slot pool=%s set & differs — DB-owned "
                            "(seed-if-absent), skipping reconcile", pool_id,
                        )
                        report.deploy_slots_skipped += 1
                        return

            # Configure the "next" slot then promote it to "current".
            next_url = f"{slots_url}/next"
            payload = {
                "skill_id":    skill_id,
                # Preserva a config DB-owned (channel_policy & cia) e só (re)afirma o
                # que o YAML de fato declara.
                "config_json": {**existing_cfg, "max_concurrent_sessions": max_concurrent},
            }
            async with http.put(
                next_url, headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status not in (200, 201):
                    logger.warning(
                        "RegistrySyncer: deploy slot PUT failed pool=%s skill=%s HTTP %d",
                        pool_id, skill_id, resp.status,
                    )
                    report.deploy_slots_errors += 1
                    return

            async with http.post(
                f"{slots_url.rsplit('/slots', 1)[0]}/promote",
                headers=headers, timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    report.deploy_slots_set += 1
                    logger.info(
                        "RegistrySyncer: deploy slot promoted pool=%s skill=%s concurrent=%d",
                        pool_id, skill_id, max_concurrent,
                    )
                else:
                    logger.warning(
                        "RegistrySyncer: deploy slot promote failed pool=%s HTTP %d",
                        pool_id, resp.status,
                    )
                    report.deploy_slots_errors += 1
        except Exception as exc:
            logger.warning(
                "RegistrySyncer: could not sync deploy slot pool=%s skill=%s — %s",
                pool_id, skill_id, exc,
            )
            report.deploy_slots_errors += 1

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

            # mention_commands rides INSIDE the flow JSON so it round-trips
            # through agent-registry: the Skill model has no dedicated column,
            # so a top-level payload field would be silently dropped. Deploy-
            # driven synthesis (_synthesize_agent_type_from_skill) and mention
            # routing read it back from get_skill_flow(). The `flow` column is
            # stored as opaque JSON (no strict schema), so the extra key is safe.
            if raw.get("mention_commands"):
                flow["mention_commands"] = raw["mention_commands"]

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

            # delegation_input — typed fields shown in DelegarTarefaDrawer
            if raw.get("delegation_input"):
                payload["delegation_input"] = raw["delegation_input"]

            # config_params — deploy-time params rendered in the Flow › Deploy form
            # (→ PoolSkillSlot.config_json → $.config.*). Declared at the skill top-level.
            if raw.get("config_params"):
                payload["config_params"] = raw["config_params"]

            # agent_role — propósito do agente (executor|orchestrator|evaluator).
            # Só entra no payload quando o YAML DECLARA: a rota preserva o valor do
            # DB quando a chave está ausente (provisioning precedence — uma edição de
            # UI não pode ser revertida a "executor" por um PUT que nem cita o campo).
            if raw.get("agent_role"):
                payload["agent_role"] = raw["agent_role"]

            await self._upsert_skill(http, headers, skill_id, payload, report)

    async def _upsert_skill(
        self,
        http:     aiohttp.ClientSession,
        headers:  dict,
        skill_id: str,
        payload:  dict,
        report:   SyncReport,
    ) -> None:
        """
        SEED-IF-ABSENT (2026-07-13) — o YAML apenas SEMEIA um skill inexistente;
        uma vez criado, o **DB é a fonte de verdade** e o syncer NÃO sobrescreve.
        Mesma regra que os pools já seguem (provisioning precedence).

        **Por que mudou (bug de perda de dados).** Antes isto era um upsert
        incondicional com `x-skill-publish: true`, e esse header faz o agent-registry
        gravar `{ flow: <yaml>, flow_draft: DbNull }`. Ou seja: **a cada boot do
        bridge, o YAML sobrescrevia a produção E APAGAVA o rascunho do editor.**
        Qualquer edição feita na UI era destruída silenciosamente no próximo restart
        — o editor não era só inócuo, ele perdia trabalho.

        `REGISTRY_SYNC_RECONCILE=true` restaura o comportamento legado (o YAML vence)
        para dev/GitOps/CI, igual aos pools.
        """
        url = f"{self._registry_url}/v1/skills/{skill_id}"

        # Seed-if-absent: existe no DB **com definição** e não estamos em reconcile → não toca.
        #
        # "Existe" ≠ "está semeado". Uma linha pode existir com `flow` NULO (skill criado
        # por um caminho que não gravava produção, ou PUT que falhou depois de escrever a
        # metadata). Pular essa linha torna o buraco PERMANENTE: o syncer nunca mais a
        # preenche, o set-next congela `yaml_snapshot: null`, e o bridge reporta "pool sem
        # slot" para um pool que TEM slot. O critério é a presença da DEFINIÇÃO, não da linha.
        if not _reconcile_enabled():
            try:
                async with http.get(url, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=10)) as probe:
                    if probe.status == 200:
                        body = await _safe_json(probe) or {}
                        if isinstance(body.get("flow"), dict) and body["flow"]:
                            # DRIFT: o arquivo foi editado, mas o DB é a verdade — a edição
                            # NÃO vai para lugar nenhum. Sem este aviso o pulo é SILENCIOSO,
                            # e "editei o YAML e reiniciei o bridge" parece ter funcionado
                            # (custou um diagnóstico errado no S2: suspeitei do Zod antes de
                            # suspeitar do syncer). O seed-if-absent continua certo — o que
                            # estava errado era ele ser mudo.
                            if _flow_differs(body.get("flow"), payload.get("flow")):
                                logger.warning(
                                    "  skill %s: o YAML DIFERE da definição no DB e NÃO será "
                                    "aplicado — o DB é a fonte de verdade (seed-if-absent). "
                                    "Para o YAML vencer, suba com REGISTRY_SYNC_RECONCILE=true.",
                                    skill_id,
                                )
                            else:
                                logger.debug(
                                    "  skill %s exists — DB-owned (seed-if-absent), skipping",
                                    skill_id,
                                )
                            report.skills_skipped += 1
                            return
                        logger.warning(
                            "  skill %s existe mas SEM definição (flow nulo) — re-semeando do YAML. "
                            "Um skill sem flow não pode ser deployado: o slot congelaria um "
                            "snapshot nulo.", skill_id,
                        )
            except Exception as exc:   # probe falhou → segue para o PUT (seed)
                logger.debug("  skill %s: existence probe failed (%s) — seeding", skill_id, exc)

        # Cria (seed) ou, em reconcile, re-aplica o YAML por cima.
        # `x-skill-publish: true` publica direto em produção: o skill semeado precisa
        # rodar, e no seed não há rascunho de ninguém para preservar.
        publish_headers = {**headers, "x-skill-publish": "true"}
        try:
            async with http.put(url, headers=publish_headers, json=payload,
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

    # ── Journey type sync ─────────────────────────────────────────────────────

    async def _sync_journey_type(
        self,
        http:    aiohttp.ClientSession,
        headers: dict,
        jt:      dict,
        report:  SyncReport,
    ) -> None:
        """
        Upsert a journey type in the Agent Registry.
          POST /v1/journey-types  → 201  created
          POST /v1/journey-types  → 409  exists → PATCH to apply any drift
        """
        jtid = jt.get("journey_type_id", "<unknown>")
        url  = f"{self._registry_url}/v1/journey-types"

        try:
            async with http.post(url, headers=headers, json=jt,
                                 timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 201:
                    logger.info("  journey_type %s created", jtid)
                    report.journey_types_created += 1
                    return
                elif resp.status != 409:
                    body = await _safe_json(resp)
                    msg  = f"journey_type {jtid}: POST returned {resp.status} — {body}"
                    logger.error("  %s", msg)
                    report.journey_types_errors += 1
                    report.errors.append(msg)
                    return
        except Exception as exc:
            msg = f"journey_type {jtid}: POST exception — {exc}"
            logger.error("  %s", msg)
            report.journey_types_errors += 1
            report.errors.append(msg)
            return

        # 409 → already exists, PATCH mutable fields (sla_ms, description)
        patch_body = {k: v for k, v in jt.items() if k != "journey_type_id"}
        patch_url  = f"{self._registry_url}/v1/journey-types/{jtid}"
        try:
            async with http.patch(patch_url, headers=headers, json=patch_body,
                                  timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status in (200, 204):
                    logger.debug("  journey_type %s already exists (no drift)", jtid)
                else:
                    body = await _safe_json(resp)
                    logger.warning("  journey_type %s: PATCH returned %s — %s", jtid, resp.status, body)
                report.journey_types_skipped += 1
        except Exception as exc:
            logger.warning("  journey_type %s: PATCH exception — %s", jtid, exc)
            report.journey_types_skipped += 1  # non-fatal — type exists, just couldn't update

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

        # 409 → already exists. Provisioning precedence (seed-if-absent / DB-owned):
        #   default: the pool is DB-owned — the syncer does NOT overwrite existing
        #     pool config on restart. This is what stops escalation_pools /
        #     mentionable_pools / hooks from being clobbered to the YAML values on
        #     every rebuild (config edited in the UI survives).
        #   REGISTRY_SYNC_RECONCILE=true: legacy — PUT the YAML over the existing
        #     row to re-apply config drift (dev/GitOps).
        if not _reconcile_enabled():
            logger.debug(
                "  pool %s exists — DB-owned (seed-if-absent), skipping reconcile", pid,
            )
            report.pools_skipped += 1
            return

        # reconcile mode: PUT the YAML config over the existing pool (drift)
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

    # ── Channel endpoint sync (config-consolidation F1.1b) ────────────────────

    async def _sync_channel_endpoints(
        self,
        http:    aiohttp.ClientSession,
        headers: dict,
        cfg:     dict,
        report:  SyncReport,
    ) -> None:
        """
        Registra channel endpoints (identificador de canal → pool) via
        POST /v1/channel-endpoints. Idempotente: 409 = já existe (skip).
        Fonte única: o YAML (migrado do seed.py aposentado). Campo `display_name`
        é o exigido pela rota (o seed antigo mandava `label` — POST falhava 400).

        `origin` (ADR adr-webhook-endpoint-single-registry, D6) é repassado como
        declarado; ausente ⇒ a rota aplica `external`.

        ⚠️ O 409 aqui é seed-if-absent, e isso tem CONSEQUÊNCIA que morde: editar
        uma entrada JÁ SEMEADA neste YAML é NO-OP silencioso — o POST devolve 409 e
        o syncer segue. Entrada NOVA aplica normalmente. Para o arquivo vencer sobre
        uma linha existente: apagar a linha no store, ou subir com
        REGISTRY_SYNC_RECONCILE (que hoje não cobre channel_endpoints).
        """
        url = f"{self._registry_url}/v1/channel-endpoints"
        for ep in cfg.get("channel_endpoints", []):
            cid = f"{ep.get('channel', '?')}/{ep.get('identifier', '?')}"
            body = {
                "channel":      ep.get("channel"),
                "identifier":   ep.get("identifier"),
                "pool_id":      ep.get("pool_id"),
                "display_name": ep.get("display_name") or ep.get("identifier"),
                "active":       ep.get("active", True),
                "origin":       ep.get("origin", "external"),
            }
            # ── Fatia 4 (ADR §7.10) — webhook `external` exige decisão EXPLÍCITA ──
            #
            # Repassado SÓ quando declarado: omitir aqui é o que faz o 422 do route
            # acontecer, e o 422 é o ponto. Se este syncer inventasse um default, o
            # portão que existe para impedir "endpoint externo nasce anônimo por
            # esquecimento" seria contornado justamente pelo caminho automático.
            #
            # ⚠️ E ele NUNCA pode declarar `true`: este chamador **descarta o corpo da
            # resposta**, onde o token em claro vive uma única vez. Um `true` aqui
            # criaria endpoint exigindo credencial que ninguém recebeu — 401 permanente,
            # e só em INSTALAÇÃO LIMPA (com a linha já semeada o POST leva 409 e nada
            # acontece), que é a classe de defeito que dorme até o dia do `--wipe`.
            # Endpoint externo que precise de token nasce pela UI, que mostra o segredo.
            if "auth_required" in ep:
                if ep.get("auth_required"):
                    logger.error(
                        "  channel_endpoint %s: auth_required=true no YAML é INVÁLIDO e foi "
                        "REBAIXADO para false — este syncer descarta o corpo do 201, onde o "
                        "token em claro vive uma única vez, então o endpoint nasceria exigindo "
                        "credencial que ninguém recebeu (401 permanente, só em instalação "
                        "limpa). Declare false aqui e gere o token pela UI, que mostra o "
                        "segredo. Ver ADR §7.10.", cid,
                    )
                body["auth_required"] = False

            try:
                async with http.post(url, headers=headers, json=body,
                                     timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 201:
                        logger.info("  channel_endpoint %s → %s created", cid, ep.get("pool_id"))
                    elif resp.status == 409:
                        logger.debug("  channel_endpoint %s already exists", cid)
                    else:
                        b = await _safe_json(resp)
                        logger.warning("  channel_endpoint %s: POST %d — %s", cid, resp.status, b)
            except Exception as exc:
                logger.warning("  channel_endpoint %s: POST exception — %s", cid, exc)

    # ── Guard: pool webhook sem endereço declarado ────────────────────────────

    def _validate_webhook_endpoints(self, cfg: dict, report: SyncReport) -> None:
        """
        ADR adr-webhook-endpoint-single-registry (D1/D8): todo pool que declara o canal
        `webhook` é acionável AGORA, e por isso precisa de uma linha `ChannelEndpoint`
        declarada — sem ela o tenant não tem resposta para *"quais URLs disparam alguma
        coisa aqui?"*, que é o defeito que o ADR fecha (não é colisão: `@@unique` já a
        bloqueia, e os prefixos de URL são disjuntos).

        POR QUE ISTO É UM CHECADOR E NÃO UM ESCRITOR — é a decisão da Fase B, não
        economia de código. Seria trivial DERIVAR a linha de `pool.webhook_skill_id` e
        POSTá-la. Duas razões para não:

          1. Derivar INVERTE a D5, que retira de `webhook_skill_id` o papel de endereço.
             Copiá-lo para o registro a cada boot mantém a autoridade nele e rebaixa o
             `ChannelEndpoint` a projeção — o contrário exato da D1.
          2. Derivar torna este guard um TESTE QUE NÃO PODE REPROVAR: a linha sempre
             concordaria com o pool porque teria sido copiada dele. Verde comprado sem
             nada em troca (§ Postura). Declarada no YAML, a reprovação tem causa real e
             cotidiana: pool webhook novo entra sem a entrada correspondente.

        Fail-open e barulhento, no padrão de `_validate_teardown_hooks`: a checagem
        cruzada é o sinal correto, não bloquear o boot. Read-only por construção.
        """
        declared: set[tuple[str, str]] = {
            (str(ep.get("channel", "")), str(ep.get("identifier", "")))
            for ep in cfg.get("channel_endpoints", []) or []
        }

        for p in cfg.get("pools", []) or []:
            if "webhook" not in (p.get("channel_types") or []):
                continue
            pid  = str(p.get("pool_id", "?"))
            wsid = p.get("webhook_skill_id")
            if not wsid:
                logger.error(
                    "  CONFIG ERROR: pool %s declara canal 'webhook' SEM webhook_skill_id "
                    "— acionável por /v1/channels/webhook/pool/%s e por endereço nenhum",
                    pid, pid,
                )
                report.errors.append(f"pool {pid}: webhook sem webhook_skill_id")
                continue
            if ("webhook", str(wsid)) not in declared:
                logger.error(
                    "  CONFIG ERROR: pool %s é acionável em /v1/channels/webhook/%s mas NÃO "
                    "tem linha declarada em channel_endpoints — endereço vivo e invisível. "
                    "Declare-a no YAML (ADR webhook-endpoint-single-registry, Fase B); "
                    "NÃO derive do pool (o guard passaria a nunca poder reprovar).",
                    pid, wsid,
                )
                report.errors.append(f"pool {pid}: webhook '{wsid}' sem channel_endpoint declarado")

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
