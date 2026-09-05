#!/usr/bin/env bash
# probe_agent_event_epochs.sh
#
# D13 do `adr-dialog-tree-options` — `/reports/agent-events/epochs` e o recorte do
# `/tree` por `form_id`.
#
# A PROPOSICAO CENTRAL nao e "o endpoint responde": e que **recortar por epoca faz o
# conflito de vocabulario deixar de existir**, em vez de ser avisado. Por isso o ramo
# C compara a arvore RECORTADA com a NAO recortada e exige que a recortada seja
# `single_vocabulary: true` — se as duas forem iguais, o recorte nao esta recortando e
# o endpoint virou decoracao.
#
# ⚠️ O ramo D guarda a armadilha do `if not x`: `form_id=` (string VAZIA) e a epoca
# ANTERIOR ao carimbo, uma epoca legitima — e NAO "sem filtro". Um `if form_id:` no
# servidor colapsaria as duas, e a unica forma de ver isso e comparar as contagens.
set -u

API="${PLUGHUB_ANALYTICS_URL:-http://localhost:3500}"
TOK="${ANALYTICS_SERVICE_TOKEN:-changeme_analytics_service_token_demo}"
TENANT="${PLUGHUB_TENANT_ID:-tenant_demo}"
ROOT="${PLUGHUB_TREE_ROOT:-retencao_humano.wrapup.servico}"

FAIL=0
INCONC=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=1; }
info() { printf '  \033[33m•\033[0m %s\n' "$1"; }

get() { curl -s "$1" -H "X-Service-Token: ${TOK}"; }

printf '\033[1mprobe_agent_event_epochs — recortar por epoca mata o conflito\033[0m\n\n'

EP=$(get "${API}/reports/agent-events/epochs?tenant_id=${TENANT}&root=${ROOT}")

printf '\033[1mA — o endpoint devolve epocas\033[0m\n'
N=$(printf '%s' "$EP" | python3 -c 'import sys,json;print(len(json.load(sys.stdin).get("data") or []))' 2>/dev/null)
if [ -z "$N" ]; then
  bad "resposta ilegivel — INCONCLUSIVO"
  printf '\n\033[31m\033[1mINCONCLUSIVO\033[0m\n'; exit 1
fi
if [ "$N" = "0" ]; then
  info "nenhuma epoca sob ${ROOT} — SEM AMOSTRA"
  printf '\n\033[33m\033[1mINCONCLUSIVO\033[0m — sem populacao para julgar.\n'; exit 1
fi
ok "${N} epoca(s) sob ${ROOT}"

# ── B: as epocas particionam os eventos (nada perdido, nada contado duas vezes) ──
printf '\n\033[1mB — as epocas PARTICIONAM: a soma delas e o total da janela\033[0m\n'
TOTAL=$(get "${API}/reports/agent-events/tree?tenant_id=${TENANT}&root=${ROOT}" \
        | python3 -c '
import sys, json
d = json.load(sys.stdin)
raiz = [r for r in d["data"] if r["prefix"].count(".") == d["meta"]["root"].count(".")]
print(raiz[0]["branch_marks"] if raiz else 0)' 2>/dev/null)
SOMA=$(printf '%s' "$EP" | python3 -c 'import sys,json;print(sum(e["events"] for e in json.load(sys.stdin)["data"]))' 2>/dev/null)
if [ "${TOTAL:-x}" = "${SOMA:-y}" ]; then
  ok "soma das epocas = ${SOMA} = total da janela"
else
  bad "soma das epocas (${SOMA}) != total da janela (${TOTAL}) — evento perdido ou contado duas vezes"
fi

# ── C: a arvore RECORTADA por epoca carimbada fala UM vocabulario ────────────
printf '\n\033[1mC — recortar por epoca CARIMBADA elimina a mistura\033[0m\n'
FID=$(printf '%s' "$EP" | python3 -c '
import sys, json
c = [e for e in json.load(sys.stdin)["data"] if e.get("stamped")]
print(c[0]["form_id"] if c else "")' 2>/dev/null)
if [ -z "$FID" ]; then
  info "nenhuma epoca CARIMBADA na janela — a proposicao central nao foi exercida"
  info "rode um wrap-up (o carimbo entrou em 2026-09-05) e repita"
  INCONC=1
else
  UNI=$(get "${API}/reports/agent-events/tree?tenant_id=${TENANT}&root=${ROOT}&form_id=${FID}" \
        | python3 -c 'import sys,json;print(json.load(sys.stdin)["meta"]["single_vocabulary"])' 2>/dev/null)
  SEM=$(get "${API}/reports/agent-events/tree?tenant_id=${TENANT}&root=${ROOT}" \
        | python3 -c 'import sys,json;print(json.load(sys.stdin)["meta"]["single_vocabulary"])' 2>/dev/null)
  if [ "$UNI" = "True" ] && [ "$SEM" = "False" ]; then
    ok "recortada em ${FID}: vocabulario unico; sem recorte: misturada — o recorte RECORTA"
  elif [ "$UNI" = "True" ] && [ "$SEM" = "True" ]; then
    info "a janela inteira ja fala um vocabulario — o recorte nao teve o que separar"
    INCONC=1
  else
    bad "recorte por epoca NAO produziu vocabulario unico (recortada=${UNI}, sem recorte=${SEM})"
  fi
fi

# ── D: `form_id=` VAZIO e a epoca sem carimbo, nao "sem filtro" ──────────────
printf '\n\033[1mD — form_id vazio e a epoca SEM CARIMBO, nao ausencia de filtro\033[0m\n'
VAZIO=$(get "${API}/reports/agent-events/tree?tenant_id=${TENANT}&root=${ROOT}&form_id=" \
        | python3 -c '
import sys, json
d = json.load(sys.stdin)
r = [x for x in d["data"] if x["prefix"] == d["meta"]["root"]]
print(r[0]["branch_marks"] if r else 0)' 2>/dev/null)
SEMFILTRO=$(get "${API}/reports/agent-events/tree?tenant_id=${TENANT}&root=${ROOT}" \
        | python3 -c '
import sys, json
d = json.load(sys.stdin)
r = [x for x in d["data"] if x["prefix"] == d["meta"]["root"]]
print(r[0]["branch_marks"] if r else 0)' 2>/dev/null)
if [ "${VAZIO:-0}" = "0" ]; then
  info "nenhum evento sem carimbo na janela — ramo D sem amostra"
elif [ "$VAZIO" = "$SEMFILTRO" ]; then
  bad "form_id vazio devolveu o MESMO que sem filtro (${VAZIO}) — o servidor esta usando truthiness"
else
  ok "sem carimbo=${VAZIO} != sem filtro=${SEMFILTRO} — a string vazia FILTRA"
fi

# ────────────────────────────────────────────────────────────────────────────
# E: a epoca CONTEM os proprios eventos (o recorte que a TELA faz)
#
# Os ramos acima recortam so por `form_id`. A lente recorta pelos limites EXATOS da
# epoca — `from_dt`/`to_dt` como o endpoint os devolveu —, e era ali que o defeito
# vivia: `emitted_at` e `DateTime64(3)`, o servidor truncava o limite para o segundo
# inteiro, e o limite SUPERIOR andava para TRAS. A epoca carimbada (dois eventos no
# mesmo milissegundo) devolvia arvore VAZIA logo abaixo do proprio cabecalho dizendo
# quantas marcacoes tinha.
#
# O gate passou VERDE nos dois estados — antes e depois do conserto — porque media
# uma proposicao VIZINHA: *"o form_id filtra"* em vez de *"a epoca contem os seus"*.
# E o catalogo: instrumento falseavel e honesto que julga a pergunta errada.
printf '\n\033[1mE - a epoca contem os PROPRIOS eventos (recorte da tela)\033[0m\n'
FALHAS=0
VISTAS=0
while IFS="|" read -r EFID EFROM ETO EEV; do
  [ -z "$EFROM" ] && continue
  # CR fora: quem gera a lista pode ser um python de Windows sobre este mesmo mount
  # (`os.linesep`), e um \r invisivel no ultimo campo faz a comparacao numerica
  # falhar com os DOIS lados imprimindo o mesmo numero — vermelho ilegivel.
  EEV=$(printf '%s' "$EEV" | tr -d '\r')
  VISTAS=$((VISTAS+1))
  MARCAS=$(get "${API}/reports/agent-events/tree?tenant_id=${TENANT}&root=${ROOT}&from_dt=${EFROM}&to_dt=${ETO}&form_id=${EFID}" \
          | python3 -c '
import sys, json
d = json.load(sys.stdin)
r = [x for x in d["data"] if x["prefix"] == d["meta"]["root"]]
print(r[0]["branch_marks"] if r else 0)' 2>/dev/null)
  ROTULO="${EFID:-<sem carimbo>}"
  if [ "${MARCAS:-0}" = "0" ]; then
    bad "epoca ${ROTULO} diz ter ${EEV} evento(s), mas a arvore nos SEUS limites veio VAZIA"
    FALHAS=$((FALHAS+1))
  elif [ "$MARCAS" != "$EEV" ]; then
    bad "epoca ${ROTULO}: ${EEV} evento(s) declarado(s), ${MARCAS} na arvore dos seus limites"
    FALHAS=$((FALHAS+1))
  fi
done <<EOF
$(printf "%s" "$EP" | python3 -c '
import sys, json
from urllib.parse import quote
# O `+00:00` do ISO tem de viajar CODIFICADO: `+` cru na query string decodifica
# como ESPACO, e o servidor cai no default de janela — o proprio gate produziria
# o vermelho que ele existe para detectar, pela razao errada.
for e in json.load(sys.stdin)["data"]:
    print("%s|%s|%s|%s" % (e["form_id"], quote(e["from_dt"]), quote(e["to_dt"]), e["events"]))')
EOF
if [ "$VISTAS" = "0" ]; then
  info "nenhuma epoca para conferir — ramo E sem amostra"
elif [ "$FALHAS" = "0" ]; then
  ok "as ${VISTAS} epoca(s) contem os proprios eventos nos proprios limites"
fi

printf '\n'
if [ "$FAIL" != "0" ]; then
  printf '\033[31m\033[1mVERMELHO\033[0m\n'; exit 1
elif [ "$INCONC" != "0" ]; then
  printf '\033[33m\033[1mINCONCLUSIVO\033[0m — a proposicao central nao teve caso.\n'; exit 1
else
  printf '\033[32m\033[1mVERDE\033[0m — a epoca recorta, particiona, e o vazio filtra.\n'; exit 0
fi
