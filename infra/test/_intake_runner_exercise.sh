# _intake_runner_exercise.sh — PID-04. Sourced pelo probe_intake_runner.sh.
#
# O ACESSO 1 pelo chat, com peças reais: cliente IMPORTADO (celular autoritativo, o único
# que o OTP aceita), CPF → OTP no celular → nenhuma pendência → formulário → gatilho do
# processo. Existe porque nenhum probe vivo passava pelo ramo "pedido novo" do intake: o
# smoke dos três acessos dispara o processo direto por webhook.
#
# `ir_access1 <numero_cartao>` preenche o formulário com o número dado — é por aí que o
# probe tenta INJETAR chaves no contexto do processo. Depois procura a sessão do processo
# (a que tem `core.workflow.origin_session_id` = sessão do chat) e lê o que chegou lá.
#
# Espera: TENANT, COMPOSE. Deixa IR_CPF, IR_SID (chat), IR_N3 (sessão do processo),
# IR_OUT (transcrição), IR_CTX (JSON {tag: valor} do contexto do processo) e IR_ERRO.

ir_access1() {
  IR_OUT=""; IR_SID=""; IR_N3=""; IR_CTX=""; IR_ERRO=""
  local cartao="$1" fone fix cust phash code_file desde roteiro
  IR_CPF="529$(date +%s%N | tail -c 9)"
  fone="+5511$(date +%s%N | tail -c 10)"
  fix=$($COMPOSE exec -T channel-gateway python - "__probe_pid04__" "pid04-$IR_CPF" "$IR_CPF" "$fone" \
    < infra/test/_import_customer_fixture.py 2>/dev/null | tail -1)
  cust=$(printf '%s' "$fix" | python3 -c 'import json,sys
try: d=json.load(sys.stdin); print(d.get("customer_id","") if d.get("outcome")=="created" else "")
except Exception: print("")')
  phash=$(printf '%s' "$fix" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("phone_hash",""))
except Exception: print("")')
  [ -n "$cust" ] && [ -n "$phash" ] || { IR_ERRO="fixture não criou: ${fix:0:160}"; return 1; }

  code_file=/tmp/otp_code_pid04
  desde=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
  $COMPOSE exec -T channel-gateway rm -f "$code_file" </dev/null >/dev/null 2>&1
  ( timeout 150 $COMPOSE logs -f --no-log-prefix --since "$desde" channel-gateway 2>&1 \
      | grep -m1 --line-buffered "OTP-DEV.*kind=phone value_hash=$phash" \
      | sed -n 's/.*code=\([0-9]*\).*/\1/p' \
      | { read -r code; [ -n "$code" ] && $COMPOSE exec -T channel-gateway sh -c "printf %s $code > $code_file" </dev/null; } ) &
  $COMPOSE cp infra/test/_ws_chat.py channel-gateway:/tmp/_ws_chat.py >/dev/null 2>&1 \
    || { IR_ERRO="docker compose cp do cliente WS falhou"; return 1; }
  roteiro=$(python3 -c 'import json,sys
cpf, fone, cartao, cf = sys.argv[1:5]
print(json.dumps([
  {"match": "CPF", "answer": cpf},
  {"match": "código de verificação", "answer": "sim"},
  {"match": "celular", "answer": fone},
  {"match": "Digite o código", "answer_file": cf, "wait_s": 40},
  {"match": "Preencha", "answer": {"numero_cartao": cartao, "vencimento_cartao": "12/30",
                                   "limite_solicitado": "5000", "cvv": "123"}},
], ensure_ascii=False))' "$IR_CPF" "$fone" "$cartao" "$code_file")
  IR_OUT=$($COMPOSE exec -T channel-gateway python3 /tmp/_ws_chat.py \
    "$TENANT" "limite_ia" "cli_pid04_$IR_CPF" "$roteiro" 150 2>&1)
  IR_SID=$(printf '%s' "$IR_OUT" | sed -n 's/^AUTHENTICATED session_id=//p' | head -1)
  [ -n "$IR_SID" ] || { IR_ERRO="o chat não autenticou: ${IR_OUT:0:200}"; return 1; }

  # a sessão do processo: a que nasceu com origin_session_id = o chat
  local k
  for _ in $(seq 1 15); do
    for k in $(docker exec plughub-demo-redis-1 redis-cli --scan --pattern "$TENANT:ctx:*" 2>/dev/null); do
      case "$k" in *journey*|*customer*) continue ;; esac
      v=$(docker exec plughub-demo-redis-1 redis-cli hget "$k" core.workflow.origin_session_id 2>/dev/null)
      case "$v" in *"$IR_SID"*) IR_N3="${k##*:}"; break 2 ;; esac
    done
    sleep 2
  done
  [ -n "$IR_N3" ] || { IR_ERRO="nenhuma sessão de processo com origin=$IR_SID (o pedido não foi disparado?)"; return 1; }
  IR_CTX=$(docker exec plughub-demo-redis-1 redis-cli hgetall "$TENANT:ctx:$IR_N3" 2>/dev/null | python3 -c 'import json,sys
l=[x.rstrip("\n") for x in sys.stdin]
out={}
for i in range(0, len(l)-1, 2):
    try: out[l[i]] = json.loads(l[i+1]).get("value")
    except Exception: out[l[i]] = l[i+1]
print(json.dumps({k: v for k, v in out.items() if k.startswith("session.")}, ensure_ascii=False))')
  return 0
}
