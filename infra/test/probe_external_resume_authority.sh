#!/usr/bin/env bash
# probe_external_resume_authority.sh — 2026-09-14  (APR-11)
#
# PERGUNTA: pela porta EXTERNA de resume, um sistema decide o que é dele (aprovar OU recusar
#           um `suspend reason: approval`, com o token como credencial) — e só isso?
#
# O QUE FOI MEDIDO ANTES (vermelho ao vivo, peças reais)
#   R1  a operadora RECUSOU a portabilidade por `/channel/webhook/resume/{token}` e o processo
#       seguiu APROVADO: a porta descartava `decision`, e o bridge assume `input`.
#   R2  a tarefa de promoção de deploy — 401 na rota interna sem credencial (AUT-46) — foi
#       APROVADA pela porta externa, sem credencial nenhuma: terminou em `efetuar_promocao`.
#       O carimbo `verification_class` só existia com aprovador (e por `setdefault`), então o
#       portão `== claimed` do fluxo não disparou.
#
# TRÊS RAMOS
#   A  CENSO — a porta externa aplica a regra da AUT-46 e julga a decisão pela casa única
#      (`resume_authority.py`) antes de retomar; o carimbo do `handle_resume` vem do servidor
#      e sobrescreve. Mutação do censo.
#   M  FALSEABILIDADE — `test_resume_authority.py` dentro da imagem, com o produto quebrado.
#   B  AO VIVO — G1 aprovação sem decisão 422 (token vivo) e recusa → `encerrar_rejeitado`;
#      G2 aprovação → pendência de confirmação (controle); G3 promoção pela porta externa 401,
#      tarefa viva. A porta externa para tarefa SEM capacidade segue aberta: é o P1 do
#      `gate_external_resume.sh`, que não se repete aqui.
#
# ⚠️ CUSTO: dois processos de portabilidade de fixture (um termina rejeitado; o outro deixa a
# pendência de confirmação, que vence no prazo) e uma tarefa de promoção, encerrada no fim.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO
set -uo pipefail
cd "$(dirname "$0")/../.."

TENANT="${TENANT:-tenant_demo}"
COMPOSE="docker compose -p plughub-demo -f docker-compose.demo.yml"
CG="${CG:-http://localhost:8010}"
AUTH="${AUTH:-http://localhost:3202}"
RE="${RE:-http://localhost:3550}"
GW_IMG="${GW_IMAGE:-plughub-demo-channel-gateway}"
AD_EMAIL="${AD_EMAIL:-admin@plughub.local}"; AD_PASS="${AD_PASS:-changeme_admin}"
MAIN=packages/channel-gateway/src/plughub_channel_gateway/main.py
WH=packages/channel-gateway/src/plughub_channel_gateway/adapters/webhook.py
FALHA=0
INCONCL=0
ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }

echo "════════════════════════════════════════════════════════════════════"
echo " a porta externa decide o que é de sistema — e só isso?"
echo "════════════════════════════════════════════════════════════════════"

# ── A · CENSO ──────────────────────────────────────────────────────────────
echo ""
echo "── A · CENSO ──────────────────────────────────────────────────────────"
censo() {  # $1 main.py · $2 webhook.py → linha de veredicto
  python3 - "$1" "$2" <<'PY'
import ast, sys
main = open(sys.argv[1], encoding="utf-8").read()
wh = open(sys.argv[2], encoding="utf-8").read()
erros = []
arv = ast.parse(main)
fn = next((n for n in ast.walk(arv) if isinstance(n, ast.AsyncFunctionDef) and n.name == "external_webhook_resume"), None)
if fn is None:
    erros.append("external_webhook_resume sumiu")
else:
    ordem = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            nome = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
            ordem.setdefault(nome, n.lineno)
    for req in ("resume_required_abac", "judge_external_decision", "handle_resume"):
        if req not in ordem:
            erros.append("porta externa não chama %s" % req)
    if not erros and not (ordem["resume_required_abac"] < ordem["handle_resume"] and ordem["judge_external_decision"] < ordem["handle_resume"]):
        erros.append("a porta externa retoma ANTES de julgar")
arv2 = ast.parse(wh)
hr = next((n for n in ast.walk(arv2) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_handle_resume_locked"), None)
if hr is None:
    erros.append("_handle_resume_locked sumiu")
else:
    src = ast.get_source_segment(wh, hr) or ""
    if "server_trust_stamp(approver)" not in src or "payload.update(carimbo)" not in src:
        erros.append("o carimbo de confiança não vem do servidor")
    if 'setdefault(\n                "verification_class"' in src or 'setdefault("verification_class"' in src:
        erros.append("o chamador volta a poder declarar verification_class (setdefault)")
print("OK" if not erros else "ERRO " + " ; ".join(erros))
PY
}
C=$(censo "$MAIN" "$WH")
[ "$C" = "OK" ] && ok "porta externa julga capacidade e decisão antes de retomar; carimbo do servidor" || falha "$C"
TMPA=$(mktemp -d)
cp "$MAIN" "$TMPA/main.py"; cp "$WH" "$TMPA/webhook.py"
python3 - "$TMPA/webhook.py" <<'PY'
import sys
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
a = "        payload.update(carimbo)\n"
assert s.count(a) == 1
open(p, "w", encoding="utf-8").write(s.replace(a, '        payload.setdefault("verification_class", carimbo["verification_class"])\n'))
PY
CM=$(censo "$TMPA/main.py" "$TMPA/webhook.py"); rm -rf "$TMPA"
[ "$CM" != "OK" ] && ok "mutação 'carimbo volta a ser setdefault' → censo acusa" || falha "mutação do censo não acusou"

# ── M · FALSEABILIDADE ─────────────────────────────────────────────────────
echo ""
echo "── M · a suíte reprova quando o produto quebra ─────────────────────────"
LOG=/tmp/_probe_apr11_suite.log
mut_py() {  # $1 rótulo · $2 arquivo rel. a plughub_channel_gateway · $3 âncora · $4 substituto
  local a b
  a=$(printf '%s' "$3" | base64 -w0); b=$(printf '%s' "$4" | base64 -w0)
  docker run --rm --entrypoint sh -e A="$a" -e B="$b" -e ARQ="$2" "$GW_IMG" -c '
    cd /app/packages/channel-gateway && python - <<PY
import base64, os, sys
p = "src/plughub_channel_gateway/" + os.environ["ARQ"]
a, b = base64.b64decode(os.environ["A"]).decode(), base64.b64decode(os.environ["B"]).decode()
s = open(p, encoding="utf-8").read()
if s.count(a) != 1: sys.exit(3)
open(p, "w", encoding="utf-8").write(s.replace(a, b))
PY
    [ $? = 3 ] && { echo __ANCORA__; exit 9; }
    python -m pytest -q -p no:cacheprovider src/plughub_channel_gateway/tests/test_resume_authority.py 2>&1' >"$LOG" 2>&1
  local e=$?
  if grep -q __ANCORA__ "$LOG"; then incon "mutação '$1' não se aplicou (âncora sumiu)"; return; fi
  if [ "$e" = "0" ]; then falha "mutação '$1' passou no pytest — os testes não a julgam"
  elif grep -qE "AssertionError|assert |DID NOT RAISE" "$LOG" && ! grep -qE "ImportError|SyntaxError|IndentationError|errors? during collection" "$LOG"; then
    ok "mutação '$1' → pytest vermelho por asserção"
  else
    incon "mutação '$1' vermelha sem asserção: $(grep -m2 -E 'Error' "$LOG")"
  fi
}
if ! docker image inspect "$GW_IMG" >/dev/null 2>&1; then
  incon "imagem $GW_IMG ausente — falseabilidade não medida"
else
  docker run --rm --entrypoint sh "$GW_IMG" -c 'cd /app/packages/channel-gateway && python -m pytest -q -p no:cacheprovider src/plughub_channel_gateway/tests/test_resume_authority.py' >"$LOG" 2>&1 \
    && ok "controle: test_resume_authority passa na imagem" \
    || incon "test_resume_authority não passa na imagem sem mutação (imagem velha?): $(tail -1 "$LOG")"
  mut_py "aprovação volta a descartar a decisão" resume_authority.py \
    '    if suspend_reason == SYSTEM_APPROVAL_REASON:' '    if False:'
  mut_py "porta externa aceita timeout" resume_authority.py \
    'EXTERNAL_DECISIONS: frozenset[str] = frozenset({"approved", "rejected"})' 'EXTERNAL_DECISIONS: frozenset[str] = frozenset({"approved", "rejected", "timeout"})'
  mut_py "porta externa decide tarefa com capacidade" main.py \
    '    if required_abac is not None:
        _mod, _field = required_abac
        logger.warning(
            "APR-11 401' '    if False:
        _mod, _field = required_abac
        logger.warning(
            "APR-11 401'
  mut_py "chamador declara a própria confiança" adapters/webhook.py \
    '        payload.update(carimbo)' '        for _k, _v in carimbo.items(): payload.setdefault(_k, _v)'
  mut_py "resume anônimo sai sem carimbo" resume_authority.py \
    '    return {"verification_class": "claimed", "principal_type": "system"}' '    return {}'
fi

# ── B · AO VIVO ────────────────────────────────────────────────────────────
echo ""
echo "── B · AO VIVO ────────────────────────────────────────────────────────"
# shellcheck source=/dev/null
. infra/test/_auth.sh
plughub_gw_service_shim
# shellcheck source=/dev/null
. infra/test/_portabilidade_door_exercise.sh
R() { docker exec plughub-demo-redis-1 redis-cli "$@"; }
ext() { command curl -s -o /tmp/_apr11_ext.json -w '%{http_code}' --max-time 20 -X POST "$CG/channel/webhook/resume/$1" -H 'content-type: application/json' -d "$2"; }
tok_de_sessao() { local t; for _ in $(seq 1 25); do t=$(R hgetall "$TENANT:resume_tokens" | paste - - | grep -F "$1" | head -1 | cut -f1); [ -n "$t" ] && { echo "$t"; return; }; sleep 1; done; }

if [ "$(command curl -s -o /dev/null -w '%{http_code}' "$CG/health")" != "200" ]; then
  incon "channel-gateway fora do ar — B não medido"
else
  # G1 — a recusa da operadora chega; sem decisão, nada retoma
  if ! pd_fixture || ! pd_dispara; then
    incon "G1 não montou: $PD_ERRO"
  else
    TK=$(tok_de_sessao "$PD_N3")
    META=$(R get "$TENANT:resume_meta:$TK")
    case "$META" in *'"suspend_reason":"approval"'*) ;; *) incon "G1: o registro do token não diz suspend_reason=approval: ${META:0:160}" ;; esac
    C=$(ext "$TK" "{\"tenant_id\":\"$TENANT\",\"payload\":{}}")
    [ "$C" = "422" ] && [ "$(R hexists "$TENANT:resume_tokens" "$TK")" = "1" ] \
      && ok "aprovação sem decisão: 422 e o token segue vivo (aprovação nunca é o default)" \
      || falha "aprovação sem decisão: HTTP $C, token vivo=$(R hexists "$TENANT:resume_tokens" "$TK")"
    C=$(ext "$TK" "{\"tenant_id\":\"$TENANT\",\"payload\":{\"decision\":\"timeout\"}}")
    [ "$C" = "422" ] && ok "timeout vindo de fora: 422" || falha "timeout vindo de fora: HTTP $C"
    C=$(ext "$TK" "{\"tenant_id\":\"$TENANT\",\"payload\":{\"decision\":\"rejected\"}}")
    FIM=$(pd_fim "$PD_N3")
    [ "$C" = "200" ] && [ "$FIM" = "encerrar_rejeitado" ] \
      && ok "a operadora RECUSA pela porta externa: o processo terminou em encerrar_rejeitado" \
      || falha "recusa: HTTP $C, fim='${FIM:-<não terminou>}' (o red era seguir aprovado)"
  fi

  # G2 — controle: a operadora aprova pela porta externa (o pd_aprova usa essa porta)
  if pd_fixture && pd_dispara && pd_aprova; then
    ok "controle: a operadora APROVA pela porta externa e a pendência de confirmação nasce"
  else
    falha "controle: aprovar pela porta externa não fez a pendência nascer — $PD_ERRO"
  fi

  # G3 — a tarefa de aprovação humana não é decidida pela porta externa
  SID=$(command curl -s --max-time 20 -X POST "$CG/v1/channels/webhook/pool/gate_promocao_ia" -H 'content-type: application/json' \
        -d "{\"tenant_id\":\"$TENANT\"}" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("session_id",""))
except Exception: print("")')
  RT=""
  for _ in $(seq 1 25); do
    RT=$(R hget "$TENANT:ctx:$SID" core.workflow.delegate_resume_token | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("value") or "")
except Exception: print("")'); [ -n "$RT" ] && break; sleep 1
  done
  if [ -z "$RT" ]; then
    incon "G3: a tarefa de promoção não suspendeu com token (sessão=$SID)"
  else
    C=$(ext "$RT" "{\"tenant_id\":\"$TENANT\",\"payload\":{\"choice\":\"aprovar\",\"verification_class\":\"possessed\"}}")
    sleep 3
    [ "$C" = "401" ] && [ "$(R hexists "$TENANT:resume_tokens" "$RT")" = "1" ] \
      && ok "promoção de deploy pela porta externa, declarando 'possessed': 401 e a tarefa segue viva" \
      || falha "promoção pela porta externa: HTTP $C, tarefa viva=$(R hexists "$TENANT:resume_tokens" "$RT") (o red era efetuar_promocao)"
    # limpeza: o aprovador legítimo encerra a tarefa de fixture
    T_ADM=$(command curl -s -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
      -d "{\"email\":\"$AD_EMAIL\",\"password\":\"$AD_PASS\",\"tenant_id\":\"$TENANT\"}" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("access_token",""))
except Exception: print("")')
    SUB=$(printf '%s' "$T_ADM" | cut -d. -f2 | python3 -c 'import base64,json,sys
p=sys.stdin.read().strip(); p+="="*(-len(p)%4)
try: print(json.loads(base64.urlsafe_b64decode(p)).get("sub",""))
except Exception: print("")')
    INST="human-$SUB"
    R SET "$TENANT:instance:$INST" "{\"instance_id\":\"$INST\",\"agent_type_id\":\"human\",\"tenant_id\":\"$TENANT\",\"status\":\"ready\",\"max_concurrent\":5,\"current_sessions\":0,\"pools\":[\"aprovacao_deploy\"],\"source\":\"human_login\",\"execution_model\":\"stateful\"}" >/dev/null
    R SADD "$TENANT:pool:aprovacao_deploy:instances" "$INST" >/dev/null
    $COMPOSE exec -T routing-engine python3 -c "
import json,urllib.request
body=json.dumps({'tenant_id':'$TENANT','pool_id':'aprovacao_deploy','session_id':'$SID','instance_id':'$INST'}).encode()
req=urllib.request.Request('$RE/v1/work_queue/claim',data=body,headers={'content-type':'application/json'})
print(urllib.request.urlopen(req).read().decode())" </dev/null >/dev/null 2>&1
    C=$(command curl -s -o /dev/null -w '%{http_code}' --max-time 20 -X POST "$CG/v1/channels/webhook/resume/$RT" \
      -H 'content-type: application/json' -H "Authorization: Bearer $T_ADM" \
      -d "{\"tenant_id\":\"$TENANT\",\"pool_id\":\"aprovacao_deploy\",\"instance_id\":\"$INST\",\"payload\":{\"decision\":\"timeout\",\"source\":\"probe:apr11-cleanup\"}}")
    R SREM "$TENANT:pool:aprovacao_deploy:instances" "$INST" >/dev/null
    echo "   limpeza da tarefa de promoção: HTTP $C"
  fi
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo " FALHA ($FALHA)"; exit 1
elif [ "$INCONCL" -gt 0 ]; then
  echo " INCONCLUSIVO ($INCONCL)"; exit 2
fi
echo " OK"
exit 0
