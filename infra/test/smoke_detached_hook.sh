#!/usr/bin/env bash
# Camada D — hooks de finalização `dispatch: detached`: smoke E2E do mecanismo.
#
# Prova que, com um hook de finalização `detached`, o bridge:
#   (1) dispara o hook como WORKFLOW WEBHOOK fire-and-forget (sessão-filha nova no
#       pool-alvo, herdando o root_session_id da origem → membro da mesma journey);
#   (2) FECHA o contato de origem NA HORA (não fica preso `active` até o force-close
#       de 180s) — é o fecho de G1/AHT.
#
# Estratégia: patcha (em runtime, via PUT /v1/pools) o `on_process_end` de um pool
# webhook que COMPLETA no trigger (outbound_demo) para uma entrada `detached` →
# pool-alvo (portabilidade_processo_ia, que suspende → sessão-filha observável).
# Restaura os hooks originais no fim. O bridge lê a config do pool fresh a cada
# disparo (get_pool_config não é cacheado), então o patch vale sem restart.
#
# NB: é a validação do MECANISMO da Camada D. A fiação real de survey/wrap-up →
# detached (e o E2E completo) é a Camada E/F. Se `outbound_demo` não tiver
# instância viva no seu demo (capacidade apertada), troque PRIMARY por outro pool
# webhook que complete no trigger.
#
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_detached_hook.sh
# ⚠️ UTF-8 explicito na saida do python — e esta linha E o conserto, nao um paliativo.
#
# Nesta bancada o `stdout` do python usa cp1252. Um `print` com acento sai em bytes
# cp1252, e todo consumidor a jusante — `grep` com padrao UTF-8, outro python, o proprio
# shell — deixa de casar sobre um texto que ESTA la. Medido com A/B em 2026-09-02
# (CNS-18): sem a env, `grep -c 'meta NAO escrito'` devolve 0 pelos DOIS caminhos
# (arquivo e variavel); com a env, devolve 1 pelos dois.
#
# ⚠️ O diagnostico levou TRES tentativas e as duas primeiras foram publicadas erradas:
# `sys.stdin` (CNS-12) e a variavel de shell (CNS-17). Nao era o fluxo de ENTRADA nem o
# transporte — era a SAIDA. Variavel e arquivo sao ambos inocentes, e `docker logs`
# tambem: medido, sobrevive intacto pelos dois. Se voce for mexer nisto, o teste que
# separa as hipoteses e o A/B na propria env, com UMA variavel por vez.
export PYTHONIOENCODING=utf-8

set -euo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
CH_DB="plughub_demo"
AR="http://localhost:3300"                 # agent-registry (publicado)
CG="http://localhost:8010"                 # channel-gateway (publicado)
SVC="${AGENT_REGISTRY_SERVICE_TOKEN:-changeme_agent_registry_service_token_demo}"
CH="$COMPOSE exec -T clickhouse clickhouse-client"

PRIMARY="outbound_demo"                     # pool webhook que COMPLETA no trigger
TARGET="portabilidade_processo_ia"          # pool webhook alvo do hook detached (suspende)

thw=(-H "x-tenant-id: $TENANT" -H "x-service-token: $SVC")

cleanup() {
  echo "restaurando hooks originais de $PRIMARY ..."
  # PUT rejeita hooks:null (Prisma exige DbNull p/ campo JSON). Se o original era
  # null/ausente, restaura com {} (= sem hooks: PoolHooks default = listas vazias);
  # senão devolve o objeto original.
  local _restore="{}"
  case "${ORIG_HOOKS:-null}" in
    null|"") _restore="{}" ;;
    *)       _restore="$ORIG_HOOKS" ;;
  esac
  curl -s -X PUT "$AR/v1/pools/$PRIMARY" "${thw[@]}" -H 'content-type: application/json' \
    -d "{\"hooks\": ${_restore}}" -o /dev/null -w '   HTTP %{http_code}\n' || true
}
trap cleanup EXIT

echo "1) Lê os hooks atuais de $PRIMARY (para restaurar depois) ..."
ORIG_HOOKS=$(curl -s "$AR/v1/pools/$PRIMARY" -H "x-tenant-id: $TENANT" \
  | python3 -c "import sys,json; p=json.load(sys.stdin); print(json.dumps(p.get('hooks')))")
echo "   hooks originais: $ORIG_HOOKS"

echo "2) Patcha $PRIMARY.hooks.on_process_end = [{pool: $TARGET, dispatch: detached}] ..."
PATCH=$(curl -s -o /tmp/_dh_body -w '%{http_code}' -X PUT "$AR/v1/pools/$PRIMARY" "${thw[@]}" \
  -H 'content-type: application/json' \
  -d "{\"hooks\": {\"on_process_end\": [{\"pool\": \"$TARGET\", \"dispatch\": \"detached\"}]}}")
echo "   HTTP $PATCH"; [ "$PATCH" = "200" ] || { echo "FALHA: PUT hooks (HTTP $PATCH)"; cat /tmp/_dh_body; exit 1; }
sleep 2

echo "3) Dispara o primário $PRIMARY (deve completar → on_process_end detached) ..."
O=$(curl -s -X POST "$CG/v1/channels/webhook/pool/$PRIMARY" \
  -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"context\":{\"campaign_id\":\"smoke_detached_nope\"}}" \
  | sed -n 's/.*"session_id":"\([^"]*\)".*/\1/p')
[ -n "$O" ] || { echo "FALHA: trigger de $PRIMARY sem session_id"; exit 1; }
echo "   origin session O=$O"

echo "4) Aguardando o processo completar + o hook detached criar a sessão-filha + o analytics gravar ..."
sleep 12

echo "5) Sessões com root_session_id == $O (esperado: O fechado + 1 filha no $TARGET):"
$CH -q "SELECT session_id, origin_session_id, root_session_id, pool_id, \
        if(closed_at IS NULL OR toUnixTimestamp(closed_at)=0,'ACTIVE','closed') AS st \
        FROM ${CH_DB}.sessions FINAL \
        WHERE tenant_id='$TENANT' AND root_session_id='$O' \
        ORDER BY opened_at FORMAT PrettyCompact"

echo
echo "6) Checks:"
PASS=0; FAIL=0
_chk() { if [ "$2" = 1 ]; then echo "  PASS — $1"; PASS=$((PASS+1)); else echo "  FAIL — $1"; FAIL=$((FAIL+1)); fi; }

# (a) origem fechada (não presa em ACTIVE)
O_CLOSED=$($CH -q "SELECT count() FROM ${CH_DB}.sessions FINAL \
  WHERE tenant_id='$TENANT' AND session_id='$O' AND closed_at IS NOT NULL AND toUnixTimestamp(closed_at)>0")
_chk "contato de origem FECHOU (G1: sem prender até 180s)" "$([ "${O_CLOSED:-0}" -ge 1 ] && echo 1 || echo 0)"

# (b) sessão-filha no pool-alvo, herdando a raiz
CHILD=$($CH -q "SELECT count() FROM ${CH_DB}.sessions FINAL \
  WHERE tenant_id='$TENANT' AND root_session_id='$O' AND session_id!='$O' AND pool_id='$TARGET'")
_chk "sessão-filha criada no pool-alvo $TARGET herdando root=$O" "$([ "${CHILD:-0}" -ge 1 ] && echo 1 || echo 0)"

echo
echo "======================================"
echo "  PASS=$PASS  FAIL=$FAIL"
if [ "$FAIL" = 0 ]; then
  echo "  ✅ SMOKE OK"
else
  echo "  ⚠️  Se FAIL: confira se $PRIMARY tem instância viva (capacidade) e completa no trigger;"
  echo "      veja os logs do bridge (grep 'Detached hook fired'). O E2E de survey/wrap-up é a Camada E."
  exit 1
fi
