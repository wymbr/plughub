#!/usr/bin/env bash
#
# probe_dialog_form_delete.sh — o DELETE do dialog-api ARQUIVA (reversível) e só PURGA o
# que nunca foi publicado; o catálogo fecha e a RESOLUÇÃO por id continua aberta.
#
# POR QUE ESTE PROBE EXISTE
# =========================
# A decisão (ADR `docs/adr/adr-dialog-form-deletion.md`) separa dois eixos que a palavra
# "soft-delete" funde:
#
#   ARMAZENAMENTO — "dá para recuperar?"   → a linha fica, com `deleted_at`
#   LEITURA       — "o contato em andamento cai?" → `GET /{form_id}` CONTINUA servindo
#
# Escolher o primeiro e fechar o segundo quebraria os SEIS leitores exatamente como um
# hard delete, só que com um backup que ninguém consulta. Dois deles leem no FIM do
# diálogo (`survey_record` compõe a nota; `segment_outcome_record` deriva os eventos
# Arc 12) e um lê HISTÓRIA JÁ ENCERRADA (`WebhookSegmentDetail`, dano permanente). E o
# `seed_dialog` trata 404 como AUSENTE: com a resolução fechada, todo boot ressuscitaria
# o form arquivado.
#
# O QUE ESTE PROBE PODE REPROVAR (a pergunta que todo verde tem de responder)
# ===========================================================================
#   S3  arquivado deixar de ser resolvível por id            → VERMELHO
#       É A TESTEMUNHA NEGATIVA. Um probe que só checasse "sumiu da lista" ficaria
#       VERDE num hard delete — é esta asserção, e só ela, que separa os dois.
#   S4  o catálogo parar de esconder o arquivado             → VERMELHO
#   S5  a lixeira (`?include_deleted`) parar de mostrá-lo    → VERMELHO
#   S6  escrita sobre arquivado deixar de ser recusada       → VERMELHO
#       (ressuscitar por escrita faz um slot antigo executar conteúdo novo em silêncio)
#   S7  o restauro não devolver o form ao catálogo/escrita   → VERMELHO
#   S8  form NUNCA publicado deixar de ser purgado           → VERMELHO
#       (vira lixo permanente; era o motivo original do item no TODO)
#   S9  `survey_link_create` aceitar form arquivado          → VERMELHO
#       (é o ÚNICO ponto que cria vínculo NOVO — sem esta guarda o arquivamento é
#       contornado toda vez que alguém dispara um survey outbound)
#   S10 testemunha de PRESENÇA: form vivo continua na lista e resolvível → VERMELHO
#       Sem ela, um backend que respondesse 404 a tudo passaria em S4 e S8.
#
# INCONCLUSIVO é ramo próprio: sem serviço de pé, o probe NÃO declara verde.
#
# RESÍDUO ESPERADO: este probe deixa UM form arquivado (`dialog_probe_delete_v1`) e o
# REUSA a cada execução (o setup restaura antes de republicar) — porque um form que já
# publicou não pode ser purgado, por decisão. O outro (`dialog_probe_purge_v1`) some.
#
# Uso:  bash infra/test/probe_dialog_form_delete.sh
set -uo pipefail

DLG="${DIALOG_API:-http://localhost:3760}"
CGW="${CHANNEL_GATEWAY:-http://localhost:8010}"
TENANT="${TENANT:-tenant_demo}"
DLG_TOKEN="${DIALOG_ADMIN_TOKEN:-demo_dialog_admin_token}"

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
FAIL=0; INC=0
ok()   { echo "  ${GRN}✓${RST} $*"; }
bad()  { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }
inc()  { echo "  ${YLW}?${RST} $*"; INC=$((INC+1)); }
info() { echo "    $*"; }
head_() { echo; echo "${BLD}$*${RST}"; }

# `status` e não `code` — `code` é o CLI do VS Code (a colisão já engoliu uma medição
# inteira neste diretório, imprimindo um download no lugar do HTTP status).
status() { curl -s -o /dev/null -m 10 -w "%{http_code}" "$@" 2>/dev/null || echo "000"; }
body()   { curl -s -m 10 "$@" 2>/dev/null; }

AUTHH=(-H "X-Admin-Token: $DLG_TOKEN")
TENH=(-H "X-Tenant-ID: $TENANT")
JSONH=(-H 'content-type: application/json')

KEEP="dialog_probe_delete_v1"     # publicado → arquivável, nunca purgável (reusado)
PURGE="dialog_probe_purge_v1"     # nunca publicado → purgável (some no fim)

form_body() {  # $1 = form_id
  cat <<JSON
{"form_id":"$1","name":"probe delete","default_locale":"pt-BR","locales":["pt-BR"],
 "nodes":[{"id":"q1","kind":"question","prompt":"ok?","interaction":"text","output_key":"v"}]}
JSON
}

# jq não é dependência garantida nos hosts que rodam os probes; grep -o basta para
# campos escalares e não inventa valor quando o campo falta (que é o ponto).
has_field() { echo "$1" | grep -q "\"$2\""; }
field_is_null() { echo "$1" | grep -qE "\"$2\"[[:space:]]*:[[:space:]]*null"; }

# ── pré-condição ──────────────────────────────────────────────────────────────
head_ "PRÉ-CONDIÇÃO — dialog-api de pé e caminho de sistema vivo"
s=$(status "$DLG/v1/health")
if [ "$s" = "200" ]; then ok "dialog-api responde ($DLG)"
else inc "dialog-api não respondeu ($s) — sem ele nada abaixo mede"; fi
if [ "$INC" -gt 0 ]; then
  echo; echo "${YLW}INCONCLUSIVO${RST} — serviço fora do ar; o probe não mediu."; exit 0
fi

# ── setup ─────────────────────────────────────────────────────────────────────
head_ "SETUP — um form publicado (reusado) e um nunca publicado"

# Restaura antes de republicar: a execução anterior deixou o KEEP arquivado, e escrita
# sobre arquivado é 409 por decisão (S6). Restaurar aqui exercita o undelete todo dia.
status -X POST "$DLG/v1/dialog/forms/$KEEP/undelete" "${AUTHH[@]}" "${TENH[@]}" >/dev/null

s=$(status "$DLG/v1/dialog/forms/$KEEP?status=published" "${TENH[@]}")
if [ "$s" != "200" ]; then
  s=$(status -X POST "$DLG/v1/dialog/forms" "${AUTHH[@]}" "${TENH[@]}" "${JSONH[@]}" -d "$(form_body "$KEEP")")
  [ "$s" = "200" ] || [ "$s" = "201" ] || { inc "não criou $KEEP ($s) — admin-token?"; }
  s=$(status -X POST "$DLG/v1/dialog/forms/$KEEP/publish" "${AUTHH[@]}" "${TENH[@]}")
  [ "$s" = "200" ] || [ "$s" = "201" ] || { inc "não publicou $KEEP ($s)"; }
fi
if [ "$INC" -gt 0 ]; then
  echo; echo "${YLW}INCONCLUSIVO${RST} — setup não completou; o probe não mediu."; exit 0
fi
ok "S1 $KEEP publicado (ou já estava)"

status -X POST "$DLG/v1/dialog/forms" "${AUTHH[@]}" "${TENH[@]}" "${JSONH[@]}" -d "$(form_body "$PURGE")" >/dev/null
s=$(status "$DLG/v1/dialog/forms/$PURGE" "${TENH[@]}")
if [ "$s" = "200" ]; then ok "S2 $PURGE criado como rascunho (nunca publicado)"
else bad "S2 $PURGE não existe após o POST ($s)"; fi

# ── arquivamento de form publicado ────────────────────────────────────────────
head_ "ARQUIVAR (publicado) — catálogo fecha, resolução NÃO"

r=$(body -X DELETE "$DLG/v1/dialog/forms/$KEEP" "${AUTHH[@]}" "${TENH[@]}")
if echo "$r" | grep -qE '"purged"[[:space:]]*:[[:space:]]*false'; then
  ok "DELETE devolveu purged=false — publicado nunca é apagado de verdade"
else bad "DELETE de form publicado não declarou purged=false"; info "resposta: ${r:0:200}"; fi

r=$(body "$DLG/v1/dialog/forms/$KEEP?status=published" "${TENH[@]}")
s=$(status "$DLG/v1/dialog/forms/$KEEP?status=published" "${TENH[@]}")
if [ "$s" = "200" ] && has_field "$r" "deleted_at" && ! field_is_null "$r" "deleted_at"; then
  ok "S3 resolução de arquivado → 200 COM deleted_at ${BLD}(testemunha negativa)${RST}"
elif [ "$s" = "404" ]; then
  bad "S3 resolução de arquivado → 404 — isto É um hard delete disfarçado"
  info "derruba contato em andamento, composição de nota, história encerrada; e o"
  info "seed_dialog vai RESSUSCITAR o form no próximo boot (trata 404 como ausente)"
else
  bad "S3 resolução → $s / deleted_at ausente ou nulo"; info "resposta: ${r:0:200}"
fi

r=$(body "$DLG/v1/dialog/forms" "${TENH[@]}")
if echo "$r" | grep -q "\"$KEEP\""; then bad "S4 arquivado ainda aparece no catálogo"
else ok "S4 catálogo esconde o arquivado (é o que corrige o combo de deploy sem código próprio)"; fi

r=$(body "$DLG/v1/dialog/forms?include_deleted=true" "${TENH[@]}")
if echo "$r" | grep -q "\"$KEEP\""; then ok "S5 lixeira (?include_deleted=true) mostra o arquivado"
else bad "S5 lixeira NÃO mostra o arquivado — não há como restaurá-lo pela tela"; fi

s=$(status -X PUT "$DLG/v1/dialog/forms/$KEEP" "${AUTHH[@]}" "${TENH[@]}" "${JSONH[@]}" -d "$(form_body "$KEEP")")
if [ "$s" = "409" ]; then ok "S6a PUT sobre arquivado → 409"
else bad "S6a PUT sobre arquivado → $s (esperado 409) — escrita ressuscita em silêncio"; fi

s=$(status -X POST "$DLG/v1/dialog/forms/$KEEP/publish" "${AUTHH[@]}" "${TENH[@]}")
if [ "$s" = "409" ]; then ok "S6b publish sobre arquivado → 409"
else bad "S6b publish sobre arquivado → $s (esperado 409)"; fi

# ── borda que cria VÍNCULO NOVO (D4) ──────────────────────────────────────────
head_ "SURVEY LINK — a única borda que recusa arquivado"

s=$(status "$CGW/health")
if [ "$s" != "200" ]; then
  inc "channel-gateway não respondeu ($s) — S9 não exercido"
else
  s=$(status -X POST "$CGW/v1/survey/web/create" "${JSONH[@]}" \
        -d "{\"tenant_id\":\"$TENANT\",\"form_id\":\"$KEEP\"}")
  if [ "$s" = "409" ]; then ok "S9 survey_link_create sobre arquivado → 409"
  elif [ "$s" = "201" ]; then
    bad "S9 criou link sobre form ARQUIVADO — o arquivamento foi contornado"
    info "com a resolução aberta (S3), este ponto SUCEDE se ninguém puser a guarda:"
    info "o form fica congelado num token por dias"
  else bad "S9 survey_link_create → $s (esperado 409)"; fi
fi

# ── restauro ──────────────────────────────────────────────────────────────────
head_ "RESTAURAR — volta ao catálogo e à escrita"

r=$(body -X POST "$DLG/v1/dialog/forms/$KEEP/undelete" "${AUTHH[@]}" "${TENH[@]}")
if echo "$r" | grep -qE '"was_deleted"[[:space:]]*:[[:space:]]*true'; then
  ok "S7a undelete devolveu was_deleted=true"
else bad "S7a undelete não confirmou o restauro"; info "resposta: ${r:0:200}"; fi

r=$(body "$DLG/v1/dialog/forms" "${TENH[@]}")
if echo "$r" | grep -q "\"$KEEP\""; then ok "S7b restaurado volta ao catálogo"
else bad "S7b restaurado NÃO voltou ao catálogo"; fi

s=$(status -X PUT "$DLG/v1/dialog/forms/$KEEP" "${AUTHH[@]}" "${TENH[@]}" "${JSONH[@]}" -d "$(form_body "$KEEP")")
if [ "$s" = "200" ]; then ok "S7c escrita liberada após o restauro"
else bad "S7c PUT após restauro → $s (esperado 200) — o 409 ficou grudado"; fi

# ── purga do nunca-publicado ──────────────────────────────────────────────────
head_ "PURGAR (nunca publicado) — apagar de verdade, porque nada pode estar vinculado"

r=$(body -X DELETE "$DLG/v1/dialog/forms/$PURGE" "${AUTHH[@]}" "${TENH[@]}")
if echo "$r" | grep -qE '"purged"[[:space:]]*:[[:space:]]*true'; then
  ok "S8a DELETE devolveu purged=true"
else bad "S8a DELETE de form nunca publicado não declarou purged=true"; info "resposta: ${r:0:200}"; fi

s=$(status "$DLG/v1/dialog/forms/$PURGE" "${TENH[@]}")
if [ "$s" = "404" ]; then ok "S8b purgado some da resolução (404) — sem resíduo no tenant"
else bad "S8b purgado ainda resolve → $s; o form virou lixo permanente"; fi

# ── testemunha de PRESENÇA ────────────────────────────────────────────────────
head_ "TESTEMUNHA DE PRESENÇA — um form vivo não foi afetado"

r=$(body "$DLG/v1/dialog/forms" "${TENH[@]}")
n=$(echo "$r" | grep -o '"form_id"' | wc -l)
if [ "${n:-0}" -ge 2 ]; then ok "S10a catálogo lista ${n} form(s) vivos"
else bad "S10a catálogo com ${n:-0} form(s) — um backend que escondesse TUDO passaria em S4"; fi

s=$(status "$DLG/v1/dialog/forms/dialog_nps_buttons?status=published" "${TENH[@]}")
if [ "$s" = "200" ]; then ok "S10b form de produção segue resolvível (dialog_nps_buttons)"
elif [ "$s" = "404" ]; then inc "S10b dialog_nps_buttons ausente — seed não rodou neste ambiente"
else bad "S10b resolução de form vivo → $s"; fi

# ── estado final ──────────────────────────────────────────────────────────────
# Deixa o KEEP arquivado de propósito: é o estado que a próxima execução espera, e é o
# que impede o probe de acumular um form por rodada.
status -X DELETE "$DLG/v1/dialog/forms/$KEEP" "${AUTHH[@]}" "${TENH[@]}" >/dev/null
info "estado final: $KEEP arquivado (reusado na próxima execução), $PURGE apagado"

# ── veredicto ─────────────────────────────────────────────────────────────────
echo
if [ "$FAIL" -gt 0 ]; then echo "${RED}${BLD}GATE VERMELHO${RST} — $FAIL falha(s)"; exit 1; fi
if [ "$INC" -gt 0 ]; then echo "${YLW}${BLD}INCONCLUSIVO${RST} — $INC cenário(s) não exercido(s)"; exit 0; fi
echo "${GRN}${BLD}GATE VERDE${RST} — arquiva reversível, purga o nunca-publicado, resolução intacta"
