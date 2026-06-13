#!/usr/bin/env python3
"""
g7_nps_to_on_contact_end.py — migração G7 Fase 3b (cutover, 2026-06-13).

Move entries `side: customer` de `hooks.on_human_end` → `hooks.on_contact_end`
em TODOS os pools do agent-registry de um tenant, via API oficial (respeita
"provisioning only via official API"; NÃO escreve no DB direto).

Por quê: o NPS deixou de ser hook de fim-de-SEGMENTO (on_human_end) e passou a
ser hook de fim-de-CONTATO de 1ª classe (on_contact_end). O reseed do YAML
(RegistrySyncer) cobre os pools versionados em infra/registry/*.yaml; este script
cobre os pools criados via UI que vivem só no DB (ex.: humanoxxx).

Idempotente: pools sem entry side=customer em on_human_end são pulados. Entries
já presentes em on_contact_end (mesmo pool) não são duplicadas.

Uso (o teto WSL roda; o sandbox não alcança o agent-registry):
    # dry-run (default) — só mostra o que faria:
    python3 infra/migrations/g7_nps_to_on_contact_end.py
    # aplicar de fato:
    python3 infra/migrations/g7_nps_to_on_contact_end.py --apply

Env:
    AGENT_REGISTRY_URL   default http://localhost:3300
    TENANT_ID            default tenant_demo
    ADMIN_TOKEN          opcional → header X-Admin-Token (se a rota exigir)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

REGISTRY = os.getenv("AGENT_REGISTRY_URL", "http://localhost:3300").rstrip("/")
TENANT   = os.getenv("TENANT_ID", "tenant_demo")
TOKEN    = os.getenv("ADMIN_TOKEN", "")
APPLY    = "--apply" in sys.argv[1:]


def _headers() -> dict[str, str]:
    h = {"x-tenant-id": TENANT, "content-type": "application/json"}
    if TOKEN:
        h["x-admin-token"] = TOKEN
    return h


def _req(method: str, path: str, body: dict | None = None) -> dict:
    url  = f"{REGISTRY}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req  = urllib.request.Request(url, data=data, method=method, headers=_headers())
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


def _is_customer(entry: object) -> bool:
    return isinstance(entry, dict) and (entry.get("side", "agent") == "customer")


def main() -> int:
    print(f"G7 NPS→on_contact_end | registry={REGISTRY} tenant={TENANT} "
          f"mode={'APPLY' if APPLY else 'DRY-RUN'}")
    try:
        listing = _req("GET", "/v1/pools/?status=active")
    except urllib.error.URLError as exc:
        print(f"ERRO: não consegui listar pools em {REGISTRY}: {exc}", file=sys.stderr)
        return 2

    pools = listing.get("pools", []) if isinstance(listing, dict) else []
    migrated = skipped = failed = 0

    for p in pools:
        pool_id = p.get("pool_id")
        if not pool_id:
            continue
        # Lê o pool autoritativo (hooks completos) por id.
        try:
            full  = _req("GET", f"/v1/pools/{pool_id}")
        except urllib.error.URLError as exc:
            print(f"  ! {pool_id}: GET falhou ({exc}) — pulado")
            failed += 1
            continue

        hooks = (full.get("hooks") or {}) if isinstance(full, dict) else {}
        ohe   = list(hooks.get("on_human_end") or [])
        oce   = list(hooks.get("on_contact_end") or [])

        customer_entries = [e for e in ohe if _is_customer(e)]
        if not customer_entries:
            skipped += 1
            continue

        new_ohe = [e for e in ohe if not _is_customer(e)]
        existing_pools = {e.get("pool") for e in oce if isinstance(e, dict)}
        added = [e for e in customer_entries if e.get("pool") not in existing_pools]
        new_oce = oce + added

        moved = ", ".join(str(e.get("pool")) for e in customer_entries)
        print(f"  • {pool_id}: move side=customer [{moved}] "
              f"on_human_end({len(ohe)}→{len(new_ohe)}) "
              f"on_contact_end({len(oce)}→{len(new_oce)})")

        if not APPLY:
            migrated += 1
            continue

        new_hooks = {
            **hooks,
            "on_human_end":   new_ohe,
            "on_contact_end": new_oce,
        }
        try:
            _req("PUT", f"/v1/pools/{pool_id}", {"hooks": new_hooks})
            migrated += 1
        except urllib.error.HTTPError as exc:
            print(f"  ! {pool_id}: PUT falhou ({exc.code} {exc.read().decode()[:200]})")
            failed += 1
        except urllib.error.URLError as exc:
            print(f"  ! {pool_id}: PUT falhou ({exc})")
            failed += 1

    verb = "migráveis" if not APPLY else "migrados"
    print(f"Resumo: {migrated} {verb}, {skipped} já-ok/sem-NPS, {failed} falhas "
          f"(total {len(pools)} pools).")
    if not APPLY and migrated:
        print("Rode novamente com --apply para gravar.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
