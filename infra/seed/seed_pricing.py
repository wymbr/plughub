#!/usr/bin/env python3
"""
seed_pricing.py — Recursos contratados do demo (capacity-governance item 6).

Configura installation_resources do tenant_demo coerentes com os deploys do
tenant_demo.yaml (Σ declarada = 280 IA) + margem:

  ai_agent     × 300  (base)  — cobre os 280 declarados + pools de teste
  human_agent  ×  10  (base)  — logins humanos concorrentes do demo

C resultante = 310 → quota {t}:quota:max_concurrent_sessions gravada pelo
quota sync do pricing-api (item 1); saldo positivo no Billing/Capacidade.

IDEMPOTENTE E NÃO-DESTRUTIVO: se o tenant já tem QUALQUER resource configurado,
o seed pula tudo — ajustes do operador (ex. testes de gate de admissão com C
baixo) sobrevivem a re-runs do compose. Para re-semear: delete os resources.

Uso:
  PRICING_API_URL=http://pricing-api:3900 PRICING_ADMIN_TOKEN=<token> python seed_pricing.py
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

PRICING_URL  = os.environ.get("PRICING_API_URL",    "http://pricing-api:3900")
ADMIN_TOKEN  = os.environ.get("PRICING_ADMIN_TOKEN", "demo_pricing_admin_token")
TENANT_ID    = os.environ.get("TENANT_ID",           "tenant_demo")
MAX_WAIT_S   = int(os.environ.get("SEED_MAX_WAIT", "120"))

RESOURCES = [
    {"resource_type": "ai_agent",    "quantity": 300, "pool_type": "base",
     "label": "Agentes IA (deploys do demo + margem)"},
    {"resource_type": "human_agent", "quantity": 10,  "pool_type": "base",
     "label": "Agentes humanos concorrentes"},
]


def log(msg):  print(f"[pricing-seed] {msg}", flush=True)
def ok(msg):   print(f"[ok]           {msg}", flush=True)
def die(msg):  print(f"[error]        {msg}", file=sys.stderr, flush=True); sys.exit(1)


def _req(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    url  = PRICING_URL.rstrip("/") + path
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "X-Admin-Token": ADMIN_TOKEN},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"detail": raw.decode(errors="replace")}


def wait_for_pricing():
    deadline = time.time() + MAX_WAIT_S
    while time.time() < deadline:
        try:
            status, _ = _req("GET", "/health")
            if status == 200:
                return
        except Exception:
            pass
        time.sleep(2)
    die(f"pricing-api não respondeu em {MAX_WAIT_S}s")


def main():
    wait_for_pricing()

    status, data = _req("GET", f"/v1/pricing/resources/{TENANT_ID}")
    if status != 200:
        die(f"GET resources falhou: HTTP {status} {data}")
    existing = data.get("resources") or []
    if existing:
        ok(f"tenant {TENANT_ID} já tem {len(existing)} resource(s) — seed pulado "
           "(ajustes do operador preservados)")
        return

    for r in RESOURCES:
        status, resp = _req("POST", f"/v1/pricing/resources/{TENANT_ID}", r)
        if status not in (200, 201):
            die(f"POST resource {r['resource_type']} falhou: HTTP {status} {resp}")
        ok(f"{r['resource_type']} × {r['quantity']} ({r['label']})")

    status, cap = _req("GET", f"/v1/pricing/capacity/{TENANT_ID}")
    if status == 200:
        ok(f"capacidade contratada (C) = {cap.get('agent_capacity_total')} "
           "— quota de admissão gravada pelo quota sync")


if __name__ == "__main__":
    main()
