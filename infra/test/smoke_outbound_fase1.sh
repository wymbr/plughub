#!/usr/bin/env bash
# Outbound Fase 1 — smoke e2e do substrato (DIFF ZERO no scheduler).
#
# Prova a cadeia: Agenda dispara o pool webhook outbound_demo com payload {campaign_id}
# → o skill drena o mailing da campanha (campaign_drain, claim atômico) → percorre as
# entradas (loop) → contabiliza cada entrega (campaign_delivery_result). Depois RE-dispara
# e confere que NÃO houve re-drain (claim idempotente por UNIQUE(campaign_id, entry)).
#
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_outbound_fase1.sh
# ⚠️ UTF-8 explicito na SAIDA do python. No Windows o `stdout` decodifica com cp1252 e
# um `print` de texto acentuado estoura `UnicodeEncodeError`, derrubando o probe por
# motivo de bancada — ou, pior, mutila o texto que o shell vai comparar.
#
# ⚠️ E o que esta linha NAO conserta, porque o diagnostico foi REFEITO em 2026-09-02:
# a corrupcao que motivou a CNS-12 nao vinha do `sys.stdin` (medido: `curl | python3 ->
# arquivo` preserva `Almoco`/`Reuniao` intactos). Vinha da VARIAVEL DE SHELL — passar
# JSON nao-ASCII por `VAR=$(…)` o mutila, medido 321 bytes contra 325. Contra isso a
# unica defesa e nao passar por variavel: producao e consumo por ARQUIVO.
export PYTHONIOENCODING=utf-8

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TENANT="tenant_demo"
MA="http://localhost:3660"
SC="http://localhost:3650"
ts=(-H "X-Tenant-ID: $TENANT")
jqid() { sed -n 's/.*"id":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1; }

echo "1) Seed (mailing + 3 entries + campaign) ..."
SEED_OUT=$(bash "$HERE/seed_outbound_demo.sh")
echo "$SEED_OUT"
CAMP=$(echo "$SEED_OUT" | sed -n 's/^CAMPAIGN_ID=//p')
[ -n "$CAMP" ] || { echo "FALHA: sem CAMPAIGN_ID"; exit 1; }

echo "2) Cria agenda (fire_at FUTURO — só /fire dispara; poller não interfere) → outbound_demo ..."
AG=$(curl -s -X POST "$SC/v1/agendas" "${ts[@]}" -H 'content-type: application/json' -d "{
  \"name\":\"E2E outbound fase1\",
  \"target_pool_id\":\"outbound_demo\",
  \"payload\":{\"campaign_id\":\"$CAMP\"},
  \"validity\":{\"starts_at\":\"2026-07-21T00:00:00Z\"},
  \"schedule\":{\"mode\":\"once\",\"fire_at\":\"2030-01-01T09:00:00Z\"}
}" | jqid)
[ -n "$AG" ] || { echo "FALHA: agenda sem id"; exit 1; }
echo "   agenda = $AG"

echo "3) Disparar agora (POST /fire) ..."
curl -s -X POST "$SC/v1/agendas/$AG/fire" "${ts[@]}" >/dev/null

# A sessão webhook é ASSÍNCRONA (cold start do pool + alocação). Poll em vez de sleep
# fixo — o skill drena/contabiliza quando a instância sobe (pode passar de 10s a frio).
echo "4) Aguardando o skill drenar + contabilizar (poll até 45s) ..."
DELIV='{"deliveries":[],"total":0}'; N1=0; NC=0
for i in $(seq 1 15); do
  sleep 3
  DELIV=$(curl -s "$MA/v1/campaigns/$CAMP/deliveries" "${ts[@]}")
  N1=$(echo "$DELIV" | python3 -c 'import sys,json;print(json.load(sys.stdin)["total"])')
  NC=$(echo "$DELIV" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(sum(1 for x in d["deliveries"] if x["result"] in ("contacted","responded")))')
  echo "   [t=$((i*3))s] total=$N1 contabilizadas=$NC"
  [ "$NC" -ge 1 ] && break
done

echo "5) Entregas da campanha (esperado: 3 claimadas + contabilizadas):"
echo "$DELIV" | python3 -m json.tool
[ "$N1" -ge 1 ] || { echo "FALHA: nenhuma entrega drenada/claimada em 45s (webhook→skill→drain quebrou?)"; exit 1; }
[ "$NC" -ge 1 ] || { echo "FALHA: skill não marcou nenhuma entrega em 45s (loop/delivery_result quebrou?)"; exit 1; }

echo "6) Re-disparar (prova de NÃO re-drain — claim idempotente) ..."
curl -s -X POST "$SC/v1/agendas/$AG/fire" "${ts[@]}" >/dev/null
echo "   aguardando a 2ª sessão rodar o drain (20s) ..."
sleep 20
N2=$(curl -s "$MA/v1/campaigns/$CAMP/deliveries" "${ts[@]}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["total"])')
echo "   total entregas (após re-disparo) = $N2"
if [ "$N2" = "$N1" ]; then
  echo "   PASS: contagem inalterada — sem re-drain (2ª sessão drenou [] pois tudo já foi claimado)."
else
  echo "   FALHA: re-drain criou novas entregas ($N1 → $N2)"; exit 1
fi

echo "7) Limpeza: cancela a agenda de teste ..."
curl -s -X POST "$SC/v1/agendas/$AG/cancel" "${ts[@]}" >/dev/null && echo "   cancelada."
echo "GATE outbound Fase 1 — OK."
