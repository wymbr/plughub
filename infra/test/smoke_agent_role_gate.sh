#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# TESTEMUNHA — o eixo `agent_role` foi REMOVIDO (CAP-01 + CAP-03, 2026-09-01).
#
# ⚠️ RESÍDUO DE NOME, declarado de propósito: o arquivo se chama `..._gate.sh` e não
# há mais gate. Renomear tocaria 5 arquivos e invalidaria a referência HISTÓRICA no
# `CHANGELOG.md`, que cita este caminho como o instrumento da verificação da CAP-01 —
# e aquela citação está correta para a data dela. O nome é dívida; o cabeçalho é o
# mecanismo que impede alguém de ler o nome e concluir que existe gate.
#
# HISTÓRIA, em duas etapas, porque a segunda mudou o que dá para medir:
#
#   CAP-01 — o gate saiu. Ele não autenticava o chamador: o `session_token` carrega
#   `instance_id` (assinado) e as tools o descartavam, consultando o papel do
#   `participant_id` vindo do INPUT. Nesta etapa o T4 ainda conseguia virar o skill
#   para `executor` pela API e provar que o contexto era entregue mesmo assim.
#
#   CAP-03 — o campo saiu. Com ele, `PUT /v1/skills/{id}` passou a RECUSAR
#   `agent_role` com 422, então **a mutação do T4 deixou de ser possível**: não há
#   mais o bit a virar. O que era prova por ASSIMETRIA virou prova ESTRUTURAL — nada
#   escreve, nada lê, e mandar o campo é erro nomeado em vez de no-op silencioso.
#
#   T1  o avaliador legítimo continua servido (regressão principal: remover não pode
#       quebrar quem já passava)
#   T2  o hash da instância NÃO carrega mais `agent_role` — o carimbo do `agent_login`
#       saiu. É o inverso exato do T2 anterior, que contava instâncias COM o campo
#   T3  a procedência chega ao resultado (sem `evaluator_unknown`), e ela vem do
#       `agent_type_id` do token ASSINADO, não de hash indexado por input (CAP-02)
#   T4  LÁPIDE — `PUT` com `agent_role` responde 422 NOMEANDO, e não altera o skill.
#       Zod ignora chave desconhecida: sem esta recusa o remetente veria 200 sobre um
#       no-op. Foi o custo medido da remoção do `unrestricted` (2026-08-31)
#   T5  CONTROLE POSITIVO do T4 — o mesmo `PUT` SEM o campo continua funcionando. Sem
#       ele, um PUT que recusasse tudo passaria no T4
#
# O sinal de T1 é a linha `evaluation_context_get evidence:`, impressa DENTRO do
# handler. Não se mede "a avaliação completou": o passo `reason` a jusante chama o
# ai-gateway, e um 502 lá derrubaria o teste sem dizer nada sobre o que se quer medir.
# Foi o que aconteceu na 1ª rodada deste smoke (2026-07-28).
#
# Pré: stack demo no ar; agent-registry + mcp-server-plughub + orchestrator-bridge
#      REBUILDADOS; pool avaliador (avaliacao_ia) e ai-gateway operantes; jq.
#      ⚠️ Rodar de dentro do WSL — o Git Bash do Windows não tem `jq`.
#
# Este script NÃO muta mais o registry (a mutação que ele fazia virou 422). Não há
# estado a restaurar, e por isso não há trap de restore.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

EVAL="${EVAL:-http://localhost:3400}"
AREG="${AREG:-http://localhost:3300}"
TENANT="${TENANT:-tenant_demo}"
SVC="${SVC:-changeme_agent_registry_service_token_demo}"
JWT_SECRET="${JWT_SECRET:-changeme_auth_jwt_secret_demo_32c}"
EVALUATOR_POOL="${EVALUATOR_POOL:-avaliacao_ia}"
EVAL_POOL_ID="${EVAL_POOL_ID:-retencao_humano}"
EVAL_SKILL="${EVAL_SKILL:-skill_avaliacao_v1}"
COMPOSE="docker compose -f docker-compose.demo.yml"
CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'
FAIL=0

assert_true() { if [ "$2" = "true" ]; then echo "  ✓ $1"; else echo "  ✗ $1 (=$2)"; FAIL=1; fi; }
uuid() { cat /proc/sys/kernel/random/uuid; }

# Marco de início: o T2 usa isto para ignorar hashes de instância escritos ANTES
# desta execução (eles vivem 1 h e podem vir da imagem anterior). Ver a nota no T2.
T0=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)

reg_hdr=(-H "x-tenant-id: $TENANT" -H "x-service-token: $SVC")

# ── Bearer com evaluation.formularios:read_write ──────────────────────────────
BEARER=$(python3 -c "
import jwt, time
print(jwt.encode({'sub':'u_smoke_agent_role','tenant_id':'$TENANT','roles':['operator'],
  'module_config':{'evaluation':{'formularios':{'access':'read_write'}}},
  'accessible_pools':[],'iat':int(time.time()),'exp':int(time.time())+3600},
  '$JWT_SECRET', algorithm='HS256'))" 2>/dev/null)
[ -n "$BEARER" ] || { echo "✗ não consegui mintar o Bearer (pyjwt instalado?)"; exit 1; }
BH="Authorization: Bearer $BEARER"

echo "══ pré-condições ══"
# É `/health`, não `/v1/health` — medido. Escrevi o segundo por analogia com outros
# serviços e o smoke abortou nas pré-condições: analogia não é medição.
HEALTH=$($CURL -o /dev/null -w '%{http_code}' "$EVAL/health")
assert_true "evaluation-api no ar (HTTP $HEALTH)" \
  "$([ "$HEALTH" = "200" ] && echo true || echo false)"
[ "$FAIL" = 0 ] || exit 1

# ── helpers de cenário ────────────────────────────────────────────────────────
seed_and_dispatch() {
  local sid="$1" tag="$2" f c inst ctx now e1 e2 form_json raw
  raw=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -H "$BH" -d "{
    \"tenant_id\":\"$TENANT\",\"name\":\"$tag\",\"min_passing_score\":7.0,\"dimensions\":[
      {\"dimension_id\":\"atendimento\",\"name\":\"Atendimento\",\"weight\":1,\"criteria\":[
        {\"criterion_id\":\"cordialidade\",\"label\":\"Cordialidade\",\"type\":\"score\",
         \"weight\":1,\"max_score\":10,\"na_allowed\":true,
         \"description\":\"O agente foi cordial e resolveu o pedido do cliente.\",
         \"scoring_guidance\":\"Atribua 0-10 com base no transcript.\"}]}]}")
  f=$(printf '%s' "$raw" | jq -r '.form_id // .id // empty' 2>/dev/null)
  # Degradação nunca é silenciosa: sem o corpo da resposta, um 403 do gate ABAC e um
  # 422 de schema viram a mesma linha inútil ("form falhou").
  [ -n "$f" ] || { echo "  ✗ form falhou — resposta: $(printf '%s' "$raw" | head -c 400)" >&2; return 1; }
  $CURL -X POST "$EVAL/v1/evaluation/forms/$f/publish?tenant_id=$TENANT" $JSON -H "$BH" \
    -d '{"published_by":"smoke"}' >/dev/null
  form_json=$($CURL "$EVAL/v1/evaluation/forms/$f?tenant_id=$TENANT")

  c=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -H "$BH" -d "{
    \"tenant_id\":\"$TENANT\",\"name\":\"${tag}_camp\",\"form_id\":\"$f\",
    \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\",
    \"evaluator_pool\":\"$EVALUATOR_POOL\"}" | jq -r '.campaign_id // .id // empty')
  [ -n "$c" ] || { echo "  ✗ campaign falhou" >&2; return 1; }

  inst=$($CURL -X POST "$EVAL/v1/evaluation/instances" $JSON \
    -d "{\"tenant_id\":\"$TENANT\",\"campaign_id\":\"$c\",\"session_id\":\"$sid\"}" \
    | jq -r '.id // .instance_id // empty')
  [ -n "$inst" ] || { echo "  ✗ instance falhou" >&2; return 1; }

  now=$(date -u +%Y-%m-%dT%H:%M:%SZ); e1=$(uuid); e2=$(uuid)
  ctx=$(jq -nc --argjson form "$form_json" --arg sid "$sid" --arg cid "$c" \
        --arg iid "$inst" --arg now "$now" --arg e1 "$e1" --arg e2 "$e2" '{
    session_id:$sid, tenant_id:"tenant_demo",
    evaluation_form:$form, campaign_id:$cid, instance_id:$iid, comparison_mode:false,
    session_meta:{session_id:$sid, outcome:"resolved", channel:"webchat",
                  agent_type_id:"agente_retencao_ia_v1", started_at:$now,
                  closed_at:$now, duration_ms:120000},
    participants:[{participant_id:"p_agent",role:"primary",agent_type_id:"agente_retencao_ia_v1"},
                  {participant_id:"p_cust",role:"customer"}],
    sentiment:[{score:0.0},{score:0.6}], knowledge_snippets:[],
    events:[
      {event_id:$e1,type:"message",turn_index:0,author_role:"customer",author_id:"p_cust",
       content:"Quero cancelar meu plano.",original_content:"Quero cancelar meu plano."},
      {event_id:$e2,type:"message",turn_index:1,author_role:"agent",author_id:"p_agent",
       content:"Apliquei 20% de desconto por 6 meses. Fica bom assim?",
       original_content:"Apliquei 20% de desconto por 6 meses. Fica bom assim?"}]}')
  printf '%s' "$ctx" | $COMPOSE exec -T redis redis-cli -x SET "tenant_demo:replay:${sid}:context" >/dev/null
  $COMPOSE exec -T redis redis-cli EXPIRE "tenant_demo:replay:${sid}:context" 3600 >/dev/null

  $CURL -X POST "$EVAL/v1/evaluation/campaigns/$c/dispatch?tenant_id=$TENANT" \
    -H "$BH" $JSON -d '{}' >/dev/null
  echo "$c"
}

# A linha "evidence" só é impressa DENTRO do handler do evaluation_context_get, logo
# sua presença prova entrega e sua ausência prova o contrário. Sinal determinístico,
# independente do LLM.
gate_authorized() {
  $COMPOSE logs --since 15m mcp-server-plughub 2>/dev/null \
    | grep -q "evaluation_context_get evidence: session=$1" && echo true || echo false
}

wait_result() {  # wait_result <session_id> <segundos> → ecoa result_id (ou vazio)
  local sid="$1" secs="$2" rid=""
  for _ in $(seq 1 $((secs / 3))); do
    rid=$($CURL "$EVAL/v1/evaluation/results?tenant_id=$TENANT&session_id=$sid&limit=20" \
          | jq -r '(.results // .data // [])[0].id // empty')
    [ -n "$rid" ] && break
    sleep 3
  done
  echo "$rid"
}

# ── T1 · o avaliador legítimo continua servido ────────────────────────────────
echo
echo "══ T1 · avaliador real recebe o contexto (regressão principal) ══"
SID_OK="sess-arole-ok-$(date +%s)"
seed_and_dispatch "$SID_OK" "arole_ok" >/dev/null || exit 1
sleep 25

AUTHORIZED=$(gate_authorized "$SID_OK")
assert_true "contexto entregue ao avaliador legítimo" "$AUTHORIZED"
[ "$AUTHORIZED" = "true" ] || \
  echo "     $COMPOSE logs --since 10m skill-flow-service | grep THREW | tail -10"

RID=$(wait_result "$SID_OK" 90)
if [ -n "$RID" ]; then
  echo "  ✓ (bônus) avaliação completa produzida — result=$RID"
else
  echo "  ⓘ avaliação não fechou — esperado se o ai-gateway estiver fora."
fi

# ── T2 · o carimbo saiu do hash ───────────────────────────────────────────────
echo
echo "══ T2 · o hash da instância NÃO carrega mais agent_role ══"
# Inverso exato do T2 da versão anterior, que contava instâncias COM o campo (=6 na
# última medição antes da CAP-03).
#
# ⚠️ DUAS armadilhas, e a segunda custou uma leitura errada em 2026-09-01:
#
#  (1) testemunha de PRESENÇA é obrigatória — "zero instâncias com o campo" é também
#      o que se vê quando não há instância nenhuma, que é ausência de amostra;
#
#  (2) a janela tem de excluir hashes ANTERIORES ao deploy. O hash da instância vive
#      1 h (TTL do session_token), então logo após o rebuild convivem hashes escritos
#      pela imagem VELHA (com o campo) e pela nova (sem). Medido: 9 de 9 hashes ainda
#      tinham `agent_role` porque o login mais recente era de 18 min ANTES do restart.
#      Contar a população inteira responderia "a remoção não funcionou" sobre dados
#      que a remoção nunca teve a chance de tocar. O corte é `logged_in_at`, e o marco
#      é o início DESTE teste — o T1 acima já forçou um `agent_login` fresco.
read -r STAMPED TOTAL <<<"$($COMPOSE exec -T redis sh -lc "
  n=0; t=0
  for k in \$(redis-cli --scan --pattern 'tenant_demo:agent:instance:*' | grep -v ':conversations\$'); do
    li=\$(redis-cli HGET \"\$k\" logged_in_at)
    [ \"\$li\" \\> '$T0' ] || continue          # só logins posteriores ao início do teste
    t=\$((t+1))
    [ -n \"\$(redis-cli HGET \"\$k\" agent_role)\" ] && n=\$((n+1))
  done
  echo \"\$n \$t\"" 2>/dev/null | tr -d '\r' | tail -1)"
assert_true "existe instância para medir (testemunha de presença, =${TOTAL:-0})" \
  "$([ "${TOTAL:-0}" -ge 1 ] && echo true || echo false)"
assert_true "nenhuma instância com agent_role no hash (=${STAMPED:-0})" \
  "$([ "${STAMPED:-0}" -eq 0 ] && echo true || echo false)"
echo "     (antes da CAP-03 este contador era 6 — o agent_login carimbava o campo)"

# ── T3 · procedência ──────────────────────────────────────────────────────────
echo
echo "══ T3 · resultado não sai como evaluator_unknown ══"
if [ -n "$RID" ]; then
  RES=$($CURL "$EVAL/v1/evaluation/results/$RID?tenant_id=$TENANT")
  assert_true "resultado sem 'evaluator_unknown'" \
    "$(echo "$RES" | grep -q 'evaluator_unknown' && echo false || echo true)"
  echo "     (e a fonte é o agent_type_id do session_token ASSINADO — CAP-02 —,"
  echo "      não um hash indexado pelo participant_id do input)"
else
  echo "  ⊘ pulado (T1 não produziu resultado)"
fi

# ── T4 · LÁPIDE ───────────────────────────────────────────────────────────────
echo
echo "══ T4 · LÁPIDE · PUT com agent_role responde 422 NOMEANDO ══"
BODY_BASE=$($CURL "$AREG/v1/skills/$EVAL_SKILL" -H "x-tenant-id: $TENANT" \
  | jq -c 'del(.id,.tenant_id,.status,.created_by,.created_at,.updated_at,
               .deploy_status,.published_at,.flow_model,.unpublished_draft,.agent_role)
           | with_entries(select(.value != null))')
BODY_WITH=$(printf '%s' "$BODY_BASE" | jq -c '. + {agent_role:"evaluator"}')
$CURL -o /tmp/arole_422.$$ -w '%{http_code}' \
  -X PUT "$AREG/v1/skills/$EVAL_SKILL" "${reg_hdr[@]}" $JSON -d "$BODY_WITH" > /tmp/arole_code.$$
CODE=$(cat /tmp/arole_code.$$); RESP=$(cat /tmp/arole_422.$$ | tr -d '\n')
rm -f /tmp/arole_422.$$ /tmp/arole_code.$$
assert_true "PUT com agent_role recusado com 422 (HTTP $CODE)" \
  "$([ "$CODE" = "422" ] && echo true || echo false)"
# Recusa muda seria quase tão ruim quanto no-op silencioso: quem manda o campo tem de
# saber que ele foi REMOVIDO, não que "deu erro".
assert_true "a recusa nomeia o campo e diz que foi removido" \
  "$(echo "$RESP" | grep -q 'agent_role_removed' && echo "$RESP" | grep -qi 'REMOVIDO' \
     && echo true || echo false)"

# ── T5 · CONTROLE POSITIVO do T4 ──────────────────────────────────────────────
echo
echo "══ T5 · CONTROLE POSITIVO · o mesmo PUT SEM o campo continua funcionando ══"
OKCODE=$($CURL -o /dev/null -w '%{http_code}' \
  -X PUT "$AREG/v1/skills/$EVAL_SKILL" "${reg_hdr[@]}" $JSON -d "$BODY_BASE")
assert_true "PUT sem agent_role aceito (HTTP $OKCODE)" \
  "$([ "$OKCODE" = "200" ] || [ "$OKCODE" = "201" ] && echo true || echo false)"
echo "     (sem este ramo, um PUT que recusasse QUALQUER corpo passaria no T4)"

echo
[ "$FAIL" = 0 ] \
  && echo "✅ testemunha OK — nada escreve nem lê agent_role; mandá-lo é erro nomeado" \
  || { echo "❌ testemunha do eixo removido com falhas"; exit 1; }
