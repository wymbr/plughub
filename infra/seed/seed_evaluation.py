#!/usr/bin/env python3
"""
seed_evaluation.py — Cria formulário e campanha de avaliação demo no ambiente docker-demo.

Recursos criados:
  Formulário "SAC Padrão" (form_id: form_sac_padrao)
    Critérios:
      - saudacao    (weight 0.20) — Saudação e apresentação conforme protocolo
      - empatia     (weight 0.25) — Demonstração de empatia com o cliente
      - resolucao   (weight 0.35) — Resolução efetiva do problema
      - conformidade(weight 0.20) — Conformidade com scripts e políticas

  Campanha "Demo SAC — Avaliação Contínua" (campaign_id: camp_demo_sac)
    - Pool: sac (todos os agentes IA do pool de SAC)
    - Amostragem: 30% das sessões resolvidas
    - Skill de revisão: skill_revisao_simples_v1
    - Política de contestação habilitada (1 round, 48h deadline)

Uso:
  EVALUATION_API_URL=http://evaluation-api:3400 ADMIN_TOKEN=<token> python seed_evaluation.py
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

# ─── Config ───────────────────────────────────────────────────────────────────
EVAL_URL    = os.environ.get("EVALUATION_API_URL", "http://evaluation-api:3400")
ADMIN_TOKEN = os.environ.get("AUTH_ADMIN_TOKEN",   "changeme_auth_admin_token_demo")
TENANT_ID   = os.environ.get("TENANT_ID",          "tenant_demo")
MAX_WAIT_S  = int(os.environ.get("SEED_MAX_WAIT",  "120"))


def log(msg):  print(f"[eval-seed]  {msg}", flush=True)
def ok(msg):   print(f"[ok]         {msg}", flush=True)
def warn(msg): print(f"[warn]       {msg}", flush=True)
def die(msg):  print(f"[error]      {msg}", file=sys.stderr, flush=True); sys.exit(1)


def _req(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    url  = EVAL_URL.rstrip("/") + path
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


def wait_for_eval_api():
    """Espera evaluation-api ficar saudável."""
    deadline = time.time() + MAX_WAIT_S
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{EVAL_URL}/health", timeout=5)
            log("evaluation-api saudável.")
            return
        except Exception:
            log("aguardando evaluation-api…")
            time.sleep(3)
    die(f"evaluation-api não ficou saudável após {MAX_WAIT_S}s.")


def upsert_form() -> str | None:
    """Cria formulário SAC Padrão; ignora se já existe (409). Retorna form_id."""
    form_id = "form_sac_padrao"

    # Verifica se já existe
    status, body = _req("GET", f"/v1/evaluation/forms?tenant_id={TENANT_ID}")
    if status == 200:
        forms = body if isinstance(body, list) else body.get("forms", [])
        for f in forms:
            if f.get("form_id") == form_id:
                warn(f"Formulário já existe: {form_id}")
                return form_id

    # Cria o formulário
    status, body = _req("POST", "/v1/evaluation/forms", {
        "form_id":           form_id,
        "tenant_id":         TENANT_ID,
        "name":              "SAC Padrão",
        "description":       "Formulário padrão de avaliação de qualidade para o SAC",
        "knowledge_namespace": "politicas_sac",
        "criteria": [
            {
                "id":          "saudacao",
                "label":       "Saudação e apresentação",
                "description": "O agente realizou a saudação e apresentação conforme o protocolo estabelecido?",
                "weight":      0.20,
                "type":        "pass_fail",
            },
            {
                "id":          "empatia",
                "label":       "Demonstração de empatia",
                "description": "O agente demonstrou empatia com a situação do cliente durante toda a interação?",
                "weight":      0.25,
                "type":        "score",
                "options": [
                    {"value": 0,  "label": "Não demonstrou"},
                    {"value": 5,  "label": "Demonstrou parcialmente"},
                    {"value": 10, "label": "Demonstrou plenamente"},
                ],
            },
            {
                "id":          "resolucao",
                "label":       "Resolução efetiva do problema",
                "description": "O agente resolveu o problema do cliente de forma efetiva e dentro do SLA?",
                "weight":      0.35,
                "type":        "score",
                "options": [
                    {"value": 0,  "label": "Não resolveu"},
                    {"value": 4,  "label": "Resolveu parcialmente"},
                    {"value": 7,  "label": "Resolveu com ressalvas"},
                    {"value": 10, "label": "Resolveu completamente"},
                ],
            },
            {
                "id":          "conformidade",
                "label":       "Conformidade com scripts e políticas",
                "description": "O agente seguiu os scripts e políticas aprovadas sem desvios?",
                "weight":      0.20,
                "type":        "na_allowed",
            },
        ],
        "active": True,
    })

    if status in (200, 201):
        ok(f"Formulário criado: {form_id}")
        return form_id

    warn(f"Não conseguiu criar formulário: {status} {body}")
    return None


def upsert_campaign(form_id: str) -> str | None:
    """Cria campanha de avaliação demo; ignora se já existe. Retorna campaign_id."""
    campaign_id = "camp_demo_sac"

    # Verifica se já existe
    status, body = _req("GET", f"/v1/evaluation/campaigns?tenant_id={TENANT_ID}")
    if status == 200:
        campaigns = body if isinstance(body, list) else body.get("campaigns", [])
        for c in campaigns:
            if c.get("campaign_id") == campaign_id:
                warn(f"Campanha já existe: {campaign_id}")
                return campaign_id

    # Cria a campanha
    status, body = _req("POST", "/v1/evaluation/campaigns", {
        "campaign_id":              campaign_id,
        "tenant_id":                TENANT_ID,
        "name":                     "Demo SAC — Avaliação Contínua",
        "form_id":                  form_id,
        "pool_id":                  "sac",
        "evaluator_pool_id":        "avaliacao_ia",
        "review_workflow_skill_id": "skill_revisao_simples_v1",
        "sampling": {
            "mode":           "random",
            "sample_rate":    0.30,
            "outcome_filter": ["resolved"],
            "min_duration_ms": 30000,
        },
        "reviewer_rules": {
            "auto_approve_above":  0.85,
            "auto_reject_below":   0.50,
            "require_human_review": False,
        },
        "contestation_policy": {
            "contestation_roles": ["operator"],
            "review_roles_by_round": {"1": ["supervisor"]},
            "authority_by_round":    {"1": "supervisor"},
            "review_deadline_hours": 48,
        },
        "status": "active",
    })

    if status in (200, 201):
        ok(f"Campanha criada: {campaign_id}")
        return campaign_id

    warn(f"Não conseguiu criar campanha: {status} {body}")
    return None


def main():
    wait_for_eval_api()
    log(f"Criando recursos de avaliação demo em {EVAL_URL} (tenant={TENANT_ID})")

    form_id = upsert_form()
    if not form_id:
        die("Falha ao criar/verificar formulário.")

    campaign_id = upsert_campaign(form_id)
    if not campaign_id:
        warn("Falha ao criar/verificar campanha — formulário OK, campanha falhou.")

    ok("seed_evaluation concluído.")


if __name__ == "__main__":
    main()
