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
    # Burn-down COMPLETO (Fase 1 da config-consolidation) — allowlist vazio:
    #   seed_redis_write          — RESOLVIDO F1.1a: seed.py não escreve mais Redis.
    #   env_dup_instance_ttl      — RESOLVIDO F1.2:  env removido (instance_ttl = default spec).
    #   env_dup_attachment_expiry — RESOLVIDO F1.2:  channel-gateway lê do config-api.
    #   pools_double_source       — RESOLVIDO F1.1b: seed.py aposentado; pools só no YAML/RegistrySyncer.
    # Qualquer violação detectada daqui pra frente é NOVA → o guard falha (exit 1).
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
    # F2-TTL: config-api tem webchat.auth_timeout_s; o env não pode reintroduzir o dup.
    if _env_assigned("PLUGHUB_WS_AUTH_TIMEOUT_S"):
        found["env_dup_ws_auth_timeout"] = "docker-compose.demo.yml"

    # 4. config-http-propagation arc: nenhum serviço FORA do config-api pode ler a
    #    cache Redis do config-api (`plughub:cfg:...`) diretamente. Config de negócio
    #    é consumida via a API HTTP do config-api (+ cache in-process + config.changed),
    #    como SessionConfigCache / RoutingConfigCache. Leitura direta = padrão furado
    #    (a chave é TTL/transitória; o valor nem sempre está lá). Secrets em
    #    `{tenant}:config:...` são OUTRO mecanismo (env-first) e NÃO são flagados aqui.
    _read_call = re.compile(r"\.(get|mget|hget|hgetall)\s*\(")
    offenders: list[str] = []
    for sub in ("py", "ts"):
        for p in (ROOT / "packages").glob(f"*/src/**/*.{sub}"):
            sp = str(p).replace("\\", "/")
            if "/config-api/" in sp:
                continue
            if "test" in p.name.lower() or "__tests__" in sp or "/tests/" in sp:
                continue
            try:
                txt = p.read_text(encoding="utf-8")
            except Exception:
                continue
            for ln in txt.splitlines():
                s = ln.lstrip()
                if s.startswith(("#", "//", "*", "/*")):
                    continue
                if "plughub:cfg:" in ln and _read_call.search(ln):
                    offenders.append(str(p.relative_to(ROOT)).replace("\\", "/"))
                    break
    if offenders:
        found["config_cache_direct_read"] = (
            f"{len(offenders)} arquivo(s) leem plughub:cfg:* direto: "
            + ", ".join(sorted(offenders)[:5])
        )

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
