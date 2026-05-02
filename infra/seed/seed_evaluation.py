#!/usr/bin/env python3
"""
seed_evaluation.py — Cria formulário e campanha de avaliação demo no ambiente docker-demo.

Recursos criados:
  Formulário "SAC Padrão"
    Dimensão: Qualidade do Atendimento (weight 1.0)
      Critérios:
        - saudacao    (weight 0.20) — Saudação e apresentação conforme protocolo
        - empatia     (weight 0.25) — Demonstração de empatia com o cliente
        - resolucao   (weight 0.35) — Resolução efetiva do problema
        - conformidade(weight 0.20) — Conformidade com scripts e políticas

  Campanha "Demo SAC — Avaliação Contínua"
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

FORM_NAME = "SAC Padrão"

DIMENSIONS = [
    {
        "dimension_id": "qualidade_atendimento",
        "label":        "Qualidade do Atendimento",
        "weight":       1.0,
        "criteria": [
            {
                "criterion_id": "saudacao",
                "label":        "Saudação e apresentação",
                "description":  "O agente realizou a saudação e apresentação conforme o protocolo estabelecido?",
                "weight":       0.20,
                "allows_na":    False,
                "max_score":    10,
            },
            {
                "criterion_id": "empatia",
                "label":        "Demonstração de empatia",
                "description":  "O agente demonstrou empatia com a situação do cliente durante toda a interação?",
                "weight":       0.25,
                "allows_na":    False,
                "max_score":    10,
            },
            {
                "criterion_id": "resolucao",
                "label":        "Resolução efetiva do problema",
                "description":  "O agente resolveu o problema do cliente de forma efetiva e dentro do SLA?",
                "weight":       0.35,
                "allows_na":    False,
                "max_score":    10,
            },
            {
                "criterion_id": "conformidade",
                "label":        "Conformidade com scripts e políticas",
                "description":  "O agente seguiu os scripts e políticas aprovadas sem desvios?",
                "weight":       0.20,
                "allows_na":    True,
                "max_score":    10,
            },
        ],
    }
]


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
    """
    Cria ou actualiza o formulário SAC Padrão.
    A API auto-gera o form_id (evform_…). Identificamos pelo nome.
    Se o formulário existir com dimensions vazias, faz PATCH para corrigi-lo.
    Retorna o form_id gerado pela API.
    """
    # Verifica se já existe pelo nome
    status, body = _req("GET", f"/v1/evaluation/forms?tenant_id={TENANT_ID}")
    if status == 200:
        forms = body if isinstance(body, list) else body.get("forms", [])
        for f in forms:
            if f.get("name") == FORM_NAME:
                form_id = f.get("id") or f.get("form_id", "")
                existing_dims = f.get("dimensions") or []
                if not existing_dims:
                    # Dimensions vazias — corrigir via PUT (o router não expõe PATCH)
                    log(f"Formulário '{FORM_NAME}' existe ({form_id}) mas sem dimensões — corrigindo via PUT…")
                    p_status, p_body = _req("PUT", f"/v1/evaluation/forms/{form_id}?tenant_id={TENANT_ID}", {
                        "dimensions": DIMENSIONS,
                    })
                    if p_status in (200, 204):
                        ok(f"Formulário '{FORM_NAME}' corrigido: {form_id}")
                    else:
                        warn(f"Falha no PUT do formulário {form_id}: {p_status} {p_body}")
                else:
                    warn(f"Formulário '{FORM_NAME}' já existe e tem dimensões: {form_id}")
                return form_id

    # Cria novo formulário
    status, body = _req("POST", "/v1/evaluation/forms", {
        "tenant_id":   TENANT_ID,
        "name":        FORM_NAME,
        "description": "Formulário padrão de avaliação de qualidade para o SAC",
        "dimensions":  DIMENSIONS,
    })

    if status in (200, 201):
        form_id = body.get("id") or body.get("form_id", "")
        ok(f"Formulário criado: {FORM_NAME} ({form_id})")
        return form_id

    warn(f"Não conseguiu criar formulário: {status} {body}")
    return None


def upsert_campaign(form_id: str) -> str | None:
    """Cria campanha de avaliação demo; ignora se já existe. Retorna campaign_id da API."""
    # Verifica se já existe pelo nome
    status, body = _req("GET", f"/v1/evaluation/campaigns?tenant_id={TENANT_ID}")
    if status == 200:
        campaigns = body if isinstance(body, list) else body.get("campaigns", [])
        for c in campaigns:
            if c.get("name") == "Demo SAC — Avaliação Contínua":
                campaign_id = c.get("id") or c.get("campaign_id", "")
                warn(f"Campanha já existe: {campaign_id}")
                return campaign_id

    # Cria a campanha
    status, body = _req("POST", "/v1/evaluation/campaigns", {
        "tenant_id":                TENANT_ID,
        "name":                     "Demo SAC — Avaliação Contínua",
        "form_id":                  form_id,
        "pool_id":                  "sac",
        "evaluator_pool_id":        "avaliacao_ia",
        "review_workflow_skill_id": "skill_revisao_simples_v1",
        "sampling_rules": {
            "mode":           "percentage",
            "rate":           0.30,
            "outcome_filter": ["resolved"],
            "min_duration_s": 30,
        },
        "reviewer_rules": {
            "auto_review":   True,
            "score_threshold": 0.85,
            "random_rate":   0.0,
            "human_review":  False,
        },
        "contestation_policy": {
            "contestation_roles":    ["operator"],
            "max_rounds":            1,
            "review_deadline_hours": 48,
            "auto_lock_on_timeout":  True,
        },
        "status": "active",
    })

    if status in (200, 201):
        campaign_id = body.get("id") or body.get("campaign_id", "")
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
