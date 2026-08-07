#!/usr/bin/env python3
"""
seed_dialog.py — provisiona os DialogForms declarativos de `infra/dialog/*.json`
na dialog-api (porta 3760), VIA API OFICIAL (invariante "provisioning only via
official API": nada de INSERT direto em `dialog.forms`).

POR QUE ESTE SEED EXISTE
------------------------
Até 2026-08-07 nenhum DialogForm era criado no boot. Os formulários viviam só em
scripts ad-hoc (`infra/test/seed_dialog_*.sh`) rodados à mão, então um banco NOVO
subia sem eles — e os dois consumidores degradavam EM SILÊNCIO:

  • agente_nps_v1: `carregar_form` (form_get) → 404 → `on_failure: encerrar`.
    O contato fecha sem NPS; nada fica vermelho.
  • DialogFormRenderer (Console): fetch `?status=published` → 404 → setForm(null).
    O item de wrap-up é reivindicado, mas o painel abre SEM formulário.

PRECEDÊNCIA (seed-if-absent / DB-owned, igual ao RegistrySyncer)
----------------------------------------------------------------
  há versão publicada  → NÃO TOCA (o DB vence; edição pela UI sobrevive a rebuild)
  não há               → POST (draft) + POST /publish
  RECONCILE=true       → o arquivo vence: PUT (novo draft) + publish

Nenhum caminho é mudo: cada form imprime a decisão E o motivo dela.

Uso:
  DIALOG_API_URL=http://dialog-api:3760 TENANT_ID=tenant_demo \
    python seed_dialog.py
  DIALOG_SEED_RECONCILE=true python seed_dialog.py     # arquivo vence
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ─── Config ───────────────────────────────────────────────────────────────────
DIALOG_URL  = os.environ.get("DIALOG_API_URL", "http://dialog-api:3760")
TENANT_ID   = os.environ.get("TENANT_ID", "tenant_demo")
FORMS_DIR   = Path(os.environ.get("DIALOG_FORMS_DIR", "/dialog"))
ADMIN_TOKEN = os.environ.get("DIALOG_ADMIN_TOKEN", "")
MAX_WAIT_S  = int(os.environ.get("SEED_MAX_WAIT", "120"))
RECONCILE   = os.environ.get("DIALOG_SEED_RECONCILE", "").lower() in ("1", "true", "yes")


def log(msg):  print(f"[dialog-seed] {msg}", flush=True)
def ok(msg):   print(f"[ok]          {msg}", flush=True)
def skip(msg): print(f"[skip]        {msg}", flush=True)
def warn(msg): print(f"[warn]        {msg}", flush=True)
def die(msg):  print(f"[error]       {msg}", file=sys.stderr, flush=True); sys.exit(1)


def _req(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    url  = DIALOG_URL.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "X-Tenant-ID": TENANT_ID}
    if ADMIN_TOKEN:
        headers["X-Admin-Token"] = ADMIN_TOKEN
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
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
    except Exception as e:                     # rede/DNS — devolve o motivo, não um 0 mudo
        return 0, {"detail": f"{type(e).__name__}: {e}"}


def wait_for_dialog_api() -> None:
    deadline = time.time() + MAX_WAIT_S
    last = ""
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{DIALOG_URL.rstrip('/')}/v1/health", timeout=5)
            log("dialog-api saudável.")
            return
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            log(f"aguardando dialog-api… ({last})")
            time.sleep(3)
    die(f"dialog-api não ficou saudável após {MAX_WAIT_S}s. Último erro: {last}")


def load_forms() -> list[tuple[Path, dict]]:
    if not FORMS_DIR.is_dir():
        die(f"diretório de forms não encontrado: {FORMS_DIR} "
            "(monte ./infra/dialog no container ou defina DIALOG_FORMS_DIR)")
    out: list[tuple[Path, dict]] = []
    for path in sorted(FORMS_DIR.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            warn(f"{path.name}: JSON inválido — PULADO ({type(e).__name__}: {e})")
            continue
        if not doc.get("form_id"):
            warn(f"{path.name}: sem 'form_id' — PULADO")
            continue
        out.append((path, doc))
    if not out:
        die(f"nenhum DialogForm válido em {FORMS_DIR}")
    return out


def published_version(form_id: str) -> int | None:
    """Versão publicada corrente, ou None se não houver (404)."""
    status, body = _req("GET", f"/v1/dialog/forms/{form_id}?status=published")
    if status == 200:
        return body.get("version")
    if status != 404:
        warn(f"{form_id}: GET published devolveu {status} ({body}) — tratando como AUSENTE")
    return None


def seed_one(form_id: str, doc: dict) -> bool:
    current = published_version(form_id)

    if current is not None and not RECONCILE:
        skip(f"{form_id}: já publicado (v{current}) — DB vence (seed-if-absent). "
             "Use DIALOG_SEED_RECONCILE=true para o arquivo vencer.")
        return True

    verb, path = ("PUT", f"/v1/dialog/forms/{form_id}") if current is not None \
        else ("POST", "/v1/dialog/forms")
    reason = f"reconcile sobre v{current}" if current is not None else "ausente no store"

    status, body = _req(verb, path, doc)
    if status not in (200, 201):
        warn(f"{form_id}: {verb} falhou ({reason}): {status} {body}")
        return False
    version = body.get("version")

    status, body = _req("POST", f"/v1/dialog/forms/{form_id}/publish?version={version}")
    if status not in (200, 201):
        warn(f"{form_id}: draft v{version} criado mas o publish falhou: {status} {body} "
             "— o form NÃO está resolvível por form_get/renderer")
        return False

    ok(f"{form_id}: publicado v{version} ({reason})")
    return True


def main() -> None:
    wait_for_dialog_api()
    forms = load_forms()
    log(f"{len(forms)} DialogForm(s) em {FORMS_DIR} → {DIALOG_URL} "
        f"(tenant={TENANT_ID}, reconcile={RECONCILE})")

    failures = [doc["form_id"] for _p, doc in forms if not seed_one(doc["form_id"], doc)]

    if failures:
        die(f"falha em {len(failures)}/{len(forms)} form(s): {', '.join(failures)}")
    ok(f"{len(forms)} DialogForm(s) resolvíveis por form_get / DialogFormRenderer.")


if __name__ == "__main__":
    main()
