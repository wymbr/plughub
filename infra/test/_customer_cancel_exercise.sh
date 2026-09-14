# _customer_cancel_exercise.sh — PID-15. Sourced pelo probe (e pela medição vermelha).
#
# O cliente comprovado cancela o próprio pedido pelo chat, com peças reais: cliente
# IMPORTADO (celular autoritativo), processo `limite_processo` parado na aprovação
# (`policy=offer`), e o acesso pelo `limite_ia`: CPF → OTP → menu de continuidade →
# "Cancelar solicitação". O intake chama `workflow_resume` com `decision=rejected` sobre o
# token de `aprovar` — que é tarefa de APROVAÇÃO (declara `approvals.decide`).
#
# Espera: TENANT, COMPOSE, CG. Deixa CC_OUT (transcrição), CC_TOKEN (token da pendência),
# CC_PROC (sessão do processo), CC_DESDE (instante antes do chat) e CC_ERRO.

cc_run() {
  CC_OUT=""; CC_TOKEN=""; CC_PROC=""; CC_DESDE=""; CC_ERRO=""
  local cpf fone fix cust phash trig pend pol code_file roteiro
  cpf="529$(date +%s | tail -c 9)"
  fone="+5511$(date +%s%N | tail -c 10)"
  fix=$($COMPOSE exec -T channel-gateway python - "__probe_pid15__" "pid15-$cpf" "$cpf" "$fone" \
    < infra/test/_import_customer_fixture.py 2>/dev/null | tail -1)
  cust=$(printf '%s' "$fix" | python3 -c 'import json,sys
try: d=json.load(sys.stdin); print(d.get("customer_id","") if d.get("outcome")=="created" else "")
except Exception: print("")')
  phash=$(printf '%s' "$fix" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("phone_hash",""))
except Exception: print("")')
  [ -n "$cust" ] && [ -n "$phash" ] || { CC_ERRO="fixture não criou: ${fix:0:160}"; return 1; }

  trig=$(curl -s --max-time 20 -X POST "$CG/v1/channels/webhook/pool/limite_processo" -H 'content-type: application/json' -d "{
    \"tenant_id\": \"$TENANT\",
    \"context\": {\"session.cpf\": \"$cpf\", \"session.customer_id\": \"$cust\",
      \"session.numero_cartao\": \"4111111111111234\", \"session.vencimento_cartao\": \"1230\",
      \"session.limite_solicitado\": \"12000\"}}")
  CC_PROC=$(printf '%s' "$trig" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("session_id",""))
except Exception: print("")')
  [ -n "$CC_PROC" ] || { CC_ERRO="trigger sem session_id: ${trig:0:160}"; return 1; }
  for _ in $(seq 1 30); do
    pend=$(curl -s --max-time 10 "$CG/v1/channels/webhook/pending/by-customer/$cust?tenant_id=$TENANT")
    pol=$(printf '%s' "$pend" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("policy") or "")
except Exception: print("")')
    [ "$pol" = "offer" ] && break
    sleep 1
  done
  [ "$pol" = "offer" ] || { CC_ERRO="a pendência não nasceu em 30 s"; return 1; }
  CC_TOKEN=$(printf '%s' "$pend" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("resume_token") or "")
except Exception: print("")')

  code_file=/tmp/otp_code_pid15
  CC_DESDE=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
  $COMPOSE exec -T channel-gateway rm -f "$code_file" </dev/null >/dev/null 2>&1
  ( timeout 150 $COMPOSE logs -f --no-log-prefix --since "$CC_DESDE" channel-gateway 2>&1 \
      | grep -m1 --line-buffered "OTP-DEV.*kind=phone value_hash=$phash" \
      | sed -n 's/.*code=\([0-9]*\).*/\1/p' \
      | { read -r code; [ -n "$code" ] && $COMPOSE exec -T channel-gateway sh -c "printf %s $code > $code_file" </dev/null; } ) &
  $COMPOSE cp infra/test/_ws_chat.py channel-gateway:/tmp/_ws_chat.py >/dev/null 2>&1 \
    || { CC_ERRO="docker compose cp do cliente WS falhou"; return 1; }
  roteiro="[{\"match\":\"CPF\",\"answer\":\"$cpf\"},{\"match\":\"código de verificação\",\"answer\":\"sim\"},{\"match\":\"celular\",\"answer\":\"$fone\"},{\"match\":\"Digite o código\",\"answer_file\":\"$code_file\",\"wait_s\":40},{\"match\":\"já tem um pedido\",\"answer\":\"cancelar_pendente\"}]"
  CC_OUT=$($COMPOSE exec -T channel-gateway python3 /tmp/_ws_chat.py \
    "$TENANT" "limite_ia" "cli_cc_$cpf" "$roteiro" 150 2>&1)
  printf '%s' "$CC_OUT" | grep -q '^ANSWER .*cancelar_pendente' \
    || { CC_ERRO="o chat não chegou ao menu de continuidade: ${CC_OUT:0:200}"; return 1; }
  return 0
}
