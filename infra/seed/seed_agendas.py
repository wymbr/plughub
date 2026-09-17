#!/usr/bin/env python3
"""
seed_agendas.py — provisiona as Agendas declarativas de `infra/scheduler/*.json` no
scheduler-api (porta 3650), VIA API OFICIAL (invariante "provisioning only via official
API": nada de INSERT direto em `scheduler.agendas`).

POR QUE ESTE SEED EXISTE (VOZ-27)
---------------------------------
Até aqui, agenda nenhuma nascia de arquivo: as do repositório eram criadas por scripts de
teste (`infra/test/smoke_*.sh`) e as de verdade, pela tela. Consequência para instalação
NOVA: nada periódico rodava, e a ausência não ficava vermelha em lugar nenhum — o modo de
falha exato que a recalibragem de fala existe para acabar (verificar o caminho de fala só
quando alguém lembra é a mesma cegueira de não verificar).

PRECEDÊNCIA (seed-if-absent / DB-owned, igual ao dialog-seed e ao RegistrySyncer)
--------------------------------------------------------------------------------
  já existe agenda com este `seed_id`  → NÃO TOCA (o DB vence), e DIZ o status dela
  não existe                           → POST /v1/agendas
  RECONCILE=true                       → o arquivo vence: PATCH sobre a existente

A identidade é o `seed_id` gravado em `payload.seed_id`, NUNCA o `name`: nome é texto de
gente, editável na tela, e casar por ele faria a primeira renomeação duplicar a agenda.

⚠️ Agenda APAGADA é recriada no próximo boot — "apagada de propósito" e "nunca semeada"
são indistinguíveis de fora. Para desligar: pausar ou cancelar (o seed respeita o status,
e o diz). Está no `infra/scheduler/README.md`.

Uso:
  SCHEDULER_API_URL=http://scheduler-api:3650 TENANT_ID=tenant_demo \
    python seed_agendas.py
  AGENDA_SEED_RECONCILE=true python seed_agendas.py     # o arquivo vence
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
SCHEDULER_URL = os.environ.get("SCHEDULER_API_URL", "http://scheduler-api:3650")
TENANT_ID     = os.environ.get("TENANT_ID", "tenant_demo")
AGENDAS_DIR   = Path(os.environ.get("AGENDAS_DIR", "/agendas"))
MAX_WAIT_S    = int(os.environ.get("SEED_MAX_WAIT", "120"))
RECONCILE     = os.environ.get("AGENDA_SEED_RECONCILE", "").lower() in ("1", "true", "yes")

# Campos que o `POST /v1/agendas` aceita. O `seed_id` NÃO é um deles — ele viaja dentro do
# payload. Enviar chave desconhecida faria o Pydantic recusar, e um seed que morre no boot
# de um campo novo do arquivo é pior que um que avisa.
_CAMPOS_API = {
    "name", "target_pool_id", "payload", "timezone",
    "calendar_id", "validity", "schedule", "misfire_policy",
}


def log(msg):  print(f"[agenda-seed] {msg}", flush=True)
def ok(msg):   print(f"[ok]          {msg}", flush=True)
def skip(msg): print(f"[skip]        {msg}", flush=True)
def warn(msg): print(f"[warn]        {msg}", flush=True)
def die(msg):  print(f"[error]       {msg}", file=sys.stderr, flush=True); sys.exit(1)


def _req(method: str, path: str, body: dict | None = None) -> tuple[int, dict | list]:
    url  = SCHEDULER_URL.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "X-Tenant-ID": TENANT_ID}
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


def wait_for_scheduler() -> None:
    deadline = time.time() + MAX_WAIT_S
    last = ""
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{SCHEDULER_URL.rstrip('/')}/v1/health", timeout=5)
            log("scheduler-api saudável.")
            return
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            log(f"aguardando scheduler-api… ({last})")
            time.sleep(3)
    die(f"scheduler-api não ficou saudável após {MAX_WAIT_S}s. Último erro: {last}")


def carregar() -> list[tuple[Path, dict]]:
    if not AGENDAS_DIR.is_dir():
        die(f"diretório de agendas não encontrado: {AGENDAS_DIR} "
            "(monte ./infra/scheduler no container ou defina AGENDAS_DIR)")
    out: list[tuple[Path, dict]] = []
    for path in sorted(AGENDAS_DIR.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            warn(f"{path.name}: JSON inválido — PULADO ({type(e).__name__}: {e})")
            continue
        if not doc.get("seed_id"):
            warn(f"{path.name}: sem 'seed_id' — PULADO (sem ele não há idempotência)")
            continue
        if not doc.get("target_pool_id"):
            warn(f"{path.name}: sem 'target_pool_id' — PULADO (agenda aciona POOL)")
            continue
        out.append((path, doc))
    if not out:
        die(f"nenhuma agenda válida em {AGENDAS_DIR}")
    return out


def existente(seed_id: str) -> dict | None:
    """A agenda deste `seed_id`, em QUALQUER status — inclusive paused/cancelled, que são
    decisões do operador e por isso contam como presença.

    ⚠️ A listagem devolve `{"agendas": [...], "total": N}`, não uma lista crua — tratar a
    resposta como lista dava zero item e teria semeado duplicata a cada boot, calado. A recusa
    alta abaixo é que transformou isso num erro visível em vez de numa segunda agenda por dia."""
    status, body = _req("GET", "/v1/agendas")
    itens = body.get("agendas") if isinstance(body, dict) else body
    if status != 200 or not isinstance(itens, list):
        die(f"não consegui listar agendas ({status}: {body}) — não vou criar às cegas, "
            "que é como se semeia duplicata a cada boot")
    for ag in itens:
        if isinstance(ag, dict) and (ag.get("payload") or {}).get("seed_id") == seed_id:
            return ag
    return None


def corpo_api(doc: dict) -> dict:
    corpo = {k: v for k, v in doc.items() if k in _CAMPOS_API}
    payload = dict(corpo.get("payload") or {})
    payload["seed_id"] = doc["seed_id"]        # a identidade viaja com o job
    corpo["payload"] = payload
    desconhecidos = set(doc) - _CAMPOS_API - {"seed_id"}
    if desconhecidos:
        warn(f"{doc['seed_id']}: campo(s) que a API não aceita, ignorado(s): "
             f"{', '.join(sorted(desconhecidos))}")
    return corpo


def semear(doc: dict) -> bool:
    seed_id = doc["seed_id"]
    atual = existente(seed_id)

    if atual is not None and not RECONCILE:
        skip(f"{seed_id}: já existe (id={atual.get('id')}, status={atual.get('status')}, "
             f"próximo disparo={atual.get('next_fire_at')}) — DB vence (seed-if-absent). "
             "Use AGENDA_SEED_RECONCILE=true para o arquivo vencer.")
        return True

    corpo = corpo_api(doc)
    if atual is not None:
        status, body = _req("PATCH", f"/v1/agendas/{atual.get('id')}", corpo)
        if status != 200:
            warn(f"{seed_id}: PATCH (reconcile) falhou: {status} {body}")
            return False
        ok(f"{seed_id}: RECONCILIADA do arquivo (id={body.get('id')}, "
           f"próximo disparo={body.get('next_fire_at')})")
        return True

    status, body = _req("POST", "/v1/agendas", corpo)
    if status != 201:
        warn(f"{seed_id}: POST falhou: {status} {body}")
        return False
    # `next_fire_at` nulo numa recorrente significa que ela NUNCA vai disparar (validade
    # vencida, regra impossível). Criar e ficar calado aí seria semear uma agenda decorativa.
    prox = body.get("next_fire_at")
    if not prox:
        warn(f"{seed_id}: criada (id={body.get('id')}) mas SEM próximo disparo — "
             "a regra não produz ocorrência (validade vencida?); ela não vai rodar")
        return True
    ok(f"{seed_id}: criada (id={body.get('id')}, pool={corpo['target_pool_id']}, "
       f"próximo disparo={prox})")
    return True


def main() -> None:
    wait_for_scheduler()
    agendas = carregar()
    log(f"{len(agendas)} agenda(s) em {AGENDAS_DIR} → {SCHEDULER_URL} "
        f"(tenant={TENANT_ID}, reconcile={RECONCILE})")

    falhas = [doc["seed_id"] for _p, doc in agendas if not semear(doc)]

    if falhas:
        die(f"falha em {len(falhas)}/{len(agendas)} agenda(s): {', '.join(falhas)}")
    ok(f"{len(agendas)} agenda(s) declarada(s) presente(s) no scheduler.")


if __name__ == "__main__":
    main()
