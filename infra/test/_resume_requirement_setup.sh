# _resume_requirement_setup.sh — PID-06. Sourced pelo probe (e pela medição vermelha).
#
# Monta o cenário da pergunta com peças REAIS e imprime as variáveis do resultado:
#   1. cliente IMPORTADO (cpf + celular autoritativos) — o único que o OTP aceita (PID-10);
#   2. processo `limite_processo` disparado para ele, até a pendência nascer (`policy=offer`);
#   3. S1 desafia o celular por OTP; o código é lido do log do gateway (modo dev);
#   4. S1 verifica e as DUAS sessões pedem a pendência pela mesma âncora.
# Deixa em RESULT a linha JSON da fase `mede` e em PEND o registro da pendência.
#
# Espera no ambiente: TENANT, COMPOSE, CG, MCP (container do mcp-server).

rr_setup() {
  RESULT=""; PEND=""; RR_ERRO=""
  local cpf fone fix cust phash trig proc_sid pol desde code d
  cpf="529$(date +%s | tail -c 9)"
  fone="+5511$(date +%s%N | tail -c 10)"
  fix=$($COMPOSE exec -T channel-gateway python - "__probe_pid06__" "pid06-$cpf" "$cpf" "$fone" \
    < infra/test/_import_customer_fixture.py 2>/dev/null | tail -1)
  cust=$(printf '%s' "$fix" | python3 -c 'import json,sys
try: d=json.load(sys.stdin); print(d.get("customer_id","") if d.get("outcome")=="created" else "")
except Exception: print("")')
  phash=$(printf '%s' "$fix" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("phone_hash",""))
except Exception: print("")')
  [ -n "$cust" ] && [ -n "$phash" ] || { RR_ERRO="fixture de cliente não criou: ${fix:0:200}"; return 1; }

  trig=$(curl -s --max-time 20 -X POST "$CG/v1/channels/webhook/pool/limite_processo" -H 'content-type: application/json' -d "{
    \"tenant_id\": \"$TENANT\",
    \"context\": {\"session.cpf\": \"$cpf\", \"session.customer_id\": \"$cust\",
      \"session.numero_cartao\": \"4111111111111234\", \"session.vencimento_cartao\": \"1230\",
      \"session.limite_solicitado\": \"12000\"}}")
  proc_sid=$(printf '%s' "$trig" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("session_id",""))
except Exception: print("")')
  [ -n "$proc_sid" ] || { RR_ERRO="trigger do limite_processo sem session_id: ${trig:0:200}"; return 1; }
  for _ in $(seq 1 30); do
    PEND=$(curl -s --max-time 10 "$CG/v1/channels/webhook/pending/by-customer/$cust?tenant_id=$TENANT")
    pol=$(printf '%s' "$PEND" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("policy") or "")
except Exception: print("")')
    [ "$pol" = "offer" ] && break
    sleep 1
  done
  [ "$pol" = "offer" ] || { RR_ERRO="a pendência não nasceu em 30 s (${PEND:0:200})"; return 1; }

  desde=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
  d=$(docker exec -i "$MCP" sh -c "cd /app/packages/mcp-server-plughub && node - desafia $TENANT $cust $fone" \
    < infra/test/_resume_requirement_exercise.cjs 2>/dev/null | tail -1)
  code=""
  for _ in $(seq 1 20); do
    code=$($COMPOSE logs --no-log-prefix --since "$desde" channel-gateway 2>&1 \
      | grep "OTP-DEV.*kind=phone value_hash=$phash" | tail -1 | sed -n 's/.*code=\([0-9]*\).*/\1/p')
    [ -n "$code" ] && break
    sleep 1
  done
  [ -n "$code" ] || { RR_ERRO="código do OTP não apareceu no log (desafio: ${d:0:200})"; return 1; }

  RESULT=$(docker exec -i "$MCP" sh -c "cd /app/packages/mcp-server-plughub && node - mede $TENANT $cust $fone $code" \
    < infra/test/_resume_requirement_exercise.cjs 2>/dev/null | tail -1)
  return 0
}
