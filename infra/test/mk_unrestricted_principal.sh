#!/usr/bin/env bash
# ==============================================================================
# mk_unrestricted_principal.sh — o principal que enxerga o tenant INTEIRO
# ==============================================================================
#
# Tres gates deste repositorio comparam um agregado da API contra um ledger lido
# DIRETO, logo so fecham sob um principal que veja tudo: `gate_sla_segment_target.sh`,
# `gate_queue_report_per_wait.sh` e `probe_process_chip_scoped_marker.sh`.
#
# ── O QUE MUDOU, e por que este arquivo estava produzindo o OPOSTO do que promete ──
#
# Ele nasceu no passo 2 (2026-08-27) criando `unrestricted: true` COM lista VAZIA — na
# epoca, o unico arranjo que sobreviveria a inversao do passo 3. O passo 3 aconteceu,
# e com ele DUAS decisoes do dono que mataram esse arranjo:
#
#   · AUT-13 — o claim `unrestricted` deixou de ser cunhado no token;
#   · AUT-03 — `accessible_pools: []` passou a significar **NENHUM pool**.
#
# Juntas, elas transformaram "irrestrito por declaracao" em "escopo vazio". O usuario
# continuava com a flag `true` na linha do banco, o nome do arquivo continuava dizendo
# IRRESTRITO, e o principal via **zero pools**. Medido em 2026-08-31 pelo
# `probe_unrestricted_claim.sh`: *"pools distintos: irrestrito=0 escopado=2"* — o
# suposto irrestrito enxergava MENOS que um operador comum, e os tres gates
# dependentes saiam VERMELHO/INCONCLUSIVO sem que ninguem ligasse uma coisa a outra.
#
# ⚠️ Este e o modo de falha mais caro de um helper de teste: ele **entrega**, com HTTP
# 200 e um nome tranquilizador, um principal que nao serve — e o gate que o consome
# reprova por um motivo que parece dele. O nome do arquivo virou a documentacao
# errada, e o campo `unrestricted` saiu do produto inteiro na AUT-15.
#
# ── O arranjo de HOJE: enumerar ────────────────────────────────────────────────
#
# A decisao do dono e que **escopo de pool e sempre enumerado** — nao ha declaracao de
# "sem recorte" para usuario. Entao "ver o tenant inteiro" e literalmente ter todos os
# pools do tenant na lista, e este script os LE do agent-registry em vez de manter uma
# copia que envelhece: pool novo entra sozinho na proxima execucao.
#
# ⚠️ O que isso NAO e: um principal `accessible_pools is None`. Esse existe, e e o de
# SERVICO (`X-Service-Token`) — mas so a analytics-api o aceita, e ele nao passa pelo
# login, entao nao serve a gate que precise de um usuario. Sao duas coisas diferentes
# com o mesmo efeito de leitura, e confundi-las e como este arquivo se perdeu.
#
# Idempotente: 409 -> PATCH garantindo o estado.
# ==============================================================================
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/_auth.sh"

REGISTRY="${REGISTRY_URL:-http://localhost:3300}"
TOK="$(plughub_token)"
EMAIL="${UNRESTRICTED_EMAIL:-probe@plughub.local}"
PASS="${UNRESTRICTED_PASS:-changeme_probe}"

# ── a lista de pools do tenant, lida da fonte ────────────────────────────────
# ⚠️ O agent-registry filtra tenant pelo HEADER `x-tenant-id`, NAO por query param —
# `?tenant_id=` sai 200 com `{"pools":[],"total":0}`, indistinguivel de "o tenant nao tem
# pool". Quem denunciou foi o ramo INCONCLUSIVO abaixo, na PRIMEIRA execucao: sem ele o
# helper teria criado um principal de escopo vazio outra vez, com HTTP 200 e nome de
# irrestrito — o mesmo defeito que ele passou a existir para nao repetir.
POOLS_JSON="$(curl -s -H "x-tenant-id: $TENANT" "$REGISTRY/v1/pools?limit=500" \
  | jq -c '[ (if type=="array" then .[] else (.pools // [])[] end) | .pool_id // .id | select(. != null) ]' 2>/dev/null)"
N_POOLS="$(printf '%s' "${POOLS_JSON:-[]}" | jq 'length' 2>/dev/null || echo 0)"
if [ "${N_POOLS:-0}" -eq 0 ]; then
  # Sem a lista, criar o principal seria pior que nao criar: ele nasceria com escopo
  # VAZIO e o gate consumidor reprovaria por um motivo que parece dele — exatamente o
  # defeito que este arquivo passou a existir para nao repetir.
  echo "INCONCLUSIVO: agent-registry nao devolveu pools em $REGISTRY (tenant=$TENANT)."
  echo "  Sem a lista nao ha como enumerar, e um principal de escopo vazio seria uma"
  echo "  armadilha com nome de irrestrito."
  exit 2
fi
echo "--- pools do tenant: $N_POOLS (enumerados a partir do agent-registry) ---"

echo "--- criando $EMAIL ---"
BODY="$(jq -nc --arg t "$TENANT" --arg e "$EMAIL" --arg p "$PASS" --argjson pools "$POOLS_JSON" \
  '{tenant_id:$t, email:$e, password:$p, name:"Probe - ve o tenant inteiro",
    roles:["admin"], accessible_pools:$pools}')"
RESP="$(curl -s -w '\n%{http_code}' -X POST "$AUTH/users" \
  -H 'content-type: application/json' -H "Authorization: Bearer $TOK" -d "$BODY")"
CODE="$(printf '%s' "$RESP" | tail -1)"
echo "  HTTP $CODE"

if [ "$CODE" = "409" ]; then
  echo "  ja existe — reenumerando os pools (pool novo entra aqui)"
  UID_P="$(curl -s "$AUTH/users?tenant_id=$TENANT" -H "Authorization: Bearer $TOK" \
           | jq -r ".[] | select(.email==\"$EMAIL\") | .id" | head -1)"
  curl -s -X PATCH "$AUTH/users/$UID_P" -H 'content-type: application/json' \
    -H "Authorization: Bearer $TOK" \
    -d "$(jq -nc --argjson pools "$POOLS_JSON" '{accessible_pools:$pools}')" \
    | jq -r '"  " + .email + " pools=" + ((.accessible_pools // [])|length|tostring)'
else
  printf '%s' "$RESP" | sed '$d' \
    | jq -r '"  " + (.email // "?") + " pools=" + ((.accessible_pools // [])|length|tostring)'
fi

# ── Testemunha, e ela LE DE VOLTA ────────────────────────────────────────────
#
# ⚠️ A primeira versao desta conferencia comparava `$N_POOLS` — o numero que o script
# PRETENDIA escrever — contra o do admin, e passava mesmo quando a escrita nao pegava.
# Provado por mutacao em 2026-08-31: trocando o corpo do PATCH por `accessible_pools:[]`
# a linha continuou anunciando "principal=36". *"Foi escrito" nao e "mudou"* — a unica
# leitura que vale e a de VOLTA, da API.
USERS="$(curl -s "$AUTH/users?tenant_id=$TENANT" -H "Authorization: Bearer $TOK")"
N_REAL="$(printf '%s' "$USERS" | jq -r ".[] | select(.email==\"$EMAIL\") | (.accessible_pools|length)" | head -1)"
N_ADMIN="$(printf '%s' "$USERS" | jq -r ".[] | select(.email==\"$_PH_EMAIL\") | (.accessible_pools|length)" | head -1)"
echo "--- conferencia: principal=${N_REAL:-0} pools (lidos de volta) · admin=${N_ADMIN:-0} pools ---"
[ "${N_REAL:-0}" -ge "${N_ADMIN:-0}" ] && [ "${N_REAL:-0}" -gt 0 ] || {
  echo "FALHA: o principal tem ${N_REAL:-0} pools e o admin ${N_ADMIN:-0} — nao ve o tenant inteiro"
  exit 1
}
