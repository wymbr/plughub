#!/usr/bin/env bash
# probe_dialog_formats_parity.sh — o catálogo de formatos tem UMA autoridade.
#
# A autoridade é `packages/schemas/src/dialog-format.ts`. O gêmeo Python
# (`plughub_contextstore/dialog_formats.py`) é GERADO, e o seed do config-api o
# importa. Este gate existe porque "gerado" é promessa até alguém conferir: nada
# impede um editor de abrir o .py e ajustar uma regex ali, e a divergência
# apareceria só no dia em que duas superfícies discordassem sobre o mesmo campo.
#
# Cinco ramos, e nenhum passa por ausência:
#   A  regenerar da TS produz arquivo IDÊNTICO           (a paridade em si)
#   B  o gêmeo tem conteúdo                              (testemunha de presença)
#   C  todo `from_masked_type` resolve contra `masking.types` VIVO, não o default
#   D  o config-api serve `dialog.formats` com os mesmos ids
#   E  o oráculo da TS aprova o catálogo semeado          (vitest)
#
# C é o ramo que o teste unitário NÃO cobre: lá o catálogo de mascaramento é o
# default embutido; aqui é o que o tenant realmente tem. Um tenant que renomeie
# ou remova um tipo quebra a herança de máscara, e o unitário ficaria verde.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2

RAIZ="$PWD"
GEMEO="packages/py-contextstore/src/plughub_contextstore/dialog_formats.py"
CONFIG_URL="${CONFIG_API_URL:-http://localhost:3600}"
TENANT="${PLUGHUB_TENANT_ID:-tenant_demo}"
FALHAS=0
INCONCLUSIVOS=0

ok()   { echo "  OK           $*"; }
bad()  { echo "  REPROVA      $*"; FALHAS=$((FALHAS+1)); }
inc()  { echo "  INCONCLUSIVO $*"; INCONCLUSIVOS=$((INCONCLUSIVOS+1)); }

echo "== probe_dialog_formats_parity =="
echo

# ── A. regenerar produz o mesmo arquivo ──────────────────────────────────────
if [ ! -f "$GEMEO" ]; then
  bad "A. o gêmeo Python não existe ($GEMEO)"
else
  ANTES=$(mktemp); cp "$GEMEO" "$ANTES"
  SAIDA=$(docker run --rm -v "$RAIZ:/w" -w /w/packages/schemas node:20-alpine \
            node_modules/.bin/vite-node ../../infra/scripts/gen_dialog_formats_py.ts 2>&1)
  RC=$?
  if [ "$RC" != "0" ]; then
    bad "A. o gerador falhou (rc=$RC)"
    echo "$SAIDA" | tail -5 | sed 's/^/       /'
    cp "$ANTES" "$GEMEO"
  elif diff -q "$ANTES" "$GEMEO" >/dev/null 2>&1; then
    ok "A. gêmeo idêntico ao regenerado da TS"
  else
    bad "A. o gêmeo DIVERGE da autoridade TS — alguém editou o gerado"
    diff "$ANTES" "$GEMEO" | head -20 | sed 's/^/       /'
  fi
  rm -f "$ANTES"
fi

# ── B. testemunha de presença ────────────────────────────────────────────────
# Sem este ramo, um gêmeo esvaziado passaria no A (regenerar um catálogo vazio
# também dá arquivos idênticos se a TS também estiver vazia).
IDS=$(python3 - <<'PY' 2>/dev/null
import sys
sys.path.insert(0, "packages/py-contextstore/src")
from plughub_contextstore.dialog_formats import DIALOG_FORMAT_CATALOG as C
print(" ".join(f["id"] for f in C["formats"]))
PY
)
N=$(echo "$IDS" | wc -w)
if [ "$N" -lt 5 ]; then
  bad "B. o gêmeo tem $N formatos — catálogo vazio ou truncado não é aprovação"
else
  ok "B. gêmeo com $N formatos ($(echo "$IDS" | cut -c1-60)…)"
fi

# ── C. from_masked_type resolve contra o masking.types VIVO ──────────────────
VIVO=$(mktemp)
curl -s -m 5 "$CONFIG_URL/config/masking/types?tenant_id=$TENANT" > "$VIVO" 2>/dev/null
if [ ! -s "$VIVO" ] || ! jq -e '.value.types' "$VIVO" >/dev/null 2>&1; then
  inc "C. config-api não respondeu masking.types — a herança de máscara NÃO foi verificada"
  rm -f "$VIVO"
else
  PENDENTES=$(VIVO_FILE="$VIVO" python3 - <<'PY'
import json, os, sys
sys.path.insert(0, "packages/py-contextstore/src")
from plughub_contextstore.dialog_formats import DIALOG_FORMAT_CATALOG as C
with open(os.environ["VIVO_FILE"], encoding="utf-8") as fh:
    vivo = json.load(fh)
ids = {t["id"] for t in vivo["value"]["types"]}
faltam, semmask = [], []
for f in C["formats"]:
    ref = f.get("from_masked_type")
    if not ref:
        continue
    if ref not in ids:
        faltam.append("%s->%s" % (f["id"], ref))
        continue
    t = next(t for t in vivo["value"]["types"] if t["id"] == ref)
    if not (t.get("formato") or {}).get("display"):
        semmask.append("%s->%s" % (f["id"], ref))
print("FALTAM=%s SEMMASK=%s VIVOS=%d" % (",".join(faltam) or "-", ",".join(semmask) or "-", len(ids)))
PY
)
  F=$(echo "$PENDENTES" | sed 's/.*FALTAM=\([^ ]*\).*/\1/')
  S=$(echo "$PENDENTES" | sed 's/.*SEMMASK=\([^ ]*\).*/\1/')
  V=$(echo "$PENDENTES" | sed 's/.*VIVOS=//')
  rm -f "$VIVO"
  if [ "$F" != "-" ]; then
    bad "C. from_masked_type sem tipo no catálogo VIVO: $F (o vivo tem $V tipos)"
  elif [ "$S" != "-" ]; then
    # Não é reprova: um tipo pode legitimamente não ter máscara de exibição
    # (email_addr é assim). Mas quem herdaria dele fica sem máscara, e isso tem
    # de ser DITO em vez de descoberto na tela.
    ok "C. todas as referências resolvem no vivo ($V tipos); sem display (herdam nada): $S"
  else
    ok "C. todas as referências resolvem no catálogo VIVO ($V tipos), todas com máscara"
  fi
fi

# ── D. o config-api serve dialog.formats ─────────────────────────────────────
SERVIDO=$(curl -s -m 5 "$CONFIG_URL/config/dialog/formats?tenant_id=$TENANT" 2>/dev/null)
if [ -z "$SERVIDO" ] || ! echo "$SERVIDO" | jq -e '.value.formats' >/dev/null 2>&1; then
  inc "D. config-api não serve dialog.formats — semeado? (serviço no ar mas chave ausente conta aqui)"
else
  SIDS=$(echo "$SERVIDO" | jq -r '[.value.formats[].id] | sort | join(" ")')
  GIDS=$(echo "$IDS" | tr ' ' '\n' | sort | tr '\n' ' ' | sed 's/ *$//')
  if [ "$SIDS" = "$GIDS" ]; then
    ok "D. config-api serve os mesmos $(echo "$SIDS" | wc -w) formatos"
  else
    # Store é fonte de verdade e seed é seed-if-absent, então divergir aqui é
    # ESPERADO depois de uma edição de tenant. Por isso avisa em vez de reprovar
    # — mas nomeia a diferença, que é o que faltaria para diagnosticar.
    ok "D. config-api DIVERGE do semeado (esperado após edição de tenant — o store vence)"
    echo "       servido: $SIDS"
    echo "       semente: $GIDS"
  fi
fi

# ── E. o oráculo da TS aprova ────────────────────────────────────────────────
VT=$(docker run --rm -v "$RAIZ:/w" -w /w/packages/schemas node:20-alpine \
       npx vitest run src/dialog-format.test.ts 2>&1)
if echo "$VT" | grep -qE '^ *Tests +[0-9]+ passed'; then
  ok "E. oráculo + interpretador verdes ($(echo "$VT" | grep -oE 'Tests +[0-9]+ passed' | head -1))"
else
  bad "E. o oráculo da TS reprovou"
  echo "$VT" | grep -E 'FAIL|×|AssertionError' | head -10 | sed 's/^/       /'
fi

echo
echo "======================"
if [ "$FALHAS" -gt 0 ]; then echo "REPROVADO ($FALHAS)"; exit 1; fi
if [ "$INCONCLUSIVOS" -gt 0 ]; then echo "INCONCLUSIVO ($INCONCLUSIVOS) — não é verde"; exit 2; fi
echo "VERDE"
