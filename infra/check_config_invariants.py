#!/usr/bin/env python3
"""
check_config_invariants.py — guard-rail da Config Consolidation (F0.2).

Verifica os invariantes "Configuration — Single Source" (CLAUDE.md, bloco temporário)
contra violações conhecidas. Burn-down: o allowlist `KNOWN` lista o que já viola hoje;
o guard **falha (exit 1) se aparecer uma violação NOVA** (fora do allowlist) e **avisa**
quando uma violação conhecida some (foi corrigida → remover do allowlist).

Roda na RAIZ do repo (precisa de infra/ + packages/ + compose juntos). Sem dependências
(stdlib only). Três formas:
    python3 infra/check_config_invariants.py            # se o host tiver python3
    docker run --rm -v "$PWD":/repo -w /repo python:3.11-slim python infra/check_config_invariants.py
    # ^ recomendado quando não há python no host (não precisa instalar nada)

Plano: docs/arcos/config-consolidation.md §8.
Escopo/itens: TODO.md § "Config Consolidation".
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Violações conhecidas (allowlist de burn-down). Remover a entrada quando o item
# correspondente do escopo (F1.x) for concluído — o guard avisa quando isso ocorrer.
KNOWN: dict[str, str] = {
    # seed_redis_write          — RESOLVIDO em F1.1a (2026-06-11): seed.py não escreve mais Redis.
    # env_dup_instance_ttl      — RESOLVIDO em F1.2 (2026-06-11): env removido (instance_ttl = default spec).
    # env_dup_attachment_expiry — RESOLVIDO em F1.2 (2026-06-11): channel-gateway lê do config-api.
    "pools_double_source":       "pools definidos em tenant_demo.yaml E seed.py (F1.1b)",
}


def _read(rel: str) -> str:
    p = ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def detect() -> dict[str, str]:
    """Retorna {violation_id: evidência} para cada violação detectada hoje."""
    found: dict[str, str] = {}
    seed = _read("infra/seed/seed.py")

    # 1. Provisioning only via API: o seed do demo NÃO pode escrever Redis direto.
    if re.search(r"def\s+seed_redis\s*\(", seed) or re.search(r'_send\(\s*"(SET|SADD|HSET)"', seed):
        found["seed_redis_write"] = "infra/seed/seed.py"

    # 2. One source per domain: pools não podem ser definidos em duas fontes.
    yaml_pools = set(re.findall(r"^\s*-\s*pool_id:\s*([a-z0-9_]+)",
                                _read("infra/registry/tenant_demo.yaml"), re.M))
    seed_pools = set(re.findall(r'"pool_id":\s*"([a-z0-9_]+)"', seed))
    dup = yaml_pools & seed_pools
    if dup:
        found["pools_double_source"] = f"{len(dup)} pools em ambos: {', '.join(sorted(dup)[:5])}…"

    # 3. env only for secrets/wiring: chaves de config de negócio não podem viver
    #    em env duplicando o config-api. Detecta ASSIGNMENT ativo (não comentário):
    #    `^\s*NAME:\s*<valor>` — uma linha começando com `#` não casa.
    compose = _read("docker-compose.demo.yml")

    def _env_assigned(name: str) -> bool:
        return re.search(rf"^\s*{re.escape(name)}:\s*\S", compose, re.M) is not None

    if _env_assigned("PLUGHUB_INSTANCE_TTL_SECONDS"):
        found["env_dup_instance_ttl"] = "docker-compose.demo.yml"
    if _env_assigned("PLUGHUB_ATTACHMENT_EXPIRY_DAYS"):
        found["env_dup_attachment_expiry"] = "docker-compose.demo.yml"

    return found


def main() -> int:
    found = detect()
    new   = {k: v for k, v in found.items() if k not in KNOWN}
    fixed = [k for k in KNOWN if k not in found]

    print("== Config Consolidation — guard-rail (F0.2) ==")
    print(f"detectadas: {len(found)} | conhecidas: {len(KNOWN)} | NOVAS: {len(new)} | corrigidas: {len(fixed)}\n")

    for k, v in sorted(found.items()):
        tag = "NOVA ❌" if k in new else "conhecida"
        print(f"  [{tag:>9}] {k}: {v}")
    for k in fixed:
        print(f"  [CORRIGIDA ✅] {k}: {KNOWN[k]} — REMOVER do allowlist KNOWN")

    if new:
        print("\nFALHA: violação(ões) NOVA(s) dos invariantes de config (CLAUDE.md § Configuration).")
        print("Todo config novo deve: fonte única por domínio · provisão só via API · env só secret/wiring.")
        return 1

    print("\nOK: nenhuma violação nova.")
    if fixed:
        print("Aviso: há violações corrigidas ainda no allowlist — remova-as de KNOWN (burn-down).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
