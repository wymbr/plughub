# -*- coding: utf-8 -*-
"""Publica a forma de navegacao com as questions de continuacao (RET-05).

⚠️ O `dialog-seed` e SEED-IF-ABSENT: a forma ja existe, entao re-semear e no-op.
Para o arquivo valer e preciso `PUT` + `publish`, que e o caminho oficial (o
mesmo que o editor usa).

⚠️ E CONFERE o que ficou publicado, nunca o 200: o Zod/pydantic da API faz strip
de chave desconhecida, e um `on_return` que a imagem antiga nao conhece some em
silencio -- foi exatamente o que aconteceu com `max_iterations` na RET-04.
"""
import io
import json
import sys
import urllib.error
import urllib.request

DIALOG = "http://localhost:3760"
FORM = "dialog_navegacao_atendimento_v1"
ARQ = "infra/dialog/dialog_navegacao_atendimento_v1.json"
H = {"X-Tenant-ID": "tenant_demo",
     "X-Admin-Token": "demo_dialog_admin_token",
     "content-type": "application/json"}


def req(metodo, url, corpo=None):
    dados = json.dumps(corpo).encode() if corpo is not None else None
    r = urllib.request.Request(url, data=dados, headers=H, method=metodo)
    try:
        with urllib.request.urlopen(r) as resp:
            txt = resp.read().decode()
            return resp.status, (json.loads(txt) if txt else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


doc = json.load(io.open(ARQ, encoding="utf-8"))
for k in ("created_at", "updated_at", "deleted_at", "status", "version"):
    doc.pop(k, None)

st, corpo = req("PUT", "%s/v1/dialog/forms/%s" % (DIALOG, FORM), doc)
if st not in (200, 201):
    print("PUT FALHOU [%s] %s" % (st, corpo))
    sys.exit(1)
versao = corpo.get("version")
print("PUT ok — rascunho v%s" % versao)

st, corpo = req("POST", "%s/v1/dialog/forms/%s/publish" % (DIALOG, FORM))
if st not in (200, 201):
    print("PUBLISH FALHOU [%s] %s" % (st, corpo))
    sys.exit(1)
print("PUBLISH ok — v%s" % corpo.get("version"))

# ── A CONFERENCIA: o que ficou publicado tem o ponteiro? ────────────────────
st, vivo = req("GET", "%s/v1/dialog/forms/%s" % (DIALOG, FORM))
if st != 200:
    print("GET FALHOU [%s]" % st)
    sys.exit(1)

qs = [n for n in vivo.get("nodes", []) if n.get("kind") == "question"]
ids = [q.get("id") for q in qs]
ponteiros = json.dumps(vivo).count('"on_return"')

print("questions publicadas: %s" % ", ".join(ids))
print("ponteiros `on_return` no publicado: %d" % ponteiros)

falhas = 0
if "pos_atendimento" not in ids:
    print("  ⚠️ a question de continuacao NAO foi publicada")
    falhas += 1
if ponteiros == 0:
    print("  ⚠️ os `on_return` SUMIRAM — a API fez strip do campo que nao conhece;")
    print("     rebuildar o dialog-api (invariante de build, ver RET-04)")
    falhas += 1

sys.exit(1 if falhas else 0)
