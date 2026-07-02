#!/usr/bin/env python3
"""
seed_auth.py — Cria usuários demo no auth-api para o ambiente docker-demo.

Usuários criados:
  admin@plughub.local       / changeme_admin     (roles: admin, developer)
      → seeded pelo próprio auth-api no startup; este script é idempotente

  supervisor@plughub.local  / changeme_supervisor (roles: supervisor)
      module_config (campos do catálogo infra/modules.yaml):
        evaluation.revisar    = read_write
        evaluation.report     = read_only
        evaluation.curar      = read_write  (G-PROBE perna humana, 2026-07-02 — curadoria/calibração)
        contacts.visualizar   = read_only
        contacts.exportar     = read_write
        billing.visualizar    = read_only

  operator@plughub.local    / changeme_operator  (roles: operator)
      module_config (campos do catálogo infra/modules.yaml):
        evaluation.contestar  = read_write
        contacts.operacao     = read_write
        contacts.visualizar   = read_only

Uso:
  AUTH_API_URL=http://auth-api:3200 AUTH_JWT_SECRET=<jwt_secret> python seed_auth.py
  (o seed minta um Bearer de bootstrap com config.usuarios:read_write; o jwt_secret
   precisa bater com o PLUGHUB_AUTH_JWT_SECRET da auth-api.)
"""

import json
import base64
import hashlib
import hmac
import os
import sys
import time
import urllib.request
import urllib.error

# ─── Config ───────────────────────────────────────────────────────────────────
AUTH_URL     = os.environ.get("AUTH_API_URL",   "http://auth-api:3200")
TENANT_ID    = os.environ.get("TENANT_ID",       "tenant_demo")
MAX_WAIT_S   = int(os.environ.get("SEED_MAX_WAIT", "120"))
# G-PROBE platform-wide: auth-api deixou de aceitar X-Admin-Token nas rotas de gestão
# (strict Bearer+ABAC `config.usuarios`). O bootstrap minta um Bearer próprio assinado
# com o MESMO jwt_secret que a auth-api valida (HS256) — sem dependência externa.
JWT_SECRET   = os.environ.get("AUTH_JWT_SECRET", "changeme_auth_jwt_secret_demo_32c")


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _mint_bootstrap_jwt() -> str:
    """JWT HS256 de bootstrap com `config.usuarios:read_write` (stdlib, sem pyjwt/jose)."""
    now = int(time.time())
    header  = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub":           "seed_bootstrap",
        "tenant_id":     TENANT_ID,
        "roles":         ["admin"],
        "module_config": {"config": {"usuarios": {"access": "read_write", "scope": []}}},
        "iat":           now,
        "exp":           now + 3600,
    }
    h = _b64url(json.dumps(header,  separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(JWT_SECRET.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


BOOTSTRAP_JWT = _mint_bootstrap_jwt()


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
            "Authorization": f"Bearer {BOOTSTRAP_JWT}",
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
    elif status == 422:
        # Drift de schema: algum módulo/campo não existe no module_registry
        # (catálogo = infra/modules.yaml). Falha alto — é erro de config, não ruído.
        die(f"module_config para {user_id} viola o catálogo (modules.yaml): {body}")
    else:
        warn(f"Falha ao definir module_config para {user_id}: {status} {body}")


# ─── Definições de usuários demo ──────────────────────────────────────────────

# IMPORTANTE: os campos abaixo DEVEM existir em infra/modules.yaml (a fonte única do
# catálogo ABAC, carregada pelo auth-api no module_registry). O PUT module-config
# valida cada módulo/campo contra o registro e rejeita TODO o config com 422 se houver
# qualquer campo desconhecido. Manter este seed alinhado ao modules.yaml.
# (config-consolidation item 5: antes referenciava módulo `analytics` inexistente,
#  `evaluation.relatorio`/`permissoes` e `billing.view` — campos que não existem no
#  catálogo → 422 → demo users ficavam sem ABAC.)
DEMO_USERS = [
    {
        "email":    "supervisor@plughub.local",
        "name":     "Demo Supervisor",
        "password": "changeme_supervisor",
        "roles":    ["supervisor"],
        "module_config": {
            "evaluation": {
                "revisar": {"access": "read_write", "scope": []},  # revisa/decide
                "report":  {"access": "read_only",  "scope": []},  # relatórios de qualidade
                "curar":   {"access": "read_write", "scope": []},  # G-PROBE 2026-07-02: curadoria/calibração
            },
            "contacts": {
                "visualizar": {"access": "read_only",  "scope": []},  # vê contatos/relatórios
                "exportar":   {"access": "read_write", "scope": []},  # exporta dados
            },
            "billing": {
                "visualizar": {"access": "read_only", "scope": []},
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
                "contestar": {"access": "read_write", "scope": []},  # contesta avaliações
            },
            "contacts": {
                "operacao":   {"access": "read_write", "scope": []},  # Monitor/Agent Assist
                "visualizar": {"access": "read_only",  "scope": []},  # vê contatos
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
