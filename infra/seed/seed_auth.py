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
        contacts.operacao     = read_write  (Monitor — decisão 3 do dono, 2026-08-27)
        config.users          = read_write  (Access + Groups — decisão 1)
        config.calendars      = read_write  (decisão 1)
        scheduler.configurar  = read_write  (decisão 1)
        scheduler.operacao    = read_write  (decisão 1)

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
        "module_config": {"config": {
            "users":       {"access": "read_write", "scope": []},
            # `permissions` e obrigatorio aqui: o proprio seed faz PUT module-config,
            # que exige o campo desde o split de 2026-08-27.
            "permissions": {"access": "read_write", "scope": []},
        }},
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
        # Admin demo — module_config EXPLÍCITO e COMPLETO (superusuário).
        #
        # Porquê completo (e não só `scheduler`): o auth-api (e outros serviços) tratam
        # "module_config vazio" como legado e LIBERAM tudo (degradação graciosa). Dar ao
        # admin um config PARCIAL (ex.: só scheduler) desliga essa degradação e o admin
        # passa a ser negado em tudo que não listou — ex.: 403 `config.usuarios` na tela
        # Access. Um admin explícito lista todos os campos do catálogo (infra/modules.yaml),
        # incluindo o novo módulo `scheduler` (Fase 3, D2 — grant individual, não role
        # default). Campos com domínio só read_only ficam em read_only (senão 422 no PUT).
        "email":    "admin@plughub.local",
        "name":     "Demo Admin",
        "password": "changeme_admin",
        "roles":    ["admin", "developer"],
        "module_config": {
            "evaluation": {
                "contestar":          {"access": "read_write", "scope": []},
                "revisar":            {"access": "read_write", "scope": []},
                "contestar_replica":  {"access": "read_write", "scope": []},
                "contestar_treplica": {"access": "read_write", "scope": []},
                "revisar_replica":    {"access": "read_write", "scope": []},
                "revisar_treplica":   {"access": "read_write", "scope": []},
                "curar":              {"access": "read_write", "scope": []},
                "report":             {"access": "read_only",  "scope": []},
                "formularios":        {"access": "read_write", "scope": []},
                "gerir_rubrica":      {"access": "read_write", "scope": []},
            },
            "contacts": {
                "operacao":   {"access": "read_write", "scope": []},
                "visualizar": {"access": "read_only",  "scope": []},
                "exportar":   {"access": "read_write", "scope": []},
            },
            "workflows": {
                "operacao":       {"access": "read_write", "scope": []},
                "visualizar":     {"access": "read_only",  "scope": []},
                "cancelar":       {"access": "read_write", "scope": []},
                "webhooks":       {"access": "read_write", "scope": []},
                "journey_read":   {"access": "read_only",  "scope": []},
                "journey_resume": {"access": "read_write", "scope": []},
            },
            "campaigns": {
                "visualizar": {"access": "read_only",  "scope": []},
                "gerenciar":  {"access": "read_write", "scope": []},
            },
            "agent_assist": {
                "atender":       {"access": "read_write", "scope": []},
                "supervisionar": {"access": "read_write", "scope": []},
            },
            "billing": {
                "visualizar": {"access": "read_only",  "scope": []},
                "gerenciar":  {"access": "read_write", "scope": []},
            },
            "config": {
                "platform":     {"access": "read_write", "scope": []},
                "resources":    {"access": "read_write", "scope": []},
                "channels":     {"access": "read_write", "scope": []},
                "dashboards":   {"access": "read_write", "scope": []},
                "calendars":    {"access": "read_write", "scope": []},
                "dialog_forms": {"access": "read_write", "scope": []},
                "users":        {"access": "read_write", "scope": []},
                "permissions":  {"access": "read_write", "scope": []},
                "masking":      {"access": "read_write", "scope": []},
            },
            "skill_flows": {
                "operacao":   {"access": "read_write", "scope": []},
                "visualizar": {"access": "read_only",  "scope": []},
                "editar":     {"access": "read_write", "scope": []},
            },
            "approvals": {
                "operacao": {"access": "read_write", "scope": []},
                "decide":   {"access": "read_write", "scope": []},
            },
            "scheduler": {
                "configurar": {"access": "read_write", "scope": []},  # autoria de agendas
                "operacao":   {"access": "read_write", "scope": []},  # Monitor: disparar/pausar/cancelar
            },
            "outbound": {
                "configurar": {"access": "read_write", "scope": []},  # mailings + campanhas + import
                "operacao":   {"access": "read_write", "scope": []},  # monitor de entregas
            },
        },
    },
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
                # Monitor (decisão 3 do dono, 2026-08-27: "precisa para operar").
                # ⚠️ Este campo também abre o Console — os dois compartilham
                # `contacts.operacao`; separá-los seria modelagem nova, não grant.
                "operacao":   {"access": "read_write", "scope": []},
            },
            # Decisão 1 do dono (2026-08-27): o supervisor administra Access, Groups,
            # Calendars e Schedules. `config.permissions` fica de FORA de propósito —
            # com ele o supervisor reescreveria a própria fronteira e as decisões 2 e 4
            # viravam sugestão (ver o split do passo 1).
            "config": {
                "users":     {"access": "read_write", "scope": []},   # Access + Groups
                "calendars": {"access": "read_write", "scope": []},
            },
            "scheduler": {
                "configurar": {"access": "read_write", "scope": []},  # autoria de agendas
                "operacao":   {"access": "read_write", "scope": []},  # Monitor › Agendas
            },
            # `billing.visualizar` REMOVIDO em 2026-08-27 (decisão 4 do dono: "o grant
            # é apenas um teste, pode desconsiderar"). Era a única divergência entre
            # este seed e o `role_defaults` do catálogo — e manter uma lista de exceção
            # de uma linha no gate envelheceria pior do que remover a linha.
            # Efeito visível: nenhum. `nav.billing` é portão de PAPEL (admin, business)
            # e o supervisor nunca esteve na lista: tinha o grant e não via a tela.
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
            # Aprovação humana — o operator é o ÚNICO aprovador que exercita a política
            # de masking. Todos os papéis de `masking.supervisor_roles`
            # (supervisor/admin/evaluator/reviewer) casam a regra `* → plain` e veem o
            # pacote em CLARO; só o `operator` cai nas regras por tag
            # (`session.numero_cartao → last_4`, `cpf_titular → last_2`,
            # `limite_solicitado → financial`). Enquanto isto era só do admin, a
            # capacidade existia e a política nunca era exercida por ninguém.
            #
            # `operacao` = ver e reivindicar na inbox pull; `decide` = o resume que
            # roteia o workflow (domínio [none, read_write] — não existe read_only).
            # Ambos NÃO-scopable de propósito: o recorte de pool vem de
            # `accessible_pools` (vazio aqui = todos), não de um scope[] por campo.
            "approvals": {
                "operacao": {"access": "read_write", "scope": []},
                "decide":   {"access": "read_write", "scope": []},
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
