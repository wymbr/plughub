#!/usr/bin/env bash
# Fase A do ADR `adr-work-item-requeue-and-agent-affinity.md` (D6) —
# POSSE DO ITEM DE TRABALHO, conferida contra um registro que sobrevive à lease.
#
# O QUE ESTE SMOKE PROVA (e o que NÃO prova)
#
# Prova, contra o árbitro real (routing-engine, POST /v1/work_queue/{claim,holder,
# release,expire}), que:
#   1. o claim grava um registro durável de posse, com TTL casado ao PRAZO DO ITEM;
#   2. com a lease apagada — o estado em que o submit de fato acontece — o holder
#      AINDA responde quem detém (via="record"). Antes da Fase A respondia
#      `found=false`, e o check A5 do channel-gateway degradava para permissivo;
#   3. depois do release, ninguém detém E o item está na fila: `found=false,
#      in_queue=true`. É a resposta POSITIVA que permite ao submit recusar, em vez
#      do silêncio que só permitia deixar passar;
#   4. o expire recupera o dono pelo registro (`claimed_via="record"`) quando a
#      lease já venceu — o cenário que motiva o expire;
#   5. (Fase B / D1) a devolução pelo `work_task_release` PRESERVA o pacote — os
#      quatro campos que o re-publish de seis campos apagava (`assigned_to`,
#      `conference_id`, `work_item_deadline`, `auto_attend`) e o `first_queued`,
#      que fixa a idade real do item na inbox.
#
# NÃO prova o F5 ponta a ponta (Console reconectando, bridge re-roteando). Esse
# trecho exige navegador e está no probe manual `probe_console_restore_after_reload.sh`;
# a limpeza do registro no re-parque é coberta pelo pytest
# `test_claim_possession_record.py::test_requeue_clears_record`. Dizer aqui que o
# F5 está coberto seria comprar confiança sem dar nada.
#
# PREVISÃO (escrita antes de rodar — o valor confere ou o smoke está errado):
#   prazo do item = 2 h  ⇒  TTL do registro ≈ 7200 s, e em todo caso > 180 s
#   (`routing.claim_lease_s`). TTL <= 180 s significa que o registro virou a lease
#   com outro nome, que é o defeito original.
#
# Uso (da raiz do repo, demo no ar):  bash infra/test/smoke_claim_possession.sh
set -uo pipefail   # sem -e: exit≠0 de um grep/cmp NÃO é vermelho (§ regras operacionais)

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
POOL="posse_test"
USER_A="userPossA"
INST_A="human-${USER_A}"
AR="http://localhost:3300"
SVC="${AGENT_REGISTRY_SERVICE_TOKEN:-changeme_agent_registry_service_token_demo}"
LEASE_TTL_DEFAULT=180

SID="sess_posse_$$"

R()  { $COMPOSE exec -T redis redis-cli "$@" < /dev/null; }
RQ() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null 2>/dev/null; }

# routing-engine não publica porta no host — POST de dentro do container.
routing_post() {  # $1 = caminho  $2 = json
  $COMPOSE exec -T routing-engine python3 -c "
import json,sys,urllib.request,urllib.error
req=urllib.request.Request('http://localhost:3550$1',data=json.dumps($2).encode(),
                           headers={'content-type':'application/json'})
try:
    print(urllib.request.urlopen(req).read().decode())
except urllib.error.HTTPError as e:
    print(json.dumps({'_http_error': e.code, '_body': e.read().decode()}))
" < /dev/null
}

# jget passa o JSON por VARIÁVEL DE AMBIENTE, não por stdin: todo `docker compose
# exec` aqui leva `< /dev/null` (senão consome o stdin do script e os passos
# seguintes rodam vazios SEM ERRO), e as duas coisas se excluem.
jget() {  # $1 = json  $2 = chave  → valor cru, ou a string ABSENT
  _J="$1" _K="$2" $COMPOSE exec -T -e _J -e _K routing-engine python3 -c "
import json, os
try:
    d = json.loads(os.environ['_J'])
except Exception:
    print('ABSENT'); raise SystemExit
v = d.get(os.environ['_K'], '__missing__')
print('ABSENT' if v == '__missing__' else json.dumps(v).strip('\"'))
" < /dev/null
}

PASS=0; FAIL=0; INCONC=0
ok()     { echo "  PASS — $1"; PASS=$((PASS+1)); }
bad()    { echo "  FAIL — $1"; [ -n "${2:-}" ] && echo "         $2"; FAIL=$((FAIL+1)); }
unsure() { echo "  INCONCLUSIVO — $1"; [ -n "${2:-}" ] && echo "         $2"; INCONC=$((INCONC+1)); }

# eq() RAMIFICA sobre o valor medido — nunca costura o medido numa frase escrita
# para outro valor (a lição dos probes de 2026-08-04: `${CLS:-orphaned}` saiu
# plausível e escondeu o estado inesperado). Três ramos: igual, diferente, ausente.
eq() {  # $1 = descrição  $2 = medido  $3 = esperado
  case "$2" in
    ABSENT)  unsure "$1" "campo AUSENTE na resposta (esperado '$3') — o árbitro não respondeu esse fato" ;;
    "$3")    ok "$1" ;;
    *)       bad "$1" "medido '$2', esperado '$3'" ;;
  esac
}

# Existe porque o `eq` trata AUSENTE como inconclusivo — regra certa quando se
# espera um valor, e errada quando a ausência É o resultado. Ramo próprio em vez
# de comparar contra a string "ABSENT", que confundiria "campo não veio" com
# "campo veio com o texto ABSENT".
nothing_there() {  # $1 = descrição  $2 = medido
  case "$2" in
    ABSENT|null|"") ok "$1" ;;
    *)              bad "$1" "esperava campo ausente/vazio, veio '$2'" ;;
  esac
}

cleanup() {
  echo "9) Limpeza ..."
  curl -s -o /dev/null -X PUT "$AR/v1/pools/$POOL" \
    -H "x-tenant-id: $TENANT" -H "x-service-token: $SVC" \
    -H 'content-type: application/json' -d '{"status":"inactive"}' || true
  R DEL "${TENANT}:instance:${INST_A}" "${TENANT}:instance:${INST_A}:sessions" \
        "${TENANT}:queue_contact:${SID}" \
        "${TENANT}:pool:${POOL}:queue" "${TENANT}:pool:${POOL}:instances" \
        "${TENANT}:pool:${POOL}:busy_instances" \
        "${TENANT}:pool:${POOL}:claim:${SID}" \
        "${TENANT}:pool:${POOL}:claim_record:${SID}" \
        "${TENANT}:queue:first_queued:${SID}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ── 0. Pool pull registrado (sem pool_config o DRAIN come o item — ver
#       smoke_directed_pull.sh § ensure_pool, mesmo modo de falha intermitente) ──
echo "0a) Registrando o pool $POOL como dispatch_mode=pull ..."
code=$(curl -s -o /tmp/_posse_pool -w '%{http_code}' -X POST "$AR/v1/pools" \
  -H "x-tenant-id: $TENANT" -H "x-service-token: $SVC" \
  -H 'content-type: application/json' \
  -d "{\"pool_id\":\"$POOL\",\"agent_kind\":\"human\",\"dispatch_mode\":\"pull\",
       \"channel_types\":[\"webchat\"],\"sla_target_ms\":86400000,
       \"description\":\"Pool de teste da posse do item (smoke Fase A)\"}")
if [ "$code" != "201" ] && [ "$code" != "200" ]; then
  code=$(curl -s -o /tmp/_posse_pool -w '%{http_code}' -X PUT "$AR/v1/pools/$POOL" \
    -H "x-tenant-id: $TENANT" -H "x-service-token: $SVC" \
    -H 'content-type: application/json' \
    -d '{"dispatch_mode":"pull","status":"active"}')
fi
echo "   pool $POOL → HTTP $code"
if [ "$code" != "200" ] && [ "$code" != "201" ]; then
  echo "   ❌ não foi possível registrar o pool (veja /tmp/_posse_pool)"; cat /tmp/_posse_pool
  exit 1
fi
sleep 3   # registry.changed → cache do routing

echo "0b) Semeando a instância humana $INST_A ..."
R SET "${TENANT}:instance:${INST_A}" \
  "{\"instance_id\":\"$INST_A\",\"agent_type_id\":\"human\",\"tenant_id\":\"$TENANT\",\"status\":\"ready\",\"max_concurrent\":5,\"current_sessions\":0,\"pools\":[\"$POOL\"],\"source\":\"human_login\"}" >/dev/null
R SADD "${TENANT}:pool:${POOL}:instances" "$INST_A" >/dev/null

NOW_MS=$(date +%s%3N)
DEADLINE=$(date -u -d '+2 hours' +%Y-%m-%dT%H:%M:%S+00:00)
CONF="conf_posse_$$"
echo "0c) Enfileirando $SID com work_item_deadline=$DEADLINE (2 h) ..."
# O pacote leva os QUATRO campos que o re-publish de seis campos apagava (medido em
# 2026-08-04): assigned_to, conference_id, work_item_deadline, auto_attend. Eles
# entram aqui para que a Fase B tenha o que medir no passo 4b.
R SET "${TENANT}:queue_contact:${SID}" \
  "{\"session_id\":\"$SID\",\"tenant_id\":\"$TENANT\",\"pool_id\":\"$POOL\",\"queued_at_ms\":$NOW_MS,\"work_item_deadline\":\"$DEADLINE\",\"assigned_to\":\"$USER_A\",\"conference_id\":\"$CONF\",\"auto_attend\":true}" >/dev/null
R ZADD "${TENANT}:pool:${POOL}:queue" "$NOW_MS" "$SID" >/dev/null
# `first_queued` com valor ANTIGO e conhecido: o enfileiramento aqui é semeado à
# mão, então sem isto a chave nasceria no release e o teste mediria CRIAÇÃO se
# passando por preservação. O valor tem de sobreviver inalterado (NX).
FQ_SEED=$((NOW_MS - 600000))   # 10 min antes
R SET "${TENANT}:queue:first_queued:${SID}" "$FQ_SEED" EX 604800 >/dev/null

# ── 1. Sem claim: ninguém detém, e o item está NA FILA ───────────────────────
echo
echo "1) Antes do claim — o holder deve dizer 'ninguém detém, está na fila' ..."
H=$(routing_post "/v1/work_queue/holder" "{'tenant_id':'$TENANT','pool_id':'$POOL','session_id':'$SID'}")
eq "found=false (ninguém reivindicou)" "$(jget "$H" found)"    "false"
eq "in_queue=true (membro do ZSET)"    "$(jget "$H" in_queue)" "true"

# ── 2. Claim → registro durável com TTL do PRAZO DO ITEM ─────────────────────
echo
echo "2) Claim por $INST_A ..."
C=$(routing_post "/v1/work_queue/claim" "{'tenant_id':'$TENANT','pool_id':'$POOL','session_id':'$SID','instance_id':'$INST_A'}")
eq "claim aceito" "$(jget "$C" claimed)" "true"

TTL_REC=$(RQ TTL "${TENANT}:pool:${POOL}:claim_record:${SID}" | tr -d '\r')
TTL_LEA=$(RQ TTL "${TENANT}:pool:${POOL}:claim:${SID}"        | tr -d '\r')
echo "   TTL registro=${TTL_REC}s  ·  TTL lease=${TTL_LEA}s"
case "$TTL_REC" in
  ''|*[!0-9-]*) unsure "TTL do registro legível" "valor não-numérico: '$TTL_REC'" ;;
  -2) bad "registro durável existe" "chave AUSENTE (TTL -2) — o claim não a gravou" ;;
  -1) bad "registro com TTL" "chave SEM expiração (TTL -1) — vazaria para sempre" ;;
  *)
    if [ "$TTL_REC" -gt "$LEASE_TTL_DEFAULT" ]; then
      ok "TTL do registro (${TTL_REC}s) sobrevive à lease (${LEASE_TTL_DEFAULT}s)"
    else
      bad "TTL do registro sobrevive à lease" \
          "${TTL_REC}s <= ${LEASE_TTL_DEFAULT}s — é a lease com outro nome; o submit volta a falhar aberto"
    fi
    # Previsão: 2 h de prazo ⇒ ~7200 s. Faixa larga para tolerar o tempo do smoke.
    if [ "$TTL_REC" -gt 6900 ] && [ "$TTL_REC" -le 7200 ]; then
      ok "TTL seguiu o prazo do item (~7200s previstos, medido ${TTL_REC}s)"
    else
      bad "TTL seguiu o prazo do item" \
          "medido ${TTL_REC}s, previsto ~7200s — o TTL não veio de work_item_deadline"
    fi
    ;;
esac

H=$(routing_post "/v1/work_queue/holder" "{'tenant_id':'$TENANT','pool_id':'$POOL','session_id':'$SID'}")
eq "holder=$INST_A"                    "$(jget "$H" instance_id)" "$INST_A"
eq "via=lease (a mais barata responde)" "$(jget "$H" via)"        "lease"
eq "in_queue=false (saiu do ZSET)"      "$(jget "$H" in_queue)"   "false"
eq "claimant_user_id derivado"          "$(jget "$H" claimant_user_id)" "$USER_A"

# ── 3. Lease apagada = vencida → o registro é quem responde ──────────────────
# Apagar a chave em vez de esperar 180 s: o que importa é a AUSÊNCIA da lease, não
# como ela sumiu. Esperar a expiração seria a versão temporal de "esperar volume".
echo
echo "3) Apagando a lease (simula os 180 s vencidos) — o registro deve responder ..."
R DEL "${TENANT}:pool:${POOL}:claim:${SID}" >/dev/null
H=$(routing_post "/v1/work_queue/holder" "{'tenant_id':'$TENANT','pool_id':'$POOL','session_id':'$SID'}")
eq "posse SOBREVIVE à lease (found=true)" "$(jget "$H" found)"       "true"
eq "via=record"                            "$(jget "$H" via)"         "record"
eq "holder continua $INST_A"               "$(jget "$H" instance_id)" "$INST_A"
# O claimant tem de ser o MESMO nos dois ramos. Conferi-lo só no ramo da lease foi
# o que deixou o defeito passar por 12 pytests verdes na primeira rodada.
eq "claimant_user_id igual no ramo do registro" "$(jget "$H" claimant_user_id)" "$USER_A"

# ── 4. Release → ninguém detém E está na fila (o veredicto que recusa) ───────
echo
echo "4) Release — a posse acaba e o item volta à fila ..."
routing_post "/v1/work_queue/release" \
  "{'tenant_id':'$TENANT','pool_id':'$POOL','session_id':'$SID','instance_id':'$INST_A'}" >/dev/null
REC_AFTER=$(RQ EXISTS "${TENANT}:pool:${POOL}:claim_record:${SID}" | tr -d '\r')
eq "registro apagado no release" "$REC_AFTER" "0"
H=$(routing_post "/v1/work_queue/holder" "{'tenant_id':'$TENANT','pool_id':'$POOL','session_id':'$SID'}")
eq "found=false após release"  "$(jget "$H" found)"    "false"
eq "in_queue=true após release" "$(jget "$H" in_queue)" "true"

# ── 4b. Fase B (D1) — o release PRESERVA o pacote ───────────────────────────
# É a diferença mensurável entre os dois caminhos de devolução. O
# `work_task_release` re-enfileira o pacote armazenado verbatim; o re-publish de
# seis campos em `conversations.inbound` reconstruía tudo pelos defaults do
# Pydantic, e os quatro campos abaixo voltavam vazios — com as CHAVES presentes,
# que é por que o JSON passava por íntegro numa inspeção.
echo
echo "4b) O pacote sobreviveu à devolução? (os 4 campos que o re-publish apagava) ..."
QC=$(RQ GET "${TENANT}:queue_contact:${SID}")
eq "assigned_to preservado"        "$(jget "$QC" assigned_to)"        "$USER_A"
eq "conference_id preservado"      "$(jget "$QC" conference_id)"      "$CONF"
eq "work_item_deadline preservado" "$(jget "$QC" work_item_deadline)" "$DEADLINE"
eq "auto_attend preservado"        "$(jget "$QC" auto_attend)"        "true"
# `first_queued` é chave própria com NX — sobrevive por construção, não por
# reescrita. Fecha a dúvida que o ADR levantou ("não é escrito no re-enqueue"):
# se o VALOR mudar, a idade do item na inbox reinicia a cada devolução, e a espera
# real do trabalho some da tela. EXISTS não serviria: a chave nasceria no próprio
# release e o teste mediria criação se passando por preservação.
FQ_NOW=$(RQ GET "${TENANT}:queue:first_queued:${SID}" | tr -d '\r')
eq "first_queued INALTERADO pelo release" "${FQ_NOW:-ABSENT}" "$FQ_SEED"

# ── 5. Expire recupera o dono pelo registro (lease ausente) ─────────────────
echo
echo "5) Re-claim, lease apagada, expire — o dono deve sair do REGISTRO ..."
routing_post "/v1/work_queue/claim" \
  "{'tenant_id':'$TENANT','pool_id':'$POOL','session_id':'$SID','instance_id':'$INST_A'}" >/dev/null
R DEL "${TENANT}:pool:${POOL}:claim:${SID}" >/dev/null
E=$(routing_post "/v1/work_queue/expire" \
  "{'tenant_id':'$TENANT','pool_id':'$POOL','session_id':'$SID','reason':'smoke'}")
eq "expire achou o dono pelo registro" "$(jget "$E" claimed_via)" "record"
eq "instância recuperada"              "$(jget "$E" instance_id)" "$INST_A"
REC_AFTER=$(RQ EXISTS "${TENANT}:pool:${POOL}:claim_record:${SID}" | tr -d '\r')
eq "registro apagado no expire" "$REC_AFTER" "0"

# ── 6. Fase C (D3) — reserva por QUEDA × desistência deliberada ─────────────
# Sessão NOVA, sem `assigned_to`: o item do passo 1 nasceu com vínculo autoral, e
# sob a regra 1 da Fase C a reserva de queda não o toca — mediria o ramo errado.
echo
echo "6) Fase C — o release de QUEDA reserva ao dono; o deliberado não ..."
SID_C="sess_posse_c_$$"
NOW_C=$(date +%s%3N)
DL_C=$(date -u -d '+2 hours' +%Y-%m-%dT%H:%M:%S+00:00)
R SET "${TENANT}:queue_contact:${SID_C}" \
  "{\"session_id\":\"$SID_C\",\"tenant_id\":\"$TENANT\",\"pool_id\":\"$POOL\",\"queued_at_ms\":$NOW_C,\"work_item_deadline\":\"$DL_C\"}" >/dev/null
R ZADD "${TENANT}:pool:${POOL}:queue" "$NOW_C" "$SID_C" >/dev/null

# 6a — desistência deliberada (sem o flag): NÃO reserva.
routing_post "/v1/work_queue/claim" \
  "{'tenant_id':'$TENANT','pool_id':'$POOL','session_id':'$SID_C','instance_id':'$INST_A'}" >/dev/null
REL=$(routing_post "/v1/work_queue/release" \
  "{'tenant_id':'$TENANT','pool_id':'$POOL','session_id':'$SID_C','instance_id':'$INST_A'}")
eq "release deliberado NÃO reserva (reserved_to null)" "$(jget "$REL" reserved_to)" "null"
QC=$(RQ GET "${TENANT}:queue_contact:${SID_C}")
nothing_there "assigned_to segue vazio após o botão" "$(jget "$QC" assigned_to)"

# 6b — queda de transporte (com o flag): reserva ao dono, com a janela do tipo.
# `posse_test` NÃO termina em `-int` → POOLED → janela curta (default 30 s).
#
# O caso `-int` (reserva PERMANENTE, sem transbordo) NÃO é coberto aqui de
# propósito: o registry REJEITA criação manual de pool com sufixo `-int` (só o
# auto-provisionamento cria espelho), então montá-lo neste smoke exigiria burlar
# a mesma garantia por construção de que a regra depende. Vive no pytest
# `test_internal_queue_never_overflows`, cujo fixture escreve direto no Redis.
routing_post "/v1/work_queue/claim" \
  "{'tenant_id':'$TENANT','pool_id':'$POOL','session_id':'$SID_C','instance_id':'$INST_A'}" >/dev/null
REL=$(routing_post "/v1/work_queue/release" \
  "{'tenant_id':'$TENANT','pool_id':'$POOL','session_id':'$SID_C','instance_id':'$INST_A','reserve_to_previous':True}")
eq "release de QUEDA reserva ao dono" "$(jget "$REL" reserved_to)" "$USER_A"
QC=$(RQ GET "${TENANT}:queue_contact:${SID_C}")
eq "assigned_to = dono anterior"          "$(jget "$QC" assigned_to)"              "$USER_A"
eq "janela do tipo NÃO-interno (30 s)"    "$(jget "$QC" fallback_to_pool_after_s)" "30"
FB_ANCHOR=$(jget "$QC" assigned_at_ms)
case "$FB_ANCHOR" in
  ABSENT|null|"") bad "âncora da janela carimbada" "assigned_at_ms=$FB_ANCHOR" ;;
  *)              ok  "âncora da janela carimbada (assigned_at_ms=$FB_ANCHOR)" ;;
esac

R DEL "${TENANT}:queue_contact:${SID_C}" "${TENANT}:queue:first_queued:${SID_C}" \
      "${TENANT}:pool:${POOL}:claim:${SID_C}" \
      "${TENANT}:pool:${POOL}:claim_record:${SID_C}" >/dev/null 2>&1 || true
R ZREM "${TENANT}:pool:${POOL}:queue" "$SID_C" >/dev/null 2>&1 || true

echo
echo "======================================"
echo "  PASS=$PASS  FAIL=$FAIL  INCONCLUSIVO=$INCONC"
if [ "$FAIL" != 0 ]; then
  echo "  ❌ SMOKE FALHOU"; exit 1
elif [ "$INCONC" != 0 ]; then
  # INCONCLUSIVO nunca é verde: campo ausente é falta de resposta, não resposta.
  echo "  ⚠️  SMOKE INCONCLUSIVO — houve fato que o árbitro não respondeu"; exit 2
else
  echo "  ✅ SMOKE OK"
fi
