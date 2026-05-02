#!/usr/bin/env python3
"""
seed_auth.py — Cria usuários demo no auth-api para o ambiente docker-demo.

Usuários criados:
  admin@plughub.local       / changeme_admin     (roles: admin, developer)
      → seeded pelo próprio auth-api no startup; este script é idempotente

  supervisor@plughub.local  / changeme_supervisor (roles: supervisor)
      module_config:
        evaluation.revisar   = read_write  (scope: global)
        evaluation.relatorio = read_only
        analytics.view       = read_only
        analytics.export     = read_only

  operator@plughub.local    / changeme_operator  (roles: operator)
      module_config:
        evaluation.contestar = read_write  (scope: global)
        analytics.view       = read_only

Uso:
  AUTH_API_URL=http://auth-api:3200 AUTH_ADMIN_TOKEN=<token> python seed_auth.py
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

# ─── Config ───────────────────────────────────────────────────────────────────
AUTH_URL     = os.environ.get("AUTH_API_URL",   "http://auth-api:3200")
ADMIN_TOKEN  = os.environ.get("AUTH_ADMIN_TOKEN", "changeme_auth_admin_token_demo")
TENANT_ID    = os.environ.get("TENANT_ID",       "tenant_demo")
MAX_WAIT_S   = int(os.environ.get("SEED_MAX_WAIT", "120"))


def log(msg):  print(f"[auth-seed]  {msg}", flush=True)
def ok(msg):   print(f"[ok]         {msg}", flush=True)
def warn(msg): print(f"[warn]       {msg}", flush=True)
def die(msg):  print(f"[error]      {msg}", file=sys.stderr, flush=True); sys.exit(1)


def _req(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    url  = AUTH_URL.rstrip("/") + path
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Content-Type":  "application/json",
            "X-Admin-Token": ADMIN_TOKEN,
        },
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


def wait_for_auth_api():
    """Espera auth-api ficar saudável."""
    deadline = time.time() + MAX_WAIT_S
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{AUTH_URL}/health", timeout=5)
            log("auth-api saudável.")
            return
        except Exception:
            log("aguardando auth-api…")
            time.sleep(3)
    die(f"auth-api não ficou saudável após {MAX_WAIT_S}s.")


def upsert_user(email: str, name: str, password: str, roles: list[str]) -> str | None:
    """Cria usuário; ignora se já existe (409). Retorna user_id ou None."""
    status, body = _req("POST", "/auth/users", {
        "tenant_id": TENANT_ID,
        "email":     email,
        "name":      name,
        "password":  password,
        "roles":     roles,
    })
    if status == 201:
        uid = body.get("id")
        ok(f"Criado: {email}  (id={uid})")
        return uid
    if status == 409:
        # Já existe — busca o id
        s2, b2 = _req("GET", f"/auth/users?tenant_id={TENANT_ID}")
        if s2 == 200:
            for u in b2 if isinstance(b2, list) else b2.get("users", []):
                if u.get("email") == email:
                    uid = u.get("id")
                    warn(f"Já existe: {email}  (id={uid})")
                    return uid
    warn(f"Não conseguiu criar {email}: {status} {body}")
    return None


def set_module_config(user_id: str, config: dict):
    """Define module_config completo do usuário."""
    status, body = _req("PUT", f"/auth/users/{user_id}/module-config", config)
    if status == 200:
        ok(f"module_config atualizado para {user_id}")
    else:
        warn(f"Falha ao definir module_config para {user_id}: {status} {body}")


# ─── Definições de usuários demo ──────────────────────────────────────────────

DEMO_USERS = [
    {
        "email":    "supervisor@plughub.local",
        "name":     "Demo Supervisor",
        "password": "changeme_supervisor",
        "roles":    ["supervisor"],
        "module_config": {
            "evaluation": {
                "revisar":    {"access": "read_write", "scope": []},
                "contestar":  {"access": "none",       "scope": []},
                "relatorio":  {"access": "read_only",  "scope": []},
                "formularios":{"access": "none",       "scope": []},
                "permissoes": {"access": "none",       "scope": []},
            },
            "analytics": {
                "view":              {"access": "read_only", "scope": []},
                "export":            {"access": "read_only", "scope": []},
                "segment_drilldown": {"access": "none",      "scope": []},
            },
            "billing": {
                "view":              {"access": "read_only", "scope": []},
                "manage_resources":  {"access": "none",      "scope": []},
                "manage_pricing":    {"access": "none",      "scope": []},
            },
        },
    },
    {
        "email":    "operator@plughub.local",
        "name":     "Demo Operator",
        "password": "changeme_operator",
        "roles":    ["operator"],
        "module_config": {
            "evaluation": {
                "contestar":  {"access": "read_write", "scope": []},
                "revisar":    {"access": "none",       "scope": []},
                "relatorio":  {"access": "none",       "scope": []},
                "formularios":{"access": "none",       "scope": []},
                "permissoes": {"access": "none",       "scope": []},
            },
            "analytics": {
                "view":              {"access": "read_only", "scope": []},
                "export":            {"access": "none",      "scope": []},
                "segment_drilldown": {"access": "none",      "scope": []},
            },
        },
    },
]


def main():
    wait_for_auth_api()
    log(f"Criando usuários demo em {AUTH_URL} (tenant={TENANT_ID})")

    for user in DEMO_USERS:
        uid = upsert_user(
            email    = user["email"],
            name     = user["name"],
            password = user["password"],
            roles    = user["roles"],
        )
        if uid and user.get("module_config"):
            set_module_config(uid, user["module_config"])

    ok("seed_auth concluído.")


if __name__ == "__main__":
    main()
