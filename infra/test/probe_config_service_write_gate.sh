#!/usr/bin/env bash
#
# probe_config_service_write_gate.sh — as portas de ESCRITA da calendar-api e da
# dialog-api estão fechadas, e as de LEITURA continuam abertas.
#
# POR QUE ESTE PROBE EXISTE
# =========================
# Medido em 2026-08-27, antes do conserto:
#   · calendar-api  `POST /v1/calendars` SEM credencial nenhuma → **201**, recurso criado.
#     `admin_token` existia em `config.py:41` e não era lido por rota alguma.
#   · dialog-api    `PLUGHUB_DIALOG_ADMIN_TOKEN` ausente no compose deixava
#     `_require_admin` INERTE (`if expected and ...`) — criar E publicar um DialogForm
#     anonimamente devolvia **200** nas duas chamadas.
#
# O que a escrita destrancada custava, para o probe não medir zelo abstrato: a janela
# em que um cliente pode ser contatado é decidida por `campaign.contact_calendar_id`
# (Outbound Fase 3a — `db_contact_eligibility` consulta `is_open` deste serviço).
# Reescrever ou apagar um calendário anonimamente ABRE essa janela. No dialog-api, o
# conteúdo dos formulários é o que o cliente lê e responde no survey e o que o agente
# preenche no wrap-up.
#
# O QUE ESTE PROBE PODE REPROVAR (a pergunta que todo verde tem de responder)
# ===========================================================================
#   S1/S6  a porta de escrita voltar a aceitar anônimo               → VERMELHO
#   S2/S7  o caminho de SISTEMA (admin-token) parar de funcionar     → VERMELHO
#          (é o que o seed usa; sem ele a base nova sobe sem forms)
#   S3/S8  quem TEM o grant deixar de conseguir escrever             → VERMELHO
#          (contraprova positiva: sem ela, um portão que recusa TUDO passaria)
#   S4     quem NÃO tem o grant conseguir escrever                   → VERMELHO
#          — e o código tem de ser 403, não 401: "não sei quem é" e "sei, e não pode"
#          são estados diferentes, e colapsá-los foi divergência medida entre serviços
#   S5/S9  a LEITURA/engine fechar junto                             → VERMELHO
#          (é a testemunha do outro lado: workflow-api, scheduler-api, mailing-api,
#          `form_get` e o survey web chamam sem credencial. Um portão que fecha a
#          leitura "passa" no teste de segurança e quebra o produto em silêncio.)
#
# INCONCLUSIVO é ramo próprio: sem serviço de pé ou sem login, o probe NÃO declara
# verde — um verde por ausência de amostra é a família "teste que não pode reprovar".
#
# Uso:  bash infra/test/probe_config_service_write_gate.sh
set -uo pipefail

CAL="${CAL_API:-http://localhost:3700}"
DLG="${DIALOG_API:-http://localhost:3760}"
AUTH="${AUTH_API:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
CAL_TOKEN="${CAL_ADMIN_TOKEN:-demo_calendar_admin_token}"
DLG_TOKEN="${DIALOG_ADMIN_TOKEN:-demo_dialog_admin_token}"

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
FAIL=0; INC=0
ok()   { echo "  ${GRN}✓${RST} $*"; }
bad()  { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }
inc()  { echo "  ${YLW}?${RST} $*"; INC=$((INC+1)); }
info() { echo "    $*"; }
head_() { echo; echo "${BLD}$*${RST}"; }

# `status` e não `code`: `code` é o CLI do VS Code, e a colisão de nomes engoliu a
# primeira medição deste arco inteira, imprimindo um download no lugar do HTTP status.
status() { curl -s -o /dev/null -m 10 -w "%{http_code}" "$@" 2>/dev/null || echo "000"; }

login() {  # $1=email $2=senha → imprime o access_token (vazio em falha)
  curl -s -m 10 -X POST "$AUTH/auth/login" \
    -H 'content-type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"$2\"}" 2>/dev/null \
    | sed -n 's/.*"access_token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
}

# Um NOME POR CENÁRIO. `uq_calendar` é UNIQUE em (organization_id, tenant_id, name),
# e reusar o nome fazia o cenário do Bearer colidir com o que o admin-token acabara de
# criar — o probe reprovava o produto por defeito próprio, e a mensagem ("portão
# recusando quem deveria passar") era convincente. Nome distinto por cenário elimina a
# dependência de ordem entre eles.
cal_body() { echo "{\"organization_id\":\"$TENANT\",\"tenant_id\":\"$TENANT\",\"name\":\"probe_write_gate_$1\",\"always_open\":true}"; }
JSONH=(-H 'content-type: application/json')

# ── pré-condição ──────────────────────────────────────────────────────────────
head_ "PRÉ-CONDIÇÃO — serviços de pé e principais disponíveis"
for pair in "calendar-api|$CAL/v1/health" "dialog-api|$DLG/v1/health"; do
  svc="${pair%%|*}"; url="${pair##*|}"
  s=$(status "$url")
  if [ "$s" = "200" ]; then ok "$svc responde ($url)"; else inc "$svc não respondeu ($s) — sem ele nada abaixo mede"; fi
done
if [ "$INC" -gt 0 ]; then
  echo; echo "${YLW}INCONCLUSIVO${RST} — serviço fora do ar; o probe não mediu."; exit 0
fi

ADMIN_JWT=$(login "admin@plughub.local" "changeme_admin")
OPER_JWT=$(login "operator@plughub.local" "changeme_operator")
[ -n "$ADMIN_JWT" ] && ok "login admin@ (tem config.calendars + config.dialog_forms)" \
                    || inc "login admin@ falhou — sem ele não há contraprova POSITIVA"
[ -n "$OPER_JWT" ]  && ok "login operator@ (não tem os grants)" \
                    || inc "login operator@ falhou — sem ele não há contraprova NEGATIVA"

# ── calendar-api ──────────────────────────────────────────────────────────────
head_ "CALENDAR-API — escrita fechada, engine aberto"

s=$(status -X POST "$CAL/v1/calendars" "${JSONH[@]}" -d "$(cal_body anon)")
if [ "$s" = "401" ]; then ok "S1 anônimo → 401 (media 201 antes do conserto)"
elif [ "$s" = "201" ]; then bad "S1 anônimo → 201 — A PORTA ESTÁ ABERTA"
                            info "config.admin_token vazio deixa o portão inerte; confira PLUGHUB_CALENDAR_ADMIN_TOKEN"
else bad "S1 anônimo → $s (esperado 401)"; fi

CREATED=""
if [ -n "$CAL_TOKEN" ]; then
  r=$(curl -s -m 10 -X POST "$CAL/v1/calendars" "${JSONH[@]}" -H "X-Admin-Token: $CAL_TOKEN" -d "$(cal_body sys)" 2>/dev/null)
  CREATED=$(echo "$r" | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
  if [ -n "$CREATED" ]; then ok "S2 admin-token → criou ($CREATED) — o caminho de SISTEMA vive"
  else bad "S2 admin-token NÃO criou — o seed e os smokes vão levar 401"; info "resposta: ${r:0:160}"; fi
fi

if [ -n "$ADMIN_JWT" ]; then
  r=$(curl -s -m 10 -X POST "$CAL/v1/calendars" "${JSONH[@]}" -H "Authorization: Bearer $ADMIN_JWT" -d "$(cal_body abac)" 2>/dev/null)
  id2=$(echo "$r" | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
  if [ -n "$id2" ]; then ok "S3 Bearer COM config.calendars → criou — o portão deixa passar quem pode"
    curl -s -m 10 -X DELETE "$CAL/v1/calendars/$id2" -H "X-Admin-Token: $CAL_TOKEN" >/dev/null 2>&1
  else bad "S3 Bearer com o grant NÃO criou — portão recusando quem deveria passar"; info "resposta: ${r:0:160}"; fi
else inc "S3 sem admin@ — contraprova positiva não exercida"; fi

if [ -n "$OPER_JWT" ]; then
  s=$(status -X POST "$CAL/v1/calendars" "${JSONH[@]}" -H "Authorization: Bearer $OPER_JWT" -d "$(cal_body nogrant)")
  if [ "$s" = "403" ]; then ok "S4 Bearer SEM o grant → 403 (autenticado e recusado)"
  elif [ "$s" = "401" ]; then bad "S4 devolveu 401, não 403 — colapsa 'não sei quem é' com 'sabe e não pode'"
  else bad "S4 Bearer sem grant → $s (esperado 403) — grant desnecessário para escrever"; fi
else inc "S4 sem operator@ — contraprova negativa não exercida"; fi

s=$(status "$CAL/v1/engine/is-open?tenant_id=$TENANT")
if [ "$s" = "200" ] || [ "$s" = "422" ]; then ok "S5 engine anônimo → $s (aberto — workflow/scheduler/mailing chamam sem credencial)"
else bad "S5 engine anônimo → $s — a LEITURA fechou junto; isto quebra o produto em silêncio"; fi

[ -n "$CREATED" ] && curl -s -m 10 -X DELETE "$CAL/v1/calendars/$CREATED" -H "X-Admin-Token: $CAL_TOKEN" >/dev/null 2>&1

# ── dialog-api ────────────────────────────────────────────────────────────────
head_ "DIALOG-API — escrita fechada, leitura aberta"

FORM_ID="dialog_probe_write_gate"
FORM="{\"form_id\":\"$FORM_ID\",\"name\":\"probe\",\"default_locale\":\"pt-BR\",\"locales\":[\"pt-BR\"],\"nodes\":[{\"id\":\"n1\",\"type\":\"statement\",\"text\":\"probe\"}]}"
TENH=(-H "X-Tenant-ID: $TENANT")

s=$(status -X POST "$DLG/v1/dialog/forms" "${JSONH[@]}" "${TENH[@]}" -d "$FORM")
if [ "$s" = "401" ]; then ok "S6 criar anônimo → 401 (media 200 antes do conserto)"
elif [ "$s" = "200" ] || [ "$s" = "201" ]; then bad "S6 criar anônimo → $s — A PORTA ESTÁ ABERTA"
else bad "S6 criar anônimo → $s (esperado 401)"; fi

s=$(status -X POST "$DLG/v1/dialog/forms/$FORM_ID/publish" "${TENH[@]}")
if [ "$s" = "401" ]; then ok "S7 publicar anônimo → 401"
elif [ "$s" = "200" ]; then bad "S7 publicar anônimo → 200 — publicação aberta"
else bad "S7 publicar anônimo → $s (esperado 401)"; fi

if [ -n "$ADMIN_JWT" ]; then
  s=$(status -X POST "$DLG/v1/dialog/forms" "${JSONH[@]}" "${TENH[@]}" -H "Authorization: Bearer $ADMIN_JWT" -d "$FORM")
  if [ "$s" = "200" ] || [ "$s" = "201" ]; then ok "S8 Bearer COM config.dialog_forms → $s — o editor da UI continua salvando"
  else bad "S8 Bearer com o grant → $s — o editor de DialogForms está quebrado"; fi
fi

# S8b existe porque o dialog-seed depende EXATAMENTE deste caminho: sem ele, uma base
# nova sobe sem formulário nenhum — o NPS de fim-de-contato cai no `on_failure` do
# `form_get` e o wrap-up abre o painel vazio. O S10 conta os forms já semeados, o que
# prova o passado; este prova que o caminho AINDA funciona.
s=$(status -X POST "$DLG/v1/dialog/forms" "${JSONH[@]}" "${TENH[@]}" -H "X-Admin-Token: $DLG_TOKEN" -d "$FORM")
if [ "$s" = "200" ] || [ "$s" = "201" ]; then ok "S8b admin-token → $s — o caminho do dialog-seed vive"
elif [ "$s" = "401" ]; then bad "S8b admin-token → 401 — o seed vai levar 401; DIALOG_ADMIN_TOKEN do dialog-seed precisa espelhar PLUGHUB_DIALOG_ADMIN_TOKEN"
else bad "S8b admin-token → $s (esperado 200/201)"; fi

# ⚠️ Limpeza pelo BANCO, e agora por um motivo DIFERENTE do original. A rota `DELETE`
# passou a existir (2026-08-28, ADR adr-dialog-form-deletion), mas ela ARQUIVA o que já
# foi publicado — e o S8b acima PUBLICA este form de propósito. Pela API o resíduo seria
# uma linha arquivada por execução; pelo banco não sobra nada. A alternativa correta, se
# um dia esta limpeza incomodar, é o probe parar de publicar (form nunca publicado é
# PURGADO pela própria rota, sem tocar no Postgres) — não é reabrir a decisão.
docker exec plughub-demo-postgres-1 psql -U plughub -d plughub_demo -q \
  -c "DELETE FROM dialog.forms WHERE form_id = '$FORM_ID';" >/dev/null 2>&1 \
  && info "limpeza: form de probe removido" \
  || info "limpeza NÃO executada (postgres fora de alcance) — verifique $FORM_ID à mão"

s=$(status "$DLG/v1/dialog/forms" "${TENH[@]}")
if [ "$s" = "200" ]; then ok "S9 leitura anônima → 200 (form_get do mcp-server e survey web dependem disto)"
else bad "S9 leitura anônima → $s — fechou a leitura; NPS e wrap-up caem no on_failure"; fi

# testemunha de PRESENÇA: o seed rodou com o token e os forms de produção existem
n=$(curl -s -m 10 "$DLG/v1/dialog/forms" "${TENH[@]}" 2>/dev/null | grep -o '"form_id"' | wc -l)
if [ "${n:-0}" -ge 3 ]; then ok "S10 $n forms no tenant — o dialog-seed atravessou o portão novo"
else bad "S10 só ${n:-0} form(s) — sinal de que o seed levou 401; DIALOG_ADMIN_TOKEN do dialog-seed precisa espelhar PLUGHUB_DIALOG_ADMIN_TOKEN"; fi

# ── veredicto ─────────────────────────────────────────────────────────────────
echo
if [ "$FAIL" -gt 0 ]; then echo "${RED}${BLD}GATE VERMELHO${RST} — $FAIL falha(s)"; exit 1; fi
if [ "$INC" -gt 0 ]; then echo "${YLW}${BLD}INCONCLUSIVO${RST} — $INC cenário(s) não exercido(s)"; exit 0; fi
echo "${GRN}${BLD}GATE VERDE${RST} — escrita fechada nos dois; leitura e engine intactos"
