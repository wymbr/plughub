#!/usr/bin/env python3
"""
seed.py — APOSENTADO (config-consolidation F1.1b, 2026-06-11).

Este seed provisionava pools, agent_types, channel_endpoints e escrevia Redis
direto — violando os invariantes "Configuration — Single Source" do CLAUDE.md
(fonte única por domínio; provisão só via API; sem escrita direta em Redis).

Onde cada coisa vive agora (fonte única = agent-registry, via RegistrySyncer no
orchestrator-bridge, lendo infra/registry/tenant_demo.yaml):
  - pools             → `pools:` no YAML            (RegistrySyncer._sync_pool)
  - channel_endpoints → `channel_endpoints:` no YAML (RegistrySyncer._sync_channel_endpoints)
  - agent_types       → entidade REMOVIDA (Fase 3 C2/C3/C4); IA vem do `deploy:` do pool
  - Redis (pool_config:{id}, {tenant}:pools) → escrito pelo routing-engine a partir
                        de registry.changed (kafka_listener → save_pool_config)

O serviço `demo-seed` foi removido do docker-compose.demo.yml. Este arquivo pode
ser apagado (`git rm infra/seed/seed.py`); mantido só como nota de migração.
"""
import sys

if __name__ == "__main__":
    print(
        "seed.py APOSENTADO (config-consolidation F1.1b). Provisionamento agora é "
        "via RegistrySyncer a partir de infra/registry/tenant_demo.yaml. Nada a fazer."
    )
    sys.exit(0)
