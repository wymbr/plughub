#!/usr/bin/env bash
#
# probe_dialog_json_surface.sh — o dry-run do editor de DialogForm responde, e
# responde a MESMA coisa que o `form_get` responderia.
#
# POR QUE ESTE PROBE EXISTE
# =========================
# Medido em 2026-09-04, antes do conserto:
#   · o editor oferecia `interaction: form` e NÃO sabia autorar `fields[]`
#     (`grep fields DialogFormsPage.tsx` = zero). O nó ia para produção sem campo,
#     degenerava num campo sintético `{type:"choice"}` sem opções, e o publish
#     aceitava — nó morto, nada vermelho;
#   · a dialog-api afirma no docstring que *"the canonical validator is the Zod
#     DialogFormSchema on the TS side"*, e do lado TS **ninguém parseava**. Os
#     widgets eram validador por ACIDENTE de afordância — e o editor JSON remove
#     justamente o acidente. Daí o dry-run existir, e daí este probe existir.
#
# O QUE ESTE PROBE PODE REPROVAR (a pergunta que todo verde tem de responder)
# ===========================================================================
#   S1  a rota responder SEM credencial                        → VERMELHO
#       (não exige PAPEL por decisão — é função pura sobre o corpo do chamador —,
#        mas anônima ela não pode ser)
#   S2  contraprova POSITIVA: rascunho válido reprovar         → VERMELHO
#       — inclusive SEM `tenant_id`/`created_at`/`updated_at`, que são do STORE.
#         Sem este ramo, um validador que reprova TUDO passaria no S3.
#   S3  forma inválida (id de nó repetido) passar              → VERMELHO
#   S4  `menu_prompt` voltar a descartar o prompt da pergunta  → VERMELHO
#       (a regressão que viveu meses: `before.join() || qPrompt`)
#   S5  `fields[]` sumir do render, ou `masked` tipado virar   → VERMELHO
#       booleano (`=== true` faria `"card_cvv"` sair DESMASCARADO)
#   S6  o veredicto divergir do que a forma REAL semeada produz → VERMELHO
#   S7  a subárvore sumir do `render`, ou `options_tree` deixar de ser  → VERMELHO
#       DERIVADO da estrutura (F3) — achatar faria a PASTA virar resposta,
#       e o operador gravaria "Financeiro" onde devia gravar a folha
#       (`dialog_limite_solicitacao`, 4 campos, `cvv` mascarado — é a ancoragem
#        em dado de produção, não em fixture do próprio probe)
#
# INCONCLUSIVO é ramo próprio: sem mcp-server de pé ou sem login, o probe NÃO
# declara verde — verde por ausência de amostra é a família "teste que não pode
# reprovar".
#
# Uso:  bash infra/test/probe_dialog_json_surface.sh
set -uo pipefail

MCP="${MCP_SERVER:-http://localhost:3100}"
DLG="${DIALOG_API:-http://localhost:3760}"
AUTH="${AUTH_API:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
FAIL=0; INC=0
ok()   { echo "  ${GRN}✓${RST} $*"; }
bad()  { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }
inc()  { echo "  ${YLW}?${RST} $*"; INC=$((INC+1)); }
info() { echo "    $*"; }
head_() { echo; echo "${BLD}$*${RST}"; }

status() { curl -s -o /dev/null -m 10 -w "%{http_code}" "$@" 2>/dev/null || echo "000"; }

login() {
  curl -s -m 10 -X POST "$AUTH/auth/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"$2\"}" 2>/dev/null \
    | sed -n 's/.*"access_token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
}

# `preview <jwt> <json>` → corpo da resposta
preview() {
  curl -s -m 10 -X POST "$MCP/api/dialog/preview" \
    -H 'content-type: application/json' -H "authorization: Bearer $1" \
    -H "x-tenant-id: $TENANT" -d "$2" 2>/dev/null
}

# jq não é dependência garantida deste repositório; python3 é (todo probe o usa).
jqp() { python3 -c "$1" 2>/dev/null; }

# ── pré-condição ──────────────────────────────────────────────────────────────
head_ "PRÉ-CONDIÇÃO"
s=$(status "$MCP/health")
[ "$s" = "200" ] || s=$(status "$MCP/api/instances")
if [ "$s" = "200" ] || [ "$s" = "401" ] || [ "$s" = "403" ]; then
  ok "mcp-server responde ($MCP)"
else
  inc "mcp-server não respondeu ($s) — sem ele nada abaixo mede"
fi

JWT=$(login "admin@plughub.local" "changeme_admin")
[ -n "$JWT" ] && ok "login admin@" || inc "login admin@ falhou — sem credencial não há contraprova positiva"

if [ "$INC" -gt 0 ]; then
  echo; echo "${YLW}INCONCLUSIVO${RST} — dependência fora do ar; o probe não mediu."; exit 0
fi

VALIDA='{"form":{"form_id":"probe_json_surface","name":"probe","default_locale":"pt-BR",
 "locales":["pt-BR"],"dimensions":[],"tags":[],
 "nodes":[{"id":"intro","kind":"statement","text":"Vou registrar seu pedido."},
          {"id":"dados","kind":"question","prompt":"Preencha os dados:","interaction":"form",
           "output_key":"dados","timeout_s":300,
           "fields":[{"id":"numero","label":"Numero","type":"text","required":true},
                     {"id":"cvv","label":"CVV","type":"text","required":true,"masked":"card_cvv"}]}]}}'

# ── S1 — anônimo não passa ────────────────────────────────────────────────────
head_ "S1 — a rota exige credencial (não exige papel, por decisão)"
s=$(status -X POST "$MCP/api/dialog/preview" -H 'content-type: application/json' -d "$VALIDA")
case "$s" in
  401|503) ok "anônimo recusado ($s)" ;;
  200)     bad "anônimo foi ATENDIDO (200) — a rota está aberta" ;;
  *)       bad "anônimo devolveu $s — esperado 401 (ou 503 sem segredo de JWT)" ;;
esac

# ── S2 — contraprova POSITIVA ─────────────────────────────────────────────────
head_ "S2 — rascunho VÁLIDO passa (sem tenant_id/created_at: são do STORE)"
R=$(preview "$JWT" "$VALIDA")
V=$(jqp "import json,sys;print(json.loads(sys.stdin.read()).get('valid'))" <<<"$R")
if [ "$V" = "True" ]; then ok "valid=true"; else
  bad "rascunho válido foi REPROVADO (valid=$V)"
  info "$(echo "$R" | cut -c1-300)"
fi

# ── S3 — inválida reprova ─────────────────────────────────────────────────────
head_ "S3 — id de nó repetido reprova, NOMEANDO o id"
DUP=$(echo "$VALIDA" | python3 -c "
import json,sys
d=json.loads(sys.stdin.read()); n=d['form']['nodes']
n.append(dict(n[1], output_key='outra'))       # mesmo id 'dados'
print(json.dumps(d))")
R=$(preview "$JWT" "$DUP")
echo "$R" | python3 -c "
import json,sys
b=json.loads(sys.stdin.read())
assert b.get('valid') is False, 'valid deveria ser false'
assert any(e.get('code')=='duplicate_node_id' and 'dados' in e.get('message','') for e in b.get('errors',[])), b.get('errors')
" 2>/dev/null && ok "reprovou nomeando 'dados'" || bad "id repetido NÃO foi reprovado (ou não nomeou o id)"

# ── S4 — o menu_prompt carrega statement E prompt ─────────────────────────────
head_ "S4 — menu_prompt junta o statement de abertura E o prompt da pergunta"
R=$(preview "$JWT" "$VALIDA")
MP=$(echo "$R" | jqp "import json,sys;print(json.loads(sys.stdin.read()).get('render',{}).get('menu_prompt',''))")
if [ "$MP" = "Vou registrar seu pedido.

Preencha os dados:" ]; then
  ok "menu_prompt traz os dois"
else
  bad "menu_prompt perdeu uma das partes — a regressão do '||' voltou"
  info "recebido: $(echo "$MP" | tr '\n' '/')"
fi

# ── S5 — fields e masked verbatim ─────────────────────────────────────────────
head_ "S5 — os campos chegam ao render, e o masked TIPADO não vira booleano"
echo "$R" | python3 -c "
import json,sys
r=json.loads(sys.stdin.read())['render']; f=r['fields']
assert [x['id'] for x in f]==['numero','cvv'], f
assert f[1]['masked']=='card_cvv', ('masked achatado', f[1]['masked'])
assert f[0]['masked'] is False, f[0]['masked']
" 2>/dev/null && ok "2 campos, 'cvv' com masked='card_cvv' verbatim" \
              || bad "fields ausentes ou masked achatado (=== true faria sair DESMASCARADO)"

# ── S6 — ancoragem em forma REAL ──────────────────────────────────────────────
head_ "S6 — o veredicto casa com a forma semeada dialog_limite_solicitacao"
FORMA=$(curl -s -m 10 "$DLG/v1/dialog/forms/dialog_limite_solicitacao?status=published" \
        -H "X-Tenant-ID: $TENANT" 2>/dev/null)
if [ -z "$FORMA" ] || [ "${FORMA:0:1}" != "{" ]; then
  inc "dialog-api não devolveu a forma — sem ancoragem em dado de produção"
else
  BODY=$(python3 -c "
import json,sys
d=json.loads(sys.stdin.read())
for k in ('tenant_id','created_at','updated_at','status','version','deleted_at'): d.pop(k,None)
print(json.dumps({'form':d}))" <<<"$FORMA")
  R=$(preview "$JWT" "$BODY")
  echo "$R" | python3 -c "
import json,sys
b=json.loads(sys.stdin.read())
assert b.get('valid') is True, b.get('errors')
f=b['render']['fields']
assert len(f)==4, ('esperados 4 campos', [x['id'] for x in f])
assert any(x['id']=='cvv' and x['masked'] for x in f), f
assert 'Preencha os dados' in b['render']['menu_prompt'], b['render']['menu_prompt']
" 2>/dev/null && ok "4 campos, cvv mascarado, prompt presente no menu_prompt" \
                || bad "a forma REAL não produz o render esperado"
fi

# -- S7 -- a ARVORE chega ao render, e a exigencia e DERIVADA -----------------
head_ "S7 — a subárvore sobrevive ao render, e options_tree é derivado"
ARVORE='{"form":{"form_id":"probe_arvore","name":"arvore","default_locale":"pt-BR",
 "locales":["pt-BR"],"dimensions":[],"tags":[],
 "nodes":[{"id":"q_motivo","kind":"question","prompt":"Motivo?","interaction":"list",
  "output_key":"motivo","timeout_s":300,
  "options":[{"id":"financeiro","label":"Financeiro","options":[
    {"id":"cobranca","label":"Cobranca","options":[{"id":"indevida","label":"Indevida"}]}]},
   {"id":"nao_se_aplica","label":"Nao se aplica"}]}]}}'
R=$(preview "$JWT" "$ARVORE")
if echo "$R" | python3 -c "
import json,sys
b=json.loads(sys.stdin.read())
assert b.get('valid') is True, b.get('errors')
r=b['render']
assert r.get('options_tree') is True, ('options_tree deveria ser True', r.get('options_tree'))
fin=[o for o in r['options'] if o['id']=='financeiro'][0]
assert fin.get('options'), ('a subarvore foi descartada', fin)
assert fin['options'][0]['options'][0]['id']=='indevida', fin
" 2>/dev/null; then
  ok "3 níveis chegam ao render, options_tree=true"
else
  bad "a subárvore não sobreviveu — escolher a PASTA viraria a resposta"
fi

# controle positivo: a mesma forma SEM aninhamento nao pode exigir arvore
PLANA=$(python3 -c "
import json,sys
d=json.loads(sys.stdin.read())
d['form']['nodes'][0]['options']=[{'id':'a','label':'A'},{'id':'b','label':'B'}]
print(json.dumps(d))" <<<"$ARVORE")
R=$(preview "$JWT" "$PLANA")
MT=$(jqp "import json,sys;print(json.loads(sys.stdin.read())['render']['options_tree'])" <<<"$R")
if [ "$MT" = "False" ]; then
  ok "forma plana: options_tree=false (controle positivo)"
else
  bad "options_tree=$MT numa forma SEM aninhamento — a exigência não é derivada"
fi
# ── veredicto ─────────────────────────────────────────────────────────────────
echo
if [ "$FAIL" -gt 0 ]; then
  echo "${RED}VERMELHO${RST} — $FAIL cenário(s) reprovaram."; exit 1
elif [ "$INC" -gt 0 ]; then
  echo "${YLW}INCONCLUSIVO${RST} — $INC cenário(s) sem amostra."; exit 0
else
  echo "${GRN}VERDE${RST} — o dry-run responde, e responde o mesmo que o form_get."
fi
