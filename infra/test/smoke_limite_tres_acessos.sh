#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Aumento de limite — aceite dos TRÊS ACESSOS (Fase 1)
# Design: docs/product/limite-credito-3-niveis-design.md
#
# O que este portão julga, e por quê cada asserção existe:
#
#   1. O processo (N3) suspende na análise humana e o item cai na fila PULL.
#   2. ACESSO 2 — a pendência do cliente traz o STATUS de negócio e o cartão
#      MASCARADO. Esta é a regressão do `_pending_context_preview` generalizado:
#      antes ele era hardcoded em portabilidade e DESCARTAVA em silêncio qualquer
#      chave que não fosse operadora_destino/numero_atual.
#   3. A spec de preview é ALLOWLIST, não passthrough: tanto o vencimento do
#      cartão quanto o CPF (âncora de identidade) viajam no delegate.context e
#      NÃO podem aparecer no preview, porque não foram declarados. Sem esta
#      asserção, um preview que vaze tudo passaria despercebido.
#   4. ACESSO 3 — depois da decisão, a pendência vira `policy: auto` e carrega o
#      resultado. É esse `auto` que faz o agente de entrada distinguir "em análise"
#      de "resultado pronto" sem nenhum flag nosso.
#
# NOTA sobre a sobrescrita: os dois delegates rodam na MESMA sessão do N3, e o
# índice de pendências é chaveado por session_id (identity/index.py:266). Logo o
# segundo pending SUBSTITUI o primeiro — aqui isso é desejado (o cliente vê sempre
# o estado corrente), mas é a mesma mecânica descrita em §7.5 do design doc, que
# em outro fluxo seria perda de dado. A asserção 4 depende dela.
#
# NÃO julga: o render do formulário no webchat (é UI) nem o claim direcionado.
#
# Veredicto: 0 = verde · 1 = DEFEITO · 2 = INCONCLUSIVO (pré-condição falhou).
# Requer: curl, jq, docker compose. Uso: bash infra/test/smoke_limite_tres_acessos.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
CG="http://localhost:8010"
AUTH="${AUTH:-http://localhost:3202}"     # 3200 do host é o ai-gateway, não a auth-api
DIALOG="http://localhost:3760"
POOL_WH="limite_processo"                 # N3 — o processo
POOL_PULL="aprovacao_credito"             # onde o aprovador reivindica
AD_EMAIL="${AD_EMAIL:-admin@plughub.local}"; AD_PASS="${AD_PASS:-changeme_admin}"

# Dados do caso. CPF novo a cada execução: pendência velha do mesmo CPF faria a
# asserção 2 ler o pedido ANTERIOR e passar por acidente. O CPF é a âncora de
# identidade do cenário (substituiu o telefone) — indexa a pendência e é lido
# em @ctx.session.cpf pelo delegate de aprovação e pelo trigger de entrega.
CPF="${CPF:-529$(date +%s | tail -c 9)}"
CARD="${CARD:-4111111111111234}"          # últimos 4 = 1234 → preview ***1234
VENCIMENTO="${VENCIMENTO:-1230}"          # MM/AA — substituiu o CPF do titular no form do cartão
LIMITE="${LIMITE:-12000}"
LIMITE_OK="${LIMITE_OK:-9000}"            # o aprovador aprova um valor MENOR

CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'
redis() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null; }
chq()   { $COMPOSE exec -T clickhouse clickhouse-client -d plughub_demo --query "$1" < /dev/null 2>&1; }

PASS=0; FAIL=0
ok()   { echo "   ✅ $1"; PASS=$((PASS+1)); }
bad()  { echo "   ❌ $1"; FAIL=$((FAIL+1)); }
die()  { echo "   ⚠️  INCONCLUSIVO (abortou): $1"; exit 2; }

echo "══ 0) login — o resume de aprovação exige ABAC approvals.decide ══"
LOGIN=$($CURL -X POST "$AUTH/auth/login" $JSON \
  -d "{\"email\":\"$AD_EMAIL\",\"password\":\"$AD_PASS\",\"tenant_id\":\"$TENANT\"}")
TOK=$(echo "$LOGIN" | jq -r '.access_token // empty')
[ -n "$TOK" ] || die "login falhou em $AUTH — ${LOGIN:0:200}"
SUB=$(echo "$TOK" | cut -d. -f2 | tr '_-' '/+' \
  | { read -r p; printf '%s' "$p$(printf '%*s' $(( (4 - ${#p} % 4) % 4 )) '' | tr ' ' '=')"; } \
  | base64 -d 2>/dev/null | jq -r '.sub // empty')
[ -n "$SUB" ] || die "não consegui extrair o sub do JWT"
INST="human-${SUB}"
echo "   ✓ aprovador = $INST · CPF do caso = $CPF"

echo "══ 1) os DialogForms estão publicados? ══"
# Auto-semeia como o smoke_approval_segment_closes.sh faz: `infra/dialog` é volume
# montado no dialog-seed, mas o job só roda no boot — numa stack já no ar os forms
# novos ainda não existem. Semear aqui evita transformar uma pré-condição trivial
# em INCONCLUSIVO a cada primeira execução.
NEED_SEED=""
for F in dialog_limite_solicitacao dialog_limite_aprovacao; do
  $CURL -f "$DIALOG/v1/dialog/forms/$F?status=published" -H "X-Tenant-ID: $TENANT" >/dev/null 2>&1 \
    || NEED_SEED=1
done
if [ -n "$NEED_SEED" ]; then
  echo "   → semeando via infra/test/seed_dialog_limite_forms.sh"
  DIALOG_API="$DIALOG" TENANT="$TENANT" bash infra/test/seed_dialog_limite_forms.sh \
    || die "não consegui semear os forms — dialog-api em $DIALOG está no ar?"
  for F in dialog_limite_solicitacao dialog_limite_aprovacao; do
    $CURL -f "$DIALOG/v1/dialog/forms/$F?status=published" -H "X-Tenant-ID: $TENANT" >/dev/null 2>&1 \
      || die "form '$F' segue não publicado depois do seed"
  done
fi
echo "   ✓ ambos publicados"

echo "══ 1a) o form de aprovação tem FIELDS (e não question nodes soltos)? ══"
# O passo 7 abaixo fabrica `payload.edits` — e por isso NÃO consegue provar que o
# Console produz esse payload. Em 2026-08-11 o form usava question nodes com
# output_key: o aprovador digitava e o valor sumia, porque o ApprovalPanel monta
# `edits` a partir dos FIELDS e o payload de decisão não carrega `answers`.
# Resultado ao vivo: "Novo limite: R$ " — vazio, com o smoke em 16/0.
# Esta asserção é a ponte entre o contrato do form e o que o workflow lê.
APROV=$($CURL "$DIALOG/v1/dialog/forms/dialog_limite_aprovacao?status=published" -H "X-Tenant-ID: $TENANT" 2>/dev/null)
for F in limite_aprovado parecer; do
  echo "$APROV" | jq -e --arg f "$F" '[.. | objects | select(.fields?) | .fields[] | select(.id == $f)] | length > 0' >/dev/null 2>&1 \
    || die "o form publicado NÃO expõe o field '$F' em fields[]. O aprovador digitaria e o
        valor não chegaria a \$.pipeline_state.aprovar.edits.$F — o cliente receberia o
        resultado com o campo em branco. Republique: bash infra/test/seed_dialog_limite_forms.sh"
done
echo "   ✓ fields limite_aprovado e parecer expostos"

echo "══ 1b) o pool $POOL_WH existe no registry? ══"
# Pré-condição barulhenta de propósito: skills e pools novos entram pelo
# RegistrySyncer no restart do orchestrator-bridge (ambos os diretórios são volume
# montado). Sem isso o trigger do passo 4 devolveria 404/500 e a mensagem culparia
# o fluxo, não o provisionamento.
POOLS=$($CURL "http://localhost:3300/v1/pools" -H "x-tenant-id: $TENANT" 2>/dev/null)
count_pool() { echo "$POOLS" | jq -r --arg p "$1" '[.. | objects | select(.pool_id? == $p)] | length' 2>/dev/null; }

# PREFLIGHT DO LEITOR, antes de julgar o alvo. Sem isto, uma leitura quebrada
# (401, porta errada, shape inesperado) devolveria zero para QUALQUER pool e o
# veredicto acusaria o provisionamento — culpando o lugar errado com convicção.
# `sac_ia` é a testemunha: existe desde sempre no tenant demo.
if [ "$(count_pool sac_ia)" != "1" ]; then
  die "não consegui LER o registry em :3300 (o pool-testemunha sac_ia não apareceu).
        Isto é falha do leitor, não do provisionamento — nada se conclui sobre
        '$POOL_WH'. Resposta: ${POOLS:0:200}"
fi
[ "$(count_pool "$POOL_WH")" = "1" ] \
  || die "registry LIDO com sucesso e o pool '$POOL_WH' NÃO está lá. Rode:
        docker compose -f docker-compose.demo.yml restart orchestrator-bridge
        (infra/registry e packages/skill-flow-engine/skills são volumes montados —
         skills e pools INÉDITOS entram por seed-if-absent, sem rebuild nem reconcile)"
for P in limite_ia limite_retorno aprovacao_credito; do
  [ "$(count_pool "$P")" = "1" ] || die "pool '$P' ausente — o RegistrySyncer aplicou
        parcialmente. Veja: docker compose -f docker-compose.demo.yml logs --tail=80 orchestrator-bridge"
done
echo "   ✓ os 4 pools do cenário estão registrados"

echo "══ 1c) os pools de IA têm slot 'current' EXECUTÁVEL? ══"
# Existir pool ≠ existir skill rodável. O bridge executa o SNAPSHOT do slot `current`,
# não o `skill.flow` — e um pool sem slot aceita delegate, parqueia a pendência e
# NUNCA roda o agente. Foi o caso real de limite_retorno: o skill falhou o create com
# 422 (classification.type inválido), o slot foi 404, e mesmo assim o passo 8 ficava
# VERDE, porque a pendência é escrita por handle_delegate_conference antes de qualquer
# agente executar. Sem esta asserção o acesso 3 estaria quebrado com o portão em verde.
for P in limite_ia limite_processo limite_entrega limite_retorno; do
  SLOTS=$($CURL "http://localhost:3300/v1/pools/$P/slots" -H "x-tenant-id: $TENANT" 2>/dev/null)
  CUR=$(printf '%s' "$SLOTS" | python3 -c '
import json,sys
try: d = json.load(sys.stdin)
except Exception: print(""); raise SystemExit
slots = d.get("slots", d) if isinstance(d, dict) else d
cur = None
if isinstance(slots, dict):  cur = slots.get("current")
elif isinstance(slots, list): cur = next((s for s in slots if s.get("slot") == "current"), None)
if not cur: print(""); raise SystemExit
sk = cur.get("skill_id") or ""
print(sk if (cur.get("yaml_snapshot") or cur.get("flow")) else "")
' 2>/dev/null)
  [ -n "$CUR" ] || die "pool '$P' SEM slot 'current' executável (skill ausente ou snapshot vazio).
        O delegate para ele parqueia a pendência e o agente nunca roda — falha que NÃO
        aparece nos passos seguintes. Cheque o create do skill:
        docker compose -f docker-compose.demo.yml logs orchestrator-bridge | grep -E 'PUT returned|deploy slot'"
  echo "   ✓ $P → $CUR"
done

echo "══ 2) resolve a identidade do cliente (Lookup 1) ══"
RESOLVE=$($CURL -X POST "$CG/v1/channels/webhook/identity/resolve" $JSON \
  -d "{\"tenant_id\":\"$TENANT\",\"provision\":true,\"anchors\":[{\"kind\":\"cpf\",\"value\":\"$CPF\"}]}")
CUST=$(echo "$RESOLVE" | jq -r '.customer_id // empty')
[ -n "$CUST" ] || die "identity/resolve não devolveu customer_id — ${RESOLVE:0:200}"
echo "   ✓ customer_id = $CUST"

echo "══ 3) semeia o aprovador PRONTO no pool pull ══"
# No fluxo real quem registra a instância é o login do Console (abrir o WS É o
# agent_login). O smoke não abre navegador; semeia da mesma forma que o
# smoke_directed_pull.sh, porque o gate que interessa aqui é o do claim.
redis SET "${TENANT}:instance:${INST}" \
  "{\"instance_id\":\"$INST\",\"agent_type_id\":\"human\",\"tenant_id\":\"$TENANT\",\"status\":\"ready\",\"max_concurrent\":5,\"current_sessions\":0,\"pools\":[\"$POOL_PULL\"],\"source\":\"human_login\",\"execution_model\":\"stateful\"}" >/dev/null
redis SADD "${TENANT}:pool:${POOL_PULL}:ready"     "$INST" >/dev/null
redis SADD "${TENANT}:pool:${POOL_PULL}:instances" "$INST" >/dev/null
echo "   ✓ $INST pronto em $POOL_PULL"

echo "══ 4) dispara o processo (o que o agente de entrada faz via workflow_trigger) ══"
TRIG=$($CURL -X POST "$CG/v1/channels/webhook/pool/$POOL_WH" $JSON -d "{
  \"tenant_id\": \"$TENANT\",
  \"context\": {
    \"session.cpf\":                \"$CPF\",
    \"session.customer_id\":        \"$CUST\",
    \"session.numero_cartao\":      \"$CARD\",
    \"session.vencimento_cartao\":  \"$VENCIMENTO\",
    \"session.limite_solicitado\":  \"$LIMITE\"
  }}")
SID=$(echo "$TRIG" | jq -r '.session_id // empty')
[ -n "$SID" ] || die "trigger não devolveu session_id — ${TRIG:0:200}"
echo "   ✓ sessão do processo: $SID"

echo "══ 5) o item de análise parqueia na fila pull de $POOL_PULL? ══"
QUEUED=""
for _ in $(seq 1 25); do
  Z=$(redis ZSCORE "${TENANT}:pool:${POOL_PULL}:queue" "$SID" | tr -d '\r')
  [ -n "$Z" ] && { QUEUED=1; break; }
  sleep 1
done
[ -n "$QUEUED" ] || die "o item não entrou na fila pull em 25 s — sem item não há
        aprovação a julgar, e as asserções seguintes não teriam sentido."
ok "item de análise na fila pull"

echo "══ 6) ACESSO 2 — a pendência traz status e cartão MASCARADO ══"
PEND=""
for _ in $(seq 1 15); do
  PEND=$($CURL "$CG/v1/channels/webhook/pending/by-customer/$CUST?tenant_id=$TENANT")
  [ "$(echo "$PEND" | jq -r '.found // false')" = "true" ] && break
  sleep 1
done
echo "   → $(echo "$PEND" | jq -c '{found, count, policy, context}' 2>/dev/null | head -c 300)"

[ "$(echo "$PEND" | jq -r '.found // false')" = "true" ] \
  && ok "pendência encontrada sob o customer_id" \
  || die "nenhuma pendência indexada. Suspeito nº 1: contact_identifier ausente no
        delegate.context — é dele que _anchors_from_context deriva a âncora."

[ "$(echo "$PEND" | jq -r '.policy // empty')" = "offer" ] \
  && ok "policy = offer (o cliente escolhe: consultar ou cancelar)" \
  || bad "policy esperada 'offer', veio '$(echo "$PEND" | jq -r '.policy // "<ausente>"')'"

ST=$(echo "$PEND" | jq -r '.context.status // empty')
[ "$ST" = "Em análise pelo time de crédito" ] \
  && ok "status de negócio chegou ao preview: '$ST'" \
  || bad "status ausente ou diferente: '${ST:-<ausente>}' — sem ele o acesso 2 não
        tem o que mostrar (pending_workflow_get não devolve etapa do workflow)"

NC=$(echo "$PEND" | jq -r '.context.numero_cartao // empty')
[ "$NC" = "***1234" ] \
  && ok "cartão mascarado no preview: $NC" \
  || bad "cartão deveria vir '***1234', veio '${NC:-<ausente>}'"

LS=$(echo "$PEND" | jq -r '.context.limite_solicitado // empty')
[ "$LS" = "$LIMITE" ] \
  && ok "valor solicitado em claro no preview (declarado plain): $LS" \
  || bad "limite_solicitado deveria vir '$LIMITE', veio '${LS:-<ausente>}'"

# A asserção que impede o modo de falha mais perigoso: preview virar passthrough.
if [ "$(echo "$PEND" | jq -r '.context | has("vencimento_cartao")')" = "false" ]; then
  ok "vencimento do cartão NÃO vazou — a spec de preview é allowlist, não passthrough"
else
  bad "vencimento_cartao apareceu no preview sem estar declarado na spec —
        _pending_context_preview virou passthrough. É vazamento, não cosmético."
fi

# A âncora de identidade (cpf) também não é dado de tela — mesma allowlist, mesmo risco.
if [ "$(echo "$PEND" | jq -r '.context | has("cpf")')" = "false" ]; then
  ok "CPF (âncora de identidade) NÃO vazou no preview"
else
  bad "CPF apareceu no preview sem estar declarado na spec — vazamento da âncora
        de identidade, não só de dado de tela."
fi

# Regressão do namespace de âncoras: a pendência TEM de cair sob o customer_id que o
# Lookup 1 devolveu. Quando o delegate.context tinha uma chave `cpf`, ela virava a
# âncora, o resolver provisionava um cliente NOVO e a pendência ficava indexada sob
# um id que ninguém consulta — escrita com sucesso e invisível para sempre.
CID_PEND=$(echo "$PEND" | jq -r '.customer_id // empty')
[ "$CID_PEND" = "$CUST" ] \
  && ok "pendência indexada sob o MESMO customer_id do Lookup 1" \
  || bad "pendência sob '$CID_PEND', mas o Lookup 1 devolveu '$CUST' — âncora divergente
        (suspeito: chave do delegate.context colidindo com phone/email/cpf/princ)"

echo "══ 7) o aprovador reivindica e decide ══"
CLAIM=$($COMPOSE exec -T routing-engine python3 -c "
import json,urllib.request
body=json.dumps({'tenant_id':'$TENANT','pool_id':'$POOL_PULL','session_id':'$SID','instance_id':'$INST'}).encode()
req=urllib.request.Request('http://localhost:3550/v1/work_queue/claim',data=body,headers={'content-type':'application/json'})
print(urllib.request.urlopen(req).read().decode())
" < /dev/null 2>&1)
echo "$CLAIM" | grep -q '"claimed": *true' \
  || die "claim recusado — ${CLAIM:0:200}"
ok "item reivindicado pelo aprovador"

RTOK=$(redis HGET "${TENANT}:ctx:${SID}" "core.workflow.delegate_resume_token" | tr -d '\r' | jq -r '.value // empty' 2>/dev/null)
[ -n "$RTOK" ] || die "não achei o resume token no ctx da sessão $SID"

# `pool_id`+`instance_id` são o binding do claimante (o ingress exige caller == claimant).
RCODE=$(curl -s -o /tmp/_limite_resume -w '%{http_code}' --max-time 20 \
  -X POST "$CG/v1/channels/webhook/resume/$RTOK" $JSON \
  -H "Authorization: Bearer $TOK" \
  -d "{\"tenant_id\":\"$TENANT\",\"pool_id\":\"$POOL_PULL\",\"instance_id\":\"$INST\",
       \"payload\":{\"choice\":\"aprovar\",\"edits\":{\"limite_aprovado\":\"$LIMITE_OK\",\"parecer\":\"Renda compatível.\"}}}")
echo "   → HTTP $RCODE · $(head -c 200 /tmp/_limite_resume)"
case "$RCODE" in
  200|202) ok "decisão submetida (HTTP $RCODE)" ;;
  401|403) die "resume recusado por AUTORIZAÇÃO (HTTP $RCODE). Achado sobre o GATE,
        não sobre o processo: só admin@plughub.local tem approvals.decide no seed." ;;
  *)       die "resume não completou (HTTP $RCODE) — o passo 8 não tem o que julgar." ;;
esac

echo "══ 7b) o pacote de aprovação foi DESARMADO após a decisão? ══"
# Regressão do defeito observado ao vivo em 2026-08-11: as tags do pacote não são
# limpas pela plataforma, então o delegate de PARKING herdava um snapshot que o
# Console lê como tarefa de aprovação. O mesmo formulário reaparecia no inbox e,
# ao decidir, resumia o PARKING — o processo terminava e o cliente nunca recebia o
# resultado. Só aparece com humano na tela; por isso a asserção é sobre o ESTADO,
# não sobre a tela.
# PREFLIGHT: a correção está DEPLOYADA? O bridge executa o snapshot do slot, não o
# arquivo — e os skills são seed-if-absent, então editar o YAML de um skill já criado
# é no-op sem REGISTRY_SYNC_RECONCILE=true. Sem esta checagem, o 7b acusaria "a
# correção não funciona" quando o fato é "a correção não está lá". Julgar o efeito de
# algo que não foi aplicado é o modo mais convincente de mandar consertar o certo.
# São DOIS estados diferentes com remédios diferentes, e confundi-los custou um ciclo:
#   skill sem o step  → o YAML não foi republicado           → reconcile
#   skill COM, slot sem → o snapshot do slot está velho       → set-next + promote
# O bridge executa o SNAPSHOT, não o skill.flow. Republicar não promove.
SK=$($CURL "http://localhost:3300/v1/skills/skill_limite_processo_v1" -H "x-tenant-id: $TENANT" 2>/dev/null)
SNAP=$($CURL "http://localhost:3300/v1/pools/limite_processo/slots"   -H "x-tenant-id: $TENANT" 2>/dev/null)
SK_OK=0;   printf '%s' "$SK"   | grep -q 'limpar_form_do_pacote' && SK_OK=1
SNAP_OK=0; printf '%s' "$SNAP" | grep -q 'limpar_form_do_pacote' && SNAP_OK=1

if [ "$SK_OK" = "0" ]; then
  die "o SKILL skill_limite_processo_v1 não contém limpar_form_do_pacote — o YAML não foi
        republicado (skills são seed-if-absent; editar o arquivo é no-op). NADA se conclui
        sobre a correção. Aplique:
          REGISTRY_SYNC_RECONCILE=true docker compose -f docker-compose.demo.yml up -d orchestrator-bridge
          sleep 25 && docker compose -f docker-compose.demo.yml up -d orchestrator-bridge"
fi
if [ "$SNAP_OK" = "0" ]; then
  die "o SKILL já tem o step, mas o SNAPSHOT do slot 'current' NÃO — e é o snapshot que o
        bridge executa. Republicar não promove. Re-snapshote:
          AR=http://localhost:3300
          H='-H x-tenant-id:$TENANT -H x-service-token:changeme_agent_registry_service_token_demo -H content-type:application/json'
          curl -s -X PUT  \$AR/v1/pools/limite_processo/slots/next \$H \\
               -d '{\"skill_id\":\"skill_limite_processo_v1\",\"config_json\":{\"max_concurrent_sessions\":10}}'
          curl -s -X POST \$AR/v1/pools/limite_processo/promote \$H"
fi

sleep 3
FID=$(redis HGET "${TENANT}:ctx:${SID}" "core.workflow.dialog_form_id" | tr -d '\r' | jq -r '.value // empty' 2>/dev/null)
DEC=$(redis HGET "${TENANT}:ctx:${SID}" "session.decisions"      | tr -d '\r' | jq -r '.value // empty' 2>/dev/null)
if [ -z "$FID" ] && [ -z "$DEC" ]; then
  ok "dialog_form_id e decisions limpos — o parking não se passa por aprovação"
else
  bad "pacote de aprovação AINDA ARMADO após a decisão (dialog_form_id='${FID:-}' decisions='${DEC:0:40}').
        O item de parking vai reaparecer no Console como tarefa de aprovação e,
        se alguém decidir, consome o acesso 3 do cliente."
fi

echo "══ 7c) a sessão da ANÁLISE fechou e saiu da fila? ══"
# Regressão do defeito observado ao vivo em 2026-08-11: mantida viva depois da
# decisão, a sessão da análise voltava para a fila pull assim que o WS do aprovador
# caía (trocar de aba bastava) — `remove_conversation` restaura a membership dos SETs
# do pool. O aprovador via Claim de novo sobre um item já decidido.
# A espera pelo cliente agora é OUTRO processo (limite_entrega); esta sessão fecha.
# ⚠️ NÃO usar a chave Redis `{t}:session:{id}:status`: ela é escrita como "active" pelo
# caminho de resume (webhook.py:1254) e quem escreveria "closed" é o CORE, que não
# participa de sessão webhook. Ela reporta `active` para TODA workflow encerrada —
# inclusive as de portabilidade. É chave órfã, e medi-la acusou defeito onde o bridge
# tinha feito tudo certo (contact_closed + session_closed + _destroy_conference).
#
# O fato durável é o `conversations.session_closed`, que aterrissa em analytics.sessions.
# Poll porque a ingestão é assíncrona; o tempo entra no veredicto.
ST=""; WAITED=0
for i in $(seq 1 45); do
  ST=$(chq "SELECT status FROM sessions FINAL
             WHERE tenant_id='$TENANT' AND session_id='$SID'" | tr -d '\r' | head -1)
  [ -n "$ST" ] && [ "$ST" != "active" ] && { WAITED=$i; break; }
  sleep 1
done
if [ -n "$ST" ] && [ "$ST" != "active" ]; then
  ok "contato da análise ENCERRADO (status=$ST) após ${WAITED}s"
  [ "$WAITED" -le 15 ] || echo "      ⚠️  levou ${WAITED}s — ingestão lenta ou teardown segurado"
else
  bad "contato da análise não encerrou em 45s (status='${ST:-<sem linha>}'). Se a sessão
        seguir viva, ela reentra na fila de $POOL_PULL na primeira queda de WS do aprovador.
        Confira o caminho real no bridge:
          docker compose -f docker-compose.demo.yml logs orchestrator-bridge | grep '$SID'"
fi

QZ=$(redis ZSCORE "${TENANT}:pool:${POOL_PULL}:queue" "$SID" | tr -d '\r')
[ -z "$QZ" ] \
  && ok "sessão da análise NÃO está mais na fila pull" \
  || bad "sessão da análise de volta na fila de $POOL_PULL (score=$QZ) — item já
        decidido sendo re-oferecido para Claim"

echo "══ 8) ACESSO 3 — a pendência vira 'auto' e o resultado está na journey ══"
# O parking (agora um `collect`, F0.2) sobrescreve a pendência da análise no índice
# (mesma chave session_id). É a substituição que representa "o estado mudou".
#
# MUDANÇA DE TRANSPORTE (2026-08-12, F0.2/alternativa A): o resultado NÃO viaja mais
# no `context_preview` da pendência. `CollectStepSchema` não tem `context`, e não
# precisa ter: resultado é fato do PROCESSO, e mora em `journey.*` — hash da raiz
# canônica, TTL 30d, migrado pelo journey_merge. A pendência voltou a ser o que o
# nome diz (ponteiro de retomada), em vez de carregar carga de negócio.
#
# Por isso estas duas asserções mudaram de ENDEREÇO, não de intenção: continuam
# provando que a decisão do aprovador chega ao cliente no acesso 3.
PEND2=""; POL2=""
for _ in $(seq 1 20); do
  PEND2=$($CURL "$CG/v1/channels/webhook/pending/by-customer/$CUST?tenant_id=$TENANT")
  POL2=$(echo "$PEND2" | jq -r '.policy // empty')
  [ "$POL2" = "auto" ] && break
  sleep 1
done
echo "   → $(echo "$PEND2" | jq -c '{found, policy, context}' 2>/dev/null | head -c 300)"

[ "$POL2" = "auto" ] \
  && ok "policy = auto (o agente de entrada retoma direto e entrega)" \
  || bad "policy esperada 'auto', veio '${POL2:-<ausente>}' — sem isso o acesso 3
        cairia no menu do acesso 2 e o cliente não receberia o resultado"

# A raiz é o ENDEREÇO do contexto do processo. Sem ela não há o que ler, e o teste
# tem de dizer isso — comparar contra vazio e seguir seria passar por coincidência.
JROOT=$(echo "$PEND2" | jq -r '.root_session_id // empty')
[ -n "$JROOT" ] \
  && ok "pendência carrega a raiz da journey: $JROOT" \
  || bad "root_session_id ausente na pendência — sem ele o acesso 3 não tem como
        unificar a journey nem achar o contexto do processo"

JKEY="${TENANT}:ctx:journey:${JROOT}"
RES=$(redis HGET "$JKEY" "journey.resultado" | tr -d '\r' | jq -r '.value // empty' 2>/dev/null)
[ "$RES" = "aprovado" ] \
  && ok "resultado no contexto da journey: $RES" \
  || bad "journey.resultado deveria ser 'aprovado', veio '${RES:-<ausente>}' — em $JKEY.
        Ausente costuma significar que o context_set gravou no hash da SESSÃO: só tag
        com prefixo 'journey.' é desviada por writeContextTag."

LA=$(redis HGET "$JKEY" "journey.limite_aprovado" | tr -d '\r' | jq -r '.value // empty' 2>/dev/null)
[ "$LA" = "$LIMITE_OK" ] \
  && ok "limite aprovado pelo humano chegou à journey: R$ $LA (pedido: R$ $LIMITE)" \
  || bad "journey.limite_aprovado deveria ser '$LIMITE_OK', veio '${LA:-<ausente>}' — a
        edição do aprovador ($.pipeline_state.aprovar.edits) não chegou ao contexto
        do processo"

echo
echo "═══════════════════════════════════════════════════════════════"
echo "  sessão do processo : $SID"
echo "  customer_id        : $CUST   (CPF $CPF)"
echo "  ✅ $PASS   ❌ $FAIL"
echo "═══════════════════════════════════════════════════════════════"
[ "$FAIL" -eq 0 ] || exit 1
exit 0
