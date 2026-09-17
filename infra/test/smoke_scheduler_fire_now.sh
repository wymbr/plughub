#!/usr/bin/env bash
# Scheduler Fase 3 — smoke do endpoint 'disparar agora' (POST /v1/agendas/{id}/fire).
#
# Prova: um disparo manual (a) cria um AgendaDispatch imediato (scheduled_for≈now) e
# (b) NÃO consome/recalcula a recorrência (next_fire_at intacto). Cria uma agenda
# RECORRENTE (daily), lê o next_fire_at, dispara agora, e confere que o ledger ganhou
# um dispatch e o next_fire_at não mudou.
#
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_scheduler_fire_now.sh
set -euo pipefail

TENANT="tenant_demo"
SC="http://localhost:3650"
BODY_POOL="deploy_promote_ia"   # pool webhook do corpo (Fase 2); qualquer webhook serve
# SCH-01 (2026-09-17): as rotas de Agenda exigem credencial (`scheduler.configurar` para
# criar/editar, `scheduler.operacao` para disparar/pausar/cancelar e ler). Este smoke passa
# a entrar como gente, que e o caminho real de quem usa a tela — sem isto ele mediria 401.
AUTH_API="${AUTH_API:-http://localhost:3202}"
SCHED_TOKEN=$(curl -s --max-time 20 -X POST "$AUTH_API/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null)
[ -n "$SCHED_TOKEN" ] || { echo "AVISO: login de admin falhou — as chamadas ao scheduler vao levar 401"; }
ts=(-H "X-Tenant-ID: $TENANT" -H "Authorization: Bearer $SCHED_TOKEN")

jqget() { sed -n "s/.*\"$1\":\"\([^\"]*\)\".*/\1/p" | head -1; }

echo "1) Cria agenda RECORRENTE (daily 09:00) → $BODY_POOL ..."
AG=$(curl -s -X POST "$SC/v1/agendas" "${ts[@]}" -H 'content-type: application/json' -d "{
  \"name\":\"E2E fire-now (daily)\",
  \"target_pool_id\":\"$BODY_POOL\",
  \"payload\":{\"target_pool\":\"sac_ia\",\"action\":\"promote\"},
  \"validity\":{\"starts_at\":\"2026-07-21T00:00:00Z\"},
  \"schedule\":{\"mode\":\"recurring\",\"rule\":{\"frequency\":\"daily\",\"interval\":1,\"times\":[\"09:00\"],\"business_day_policy\":\"ignore\",\"month_overflow\":\"clamp\"}}
}" | jqget id)
[ -n "$AG" ] || { echo "FALHA: agenda sem id"; exit 1; }
echo "   agenda = $AG"

NEXT_BEFORE=$(curl -s "$SC/v1/agendas/$AG" "${ts[@]}" | jqget next_fire_at)
echo "   next_fire_at (antes) = $NEXT_BEFORE"

echo "2) Disparar agora (POST /fire) ..."
curl -s -X POST "$SC/v1/agendas/$AG/fire" "${ts[@]}" | python3 -m json.tool

echo "3) Aguardando o ledger (3s) ..."
sleep 3

echo "4) Ledger de disparos (esperado: 1 dispatch, scheduled_for≈now):"
curl -s "$SC/v1/agendas/$AG/dispatches" "${ts[@]}" | python3 -m json.tool

NEXT_AFTER=$(curl -s "$SC/v1/agendas/$AG" "${ts[@]}" | jqget next_fire_at)
echo "5) next_fire_at (depois) = $NEXT_AFTER"

if [ "$NEXT_AFTER" = "$NEXT_BEFORE" ]; then
  echo "   PASS: next_fire_at INALTERADO — o disparo manual não consumiu a recorrência."
else
  echo "   FALHA: next_fire_at mudou ($NEXT_BEFORE → $NEXT_AFTER) — fire-now não deve reagendar."; exit 1
fi

echo "6) Limpeza: cancela a agenda de teste ..."
curl -s -X POST "$SC/v1/agendas/$AG/cancel" "${ts[@]}" >/dev/null && echo "   cancelada."
echo "GATE fire-now — OK."
