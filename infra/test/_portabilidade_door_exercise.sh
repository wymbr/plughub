# _portabilidade_door_exercise.sh — PID-17. Sourced pelo probe_portabilidade_door.sh.
#
# A porta da portabilidade pelo chat, com peças reais: cliente IMPORTADO (celular
# autoritativo, o único que o OTP aceita), a porta de plataforma no `portabilidade_ia`
# (âncora = a linha a portar) e o processo `portabilidade_processo_ia`.
#
#   pd_fixture                cria o cliente           → PD_FONE, PD_CUST, PD_PHASH
#   pd_chat <label> <regras>  identifica + prova pelo chat e aplica as regras extras
#                                                      → PD_OUT, PD_SID, PD_DESDE
#   pd_acha_processo          processo nascido do chat → PD_N3, PD_CTX (session.*)
#   pd_journey <tag>          valor da tag na journey cujo número é PD_FONE
#   pd_dispara                processo direto pelo pool webhook (sem porta)  → PD_N3
#   pd_aprova                 aprova o `suspend` do processo e espera a pendência `offer`
#                                                      → PD_TOKEN
#   pd_vivo <token>           1 se o token de retomada segue válido
#   pd_fim <sessão>           último step antes do __complete__ (vazio se não terminou)
#
# Espera: TENANT, COMPOSE, CG. Erros em PD_ERRO.
#
# `pd_aprova` retoma o `suspend reason: approval` como a OPERADORA retomaria: pela porta
# EXTERNA, com o token como credencial e `decision: approved` (APR-11, decisão do dono em
# 2026-09-14). Antes ia pela rota interna sem credencial, porque a externa descartava a
# decisão — e a recusa da operadora virava aprovação.

pd_fixture() {
  local cpf fix
  PD_ERRO=""; PD_FONE="+5511$(date +%s%N | tail -c 10)"
  cpf="529$(date +%s%N | tail -c 9)"
  fix=$($COMPOSE exec -T channel-gateway python - "__probe_pid17__" "pid17-$cpf" "$cpf" "$PD_FONE" \
    < infra/test/_import_customer_fixture.py 2>/dev/null | tail -1)
  PD_CUST=$(printf '%s' "$fix" | python3 -c 'import json,sys
try: d=json.load(sys.stdin); print(d.get("customer_id","") if d.get("outcome")=="created" else "")
except Exception: print("")')
  PD_PHASH=$(printf '%s' "$fix" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("phone_hash",""))
except Exception: print("")')
  [ -n "$PD_CUST" ] && [ -n "$PD_PHASH" ] || { PD_ERRO="fixture não criou: ${fix:0:160}"; return 1; }
}

pd_chat() {  # $1 rótulo · $2 regras extras (JSON list) · $3 hold_s
  local code_file="/tmp/otp_code_pid17_$1" roteiro
  PD_OUT=""; PD_SID=""; PD_ERRO=""
  PD_DESDE=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
  $COMPOSE exec -T channel-gateway rm -f "$code_file" </dev/null >/dev/null 2>&1
  ( timeout 150 $COMPOSE logs -f --no-log-prefix --since "$PD_DESDE" channel-gateway 2>&1 \
      | grep -m1 --line-buffered "OTP-DEV.*kind=phone value_hash=$PD_PHASH" \
      | sed -n 's/.*code=\([0-9]*\).*/\1/p' \
      | { read -r code; [ -n "$code" ] && $COMPOSE exec -T channel-gateway sh -c "printf %s $code > $code_file" </dev/null; } ) &
  $COMPOSE cp infra/test/_ws_chat.py channel-gateway:/tmp/_ws_chat.py >/dev/null 2>&1 \
    || { PD_ERRO="docker compose cp do cliente WS falhou"; return 1; }
  roteiro=$(python3 -c 'import json,sys
fone, cf, extra = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
print(json.dumps([
  {"match": "deseja portar", "answer": fone},
  {"match": "código de verificação", "answer": "sim"},
  {"match": "Para qual celular", "answer": fone},
  {"match": "Digite o código", "answer_file": cf, "wait_s": 40},
] + extra, ensure_ascii=False))' "$PD_FONE" "$code_file" "$2")
  PD_OUT=$($COMPOSE exec -T channel-gateway python3 /tmp/_ws_chat.py \
    "$TENANT" "portabilidade_ia" "cli_pid17_$1_$(date +%s%N | tail -c 7)" "$roteiro" "${3:-150}" 2>&1)
  PD_SID=$(printf '%s' "$PD_OUT" | sed -n 's/^AUTHENTICATED session_id=//p' | head -1)
  [ -n "$PD_SID" ] || { PD_ERRO="o chat não autenticou: ${PD_OUT:0:200}"; return 1; }
  return 0
}

pd_acha_processo() {
  local k v
  PD_N3=""; PD_CTX=""
  for _ in $(seq 1 15); do
    for k in $(docker exec plughub-demo-redis-1 redis-cli --scan --pattern "$TENANT:ctx:*" 2>/dev/null); do
      case "$k" in *journey*|*customer*) continue ;; esac
      v=$(docker exec plughub-demo-redis-1 redis-cli hget "$k" core.workflow.origin_session_id 2>/dev/null)
      case "$v" in *"$PD_SID"*) PD_N3="${k##*:}"; break 2 ;; esac
    done
    sleep 2
  done
  [ -n "$PD_N3" ] || { PD_ERRO="nenhum processo com origin=$PD_SID (o pedido não foi disparado?)"; return 1; }
  PD_CTX=$(docker exec plughub-demo-redis-1 redis-cli hgetall "$TENANT:ctx:$PD_N3" 2>/dev/null | python3 -c 'import json,sys
l=[x.rstrip("\n") for x in sys.stdin]
out={}
for i in range(0, len(l)-1, 2):
    try: out[l[i]] = json.loads(l[i+1]).get("value")
    except Exception: out[l[i]] = l[i+1]
print(json.dumps({k: v for k, v in out.items() if k.startswith("session.")}, ensure_ascii=False))')
  return 0
}

pd_journey() {  # $1 tag → valor na journey cujo journey.numero_atual é PD_FONE
  local k v
  for k in $(docker exec plughub-demo-redis-1 redis-cli --scan --pattern "$TENANT:ctx:journey:*" 2>/dev/null); do
    v=$(docker exec plughub-demo-redis-1 redis-cli hget "$k" journey.numero_atual 2>/dev/null)
    case "$v" in *"$PD_FONE"*)
      docker exec plughub-demo-redis-1 redis-cli hget "$k" "$1" 2>/dev/null \
        | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("value") or "")
except Exception: print("")'
      return 0 ;;
    esac
  done
  echo ""
}

pd_dispara() {
  local trig
  PD_N3=""; PD_ERRO=""
  trig=$(curl -s --max-time 20 -X POST "$CG/v1/channels/webhook/pool/portabilidade_processo_ia" \
    -H 'content-type: application/json' -d "{\"tenant_id\": \"$TENANT\",
    \"context\": {\"session.phone\": \"$PD_FONE\", \"session.customer_id\": \"$PD_CUST\",
      \"session.operadora_destino\": \"tim\", \"session.contact_identifier\": \"$PD_FONE\"}}")
  PD_N3=$(printf '%s' "$trig" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("session_id",""))
except Exception: print("")')
  [ -n "$PD_N3" ] || { PD_ERRO="trigger sem session_id: ${trig:0:160}"; return 1; }
}

pd_aprova() {
  local tok pend pol code
  PD_TOKEN=""; PD_ERRO=""
  for _ in $(seq 1 20); do
    tok=$(docker exec plughub-demo-redis-1 redis-cli hgetall "$TENANT:resume_tokens" 2>/dev/null \
          | paste - - | grep -F "$PD_N3" | head -1 | cut -f1)
    [ -n "$tok" ] && break; sleep 1
  done
  [ -n "$tok" ] || { PD_ERRO="o processo $PD_N3 não suspendeu na aprovação"; return 1; }
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -X POST "$CG/channel/webhook/resume/$tok" \
    -H 'content-type: application/json' -d "{\"tenant_id\":\"$TENANT\",\"payload\":{\"decision\":\"approved\"}}")
  case "$code" in 200|202) ;; *) PD_ERRO="aprovação recusada (HTTP $code) — ver a nota do cabeçalho"; return 1 ;; esac
  for _ in $(seq 1 30); do
    pend=$(curl -s --max-time 10 "$CG/v1/channels/webhook/pending/by-customer/$PD_CUST?tenant_id=$TENANT")
    pol=$(printf '%s' "$pend" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("policy") or "")
except Exception: print("")')
    [ "$pol" = "offer" ] && break
    sleep 1
  done
  [ "$pol" = "offer" ] || { PD_ERRO="a pendência offer não nasceu em 30 s depois da aprovação"; return 1; }
  PD_TOKEN=$(printf '%s' "$pend" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("resume_token") or "")
except Exception: print("")')
}

pd_vivo() { docker exec plughub-demo-redis-1 redis-cli hexists "$TENANT:resume_tokens" "$1" 2>/dev/null; }

pd_fim() {
  local v
  for _ in $(seq 1 20); do
    v=$(docker exec plughub-demo-postgres-1 psql -U plughub -d plughub_demo -Atc "select x->>'from_step' from session_pipeline_state s,
        jsonb_array_elements(s.transitions) x where s.session_id='$1' and x->>'to_step'='__complete__'
        order by s.persisted_at desc limit 1" 2>/dev/null)
    [ -n "$v" ] && { echo "$v"; return; }
    sleep 1
  done
}
