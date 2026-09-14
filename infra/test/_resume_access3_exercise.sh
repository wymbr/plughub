# _resume_access3_exercise.sh — PID-13. Sourced pelo probe_resume_requirement.sh (ramo D).
#
# O CONTROLE POSITIVO da retomada com exigência, com peças reais: cliente IMPORTADO
# (celular autoritativo, o único que o OTP aceita), processo `limite_processo` aprovado
# pelo aprovador (via smoke_limite_tres_acessos, com o CPF deste cliente), e o ACESSO 3
# pelo chat: CPF → OTP no celular → pendência `auto` → `retomar_resultado` → o especialista
# `limite_retorno` chama `workflow_resume` pelo MCP, que só atesta a retomada se a sessão
# do intake provou. Sem este caminho, "a sessão sem prova é recusada" não distingue um
# portão que funciona de um que recusa tudo.
#
# Espera: TENANT, COMPOSE, CG. Deixa A3_OUT (transcrição), A3_SID (sessão do acesso 3),
# A3_ERRO (motivo quando não montou) e A3_DESDE (instante antes do chat).

a3_run() {
  A3_OUT=""; A3_SID=""; A3_ERRO=""; A3_DESDE=""
  local cpf fone fix cust phash smoke_rc code_file
  cpf="529$(date +%s | tail -c 9)"
  fone="+5511$(date +%s%N | tail -c 10)"
  fix=$($COMPOSE exec -T channel-gateway python - "__probe_pid13__" "pid13-$cpf" "$cpf" "$fone" \
    < infra/test/_import_customer_fixture.py 2>/dev/null | tail -1)
  cust=$(printf '%s' "$fix" | python3 -c 'import json,sys
try: d=json.load(sys.stdin); print(d.get("customer_id","") if d.get("outcome")=="created" else "")
except Exception: print("")')
  phash=$(printf '%s' "$fix" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("phone_hash",""))
except Exception: print("")')
  [ -n "$cust" ] && [ -n "$phash" ] || { A3_ERRO="fixture não criou: ${fix:0:160}"; return 1; }

  # processo + aprovação + pendência auto, pelo smoke (mesmo CPF → mesmo cliente)
  CPF="$cpf" timeout 600 bash infra/test/smoke_limite_tres_acessos.sh > /tmp/pid13_a3_smoke.log 2>&1
  smoke_rc=$?
  grep -q "policy = auto" /tmp/pid13_a3_smoke.log \
    || { A3_ERRO="o smoke não deixou pendência auto (EXIT=$smoke_rc, ver /tmp/pid13_a3_smoke.log)"; return 1; }

  code_file=/tmp/otp_code_pid13
  A3_DESDE=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
  $COMPOSE exec -T channel-gateway rm -f "$code_file" </dev/null >/dev/null 2>&1
  ( timeout 150 $COMPOSE logs -f --no-log-prefix --since "$A3_DESDE" channel-gateway 2>&1 \
      | grep -m1 --line-buffered "OTP-DEV.*kind=phone value_hash=$phash" \
      | sed -n 's/.*code=\([0-9]*\).*/\1/p' \
      | { read -r code; [ -n "$code" ] && $COMPOSE exec -T channel-gateway sh -c "printf %s $code > $code_file" </dev/null; } ) &
  $COMPOSE cp infra/test/_ws_chat.py channel-gateway:/tmp/_ws_chat.py >/dev/null 2>&1 \
    || { A3_ERRO="docker compose cp do cliente WS falhou"; return 1; }
  local roteiro
  roteiro="[{\"match\":\"CPF\",\"answer\":\"$cpf\"},{\"match\":\"código de verificação\",\"answer\":\"sim\"},{\"match\":\"celular\",\"answer\":\"$fone\"},{\"match\":\"Digite o código\",\"answer_file\":\"$code_file\",\"wait_s\":40},{\"match\":\"mais alguma coisa\",\"answer\":\"nao\"}]"
  A3_OUT=$($COMPOSE exec -T channel-gateway python3 /tmp/_ws_chat.py \
    "$TENANT" "limite_ia" "cli_a3_$cpf" "$roteiro" 180 2>&1)
  A3_SID=$(printf '%s' "$A3_OUT" | sed -n 's/^AUTHENTICATED session_id=//p' | head -1)
  [ -n "$A3_SID" ] || { A3_ERRO="o acesso 3 não autenticou: ${A3_OUT:0:200}"; return 1; }
  return 0
}
