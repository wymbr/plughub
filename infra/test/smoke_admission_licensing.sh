#!/usr/bin/env bash
# smoke_admission_licensing.sh — fatia 3 no SISTEMA VIVO, não na função.
#
# POR QUE ESTE SMOKE EXISTE, tendo `test_admission_licensing.py`. A suíte cobre o
# `AdmissionController` isolado. O que a fatia 3 mudou, porém, atravessa quatro
# processos: routing-engine (produtor) → Kafka → analytics-api (consumer) → ClickHouse,
# mais o agent-registry lendo o Redis para o Monitor. Um `AdmissionController` correto
# com o flusher publicando `__shared__` ainda produziria a tela antiga com o número
# novo — e nada ficaria vermelho.
#
# QUATRO PORTÕES:
#   A. chaves MORTAS não voltam    → `{t}:admission:shared` e `…:reserved:*` ausentes
#   B. chave VIVA renomeada        → `{t}:admission:ai_pools` é a que existe
#   C. série reapontada            → `__admitted_ai__` chegando; `__shared__`/`__reserved__` não
#   D. Monitor coerente            → summary do /v1/operational/pools sem `shared`/`reserved`
#
# O portão A é o que discrimina de verdade. Sem ele, um deploy PARCIAL (routing-engine
# novo, analytics velha, ou o inverso) passaria: as chaves antigas continuariam sendo
# escritas por um processo desatualizado, com valores plausíveis, e o único sintoma
# seria a admissão recusando contato humano de novo — em produção, sem aviso.
#
# ATENÇÃO ao TTL: `{t}:admission:member:{sid}` e os SETs antigos NÃO expiram sozinhos
# (member key tem TTL 7d; os SETs, nenhum). Chave RESIDUAL de antes do deploy é
# esperada e não é regressão — por isso o portão A checa se as chaves estão sendo
# ESCRITAS DE NOVO (compara duas leituras em volta de tráfego), e não se existem.
#
# Uso:  bash infra/test/smoke_admission_licensing.sh
# Pré:  stack demo no ar. Dura: ~90 s.

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${PLUGHUB_TENANT:-tenant_demo}"
CH() { $DC exec -T clickhouse clickhouse-client -u plughub --password plughub \
         -d plughub_demo -q "$1" < /dev/null 2>/dev/null; }
RED() { $DC exec -T redis redis-cli "$@" 2>/dev/null; }

FAIL=0
INCONCLUSIVE=0
note() { echo "   $*"; }
ok()   { echo "   ✅ $*"; }
bad()  { echo "   ❌ $*"; FAIL=1; }
inc()  { echo "   ⚠️  INCONCLUSIVO: $*"; INCONCLUSIVE=1; }

echo "── pré-condições ───────────────────────────────────────────────────────────"
if ! $DC exec -T routing-engine python -c 'import plughub_routing' >/dev/null 2>&1; then
  inc "routing-engine não executa código — nada a medir"; exit 2
fi
# A imagem em execução tem de ser a da fatia 3. Sem esta checagem, TODOS os portões
# abaixo podem passar contra o código antigo num tenant ocioso (sem tráfego, ninguém
# escreve as chaves velhas e a série não recebe linha nova) — verde por silêncio.
# Casa DEFINIÇÃO ou CHAMADA, nunca o nome solto: `_shared_limit` sobrevive de propósito
# no docstring de `admission.py`, que explica por que ele saiu. A primeira versão deste
# portão grepava o nome cru e reprovou contra a imagem CORRETA — o mesmo modo de falha
# que o Portão 3 do smoke da F5 já evitava, repetido aqui por descuido.
if $DC exec -T routing-engine sh -lc \
     "grep -rqE 'def _shared_limit|self\._shared_limit\(' /app --include='*.py'" 2>/dev/null; then
  bad "\`_shared_limit\` ainda existe na imagem do routing-engine — a fatia 3 não subiu"
  note "Corrigir e RE-RODAR: $DC build routing-engine && $DC up -d --force-recreate routing-engine"
  note "Os portões abaixo não seriam conclusivos contra a imagem antiga; abortando."
  exit 1
fi
ok "imagem do routing-engine é a da fatia 3 (\`_shared_limit\` ausente)"
if [ "$(CH 'SELECT 1')" != "1" ]; then
  inc "ClickHouse inacessível"; exit 2
fi

T0="$(CH "SELECT toString(now())")"
note "corte de ingestão (ingested_at >= '$T0'), tenant '$TENANT'"

# ── Portão A · as chaves mortas não estão sendo reescritas ───────────────────
echo
echo "── Portão A · chaves do pote misto ─────────────────────────────────────────"
# Duas leituras em volta de uma janela de tráfego. O que reprova é a chave APARECER
# (ou ganhar membro) na janela — chave residual de antes do deploy é história, não
# regressão, e diferenciar as duas é o ponto.
A_BEFORE="$(RED --scan --pattern "${TENANT}:admission:shared*" | sort | tr '\n' ' ')"
R_BEFORE="$(RED --scan --pattern "${TENANT}:admission:reserved:*" | sort | tr '\n' ' ')"
M_BEFORE="$(RED --scan --pattern "${TENANT}:admission:member:*" | wc -l)"
note "janela de 45 s — gerar contato agora ajuda, mas o tráfego normal serve"
sleep 45
A_AFTER="$(RED --scan --pattern "${TENANT}:admission:shared*" | sort | tr '\n' ' ')"
R_AFTER="$(RED --scan --pattern "${TENANT}:admission:reserved:*" | sort | tr '\n' ' ')"
M_AFTER="$(RED --scan --pattern "${TENANT}:admission:member:*" | wc -l)"

if [ "$A_BEFORE" = "$A_AFTER" ] && [ "$R_BEFORE" = "$R_AFTER" ] \
   && [ "${M_AFTER:-0}" -le "${M_BEFORE:-0}" ]; then
  ok "nenhuma chave nova do pote misto na janela"
  [ -n "${A_AFTER// }" ] && note "resíduo pré-deploy presente (não expira sozinho): $A_AFTER"
  [ -n "${R_AFTER// }" ] && note "resíduo pré-deploy presente: $R_AFTER"
else
  bad "chaves do pote misto sendo ESCRITAS depois do deploy"
  note "antes: shared=[$A_BEFORE] reserved=[$R_BEFORE] member=$M_BEFORE"
  note "depois: shared=[$A_AFTER] reserved=[$R_AFTER] member=$M_AFTER"
  note "algum processo ainda roda o código antigo. Recriar TODOS os que tocam admissão:"
  note "  $DC build routing-engine agent-registry && $DC up -d --force-recreate routing-engine agent-registry"
fi

# ── Portão B · a chave viva é a renomeada ───────────────────────────────────
echo
echo "── Portão B · atribuição por pool (ai_pools) ───────────────────────────────"
AI_HASH="$(RED exists "${TENANT}:admission:ai_pools")"
AI_SET="$(RED scard "${TENANT}:admission:kind:ai")"
if [ "${AI_SET:-0}" -gt 0 ] || [ "${AI_HASH:-0}" = "1" ]; then
  ok "balde de IA vivo (kind:ai=${AI_SET}, ai_pools presente=${AI_HASH})"
  # `Σ fatias == SCARD` é o invariante em REGIME. Fora dele há uma divergência
  # LEGÍTIMA e finita, e vale nomeá-la porque foi a primeira coisa que apareceu na
  # validação de 2026-08-02 (`hlen 0 × scard 2`): sessões admitidas ANTES do deploy
  # entraram no SET `kind:ai` — que não mudou de nome — mas sua atribuição foi escrita
  # no HASH antigo (`shared_pools`). Elas nunca aparecerão em `ai_pools`, e o
  # reconciler NÃO as conserta de propósito: a higiene remove entrada de HASH sem
  # lastro no SET, nunca inventa a de um membro do SET (inventar exigiria adivinhar o
  # pool, e um pool adivinhado é pior que uma fatia faltando).
  #
  # A cura é o fechamento dessas sessões — finita e auto-limitada. O que distingue
  # resíduo de defeito é a DIREÇÃO: `hlen < scard` é herança; `hlen > scard` é
  # atribuição sem lastro, e aí sim o reconciler falhou.
  HLEN="$(RED hlen "${TENANT}:admission:ai_pools")"
  LEGACY="$(RED hlen "${TENANT}:admission:shared_pools")"
  if [ "${HLEN:-0}" = "${AI_SET:-0}" ]; then
    ok "Σ fatias por pool == SCARD(kind:ai) (= ${AI_SET})"
  elif [ "${HLEN:-0}" -lt "${AI_SET:-0}" ]; then
    note "hlen(ai_pools)=${HLEN} < scard(kind:ai)=${AI_SET} — $(( AI_SET - HLEN )) sessão(ões)"
    note "admitida(s) antes do deploy (atribuição ficou no HASH antigo: shared_pools tem"
    note "${LEGACY:-0} entrada(s)). Some conforme elas fecham; não é reprovação."
    note "Confirmar depois de um ciclo de sessões novas — se persistir SEM sessão antiga"
    note "viva, aí é a escrita da atribuição que não está acontecendo."
  else
    bad "hlen(ai_pools)=${HLEN} > scard(kind:ai)=${AI_SET} — atribuição SEM lastro no SET"
    note "esta direção o reconciler deveria curar (higiene do HASH). Ele não está rodando,"
    note "ou está falhando em silêncio: $DC logs --tail=60 routing-engine | grep -i reconcile"
  fi
else
  inc "nenhuma sessão de IA ativa — o balde não tem o que mostrar"
  note "não é reprovação: sem tráfego de IA não há atribuição. Gerar um contato para"
  note "um pool com agent_kind='ai' e repetir."
fi

# ── Portão C · a série foi reapontada ────────────────────────────────────────
echo
echo "── Portão C · linhas agregadas da série ────────────────────────────────────"
N_AI="$(CH "SELECT count() FROM plughub_demo.pool_occupancy_peaks
             WHERE ingested_at >= toDateTime('${T0}') AND tenant_id = '${TENANT}'
               AND pool_id = '__admitted_ai__'")"
N_OLD="$(CH "SELECT count() FROM plughub_demo.pool_occupancy_peaks
              WHERE ingested_at >= toDateTime('${T0}') AND tenant_id = '${TENANT}'
                AND pool_id IN ('__shared__','__reserved__')")"
if [ "${N_AI:-0}" -eq 0 ] && [ "${N_OLD:-0}" -eq 0 ]; then
  inc "nenhuma linha agregada nova — o flusher não publicou no período"
  note "sem isso o portão não julgou. Checar o flusher antes de concluir qualquer coisa:"
  note "  bash infra/test/smoke_occupancy_peak_flusher.sh"
else
  if [ "${N_OLD:-0}" = "0" ]; then
    ok "nenhuma linha \`__shared__\`/\`__reserved__\` nova"
  else
    bad "$N_OLD linha(s) \`__shared__\`/\`__reserved__\` INGERIDA(S) após o corte"
    note "o produtor antigo segue publicando: recriar o routing-engine."
  fi
  if [ "${N_AI:-0}" -gt 0 ]; then
    ok "$N_AI linha(s) \`__admitted_ai__\` na série"
    CH "SELECT minute, peak_concurrency, provisioned_capacity
        FROM plughub_demo.pool_occupancy_peaks FINAL
        WHERE ingested_at >= toDateTime('${T0}') AND tenant_id='${TENANT}'
          AND pool_id='__admitted_ai__' ORDER BY minute DESC LIMIT 3 FORMAT TSV" \
      | sed 's/^/      /'
    note "colunas: minuto · sessões debitando C_ai · C_ai."
  else
    bad "nenhuma linha \`__admitted_ai__\` — a linha nova não tem produtor"
  fi
fi

# ── Portão D · o Monitor não expõe mais o pote ──────────────────────────────
echo
echo "── Portão D · summary do /v1/operational/pools ─────────────────────────────"
# Cadeia de sondas: a imagem do agent-registry não traz `curl` (foi o que fez este
# portão sair INCONCLUSIVO na validação de 2026-08-02). `node` está garantido — é um
# serviço Node —, então ele é o fundo do poço, não uma conveniência.
# ⚠️ AUT-19 (2026-08-31): esta rota passou a exigir credencial. Ate entao a chamada
# saia sem `Authorization` e recebia 200 — a mesma porta que a AUT-23 mediu servindo os
# 36 pools do tenant a quem nao se identificasse. Fechar a borda obriga a MIGRAR os
# chamadores, e este e um deles; sem o token o portao D sairia INCONCLUSIVO por 401 e
# alguem leria como "agent-registry fora do ar".
D_TOK="$(curl -s --max-time 10 -X POST "${AUTH:-http://localhost:3202/auth}/login"   -H 'content-type: application/json'   -d "{\"email\":\"${ADMIN_EMAIL:-admin@plughub.local}\",\"password\":\"${ADMIN_PASS:-changeme_admin}\",\"tenant_id\":\"$TENANT\"}"   | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("access_token",""))
except Exception: print("")' 2>/dev/null)"
if [ -z "$D_TOK" ]; then
  inc "login falhou — o portao D exige credencial desde a AUT-19 e nao ha token"
  note "sem token esta rota devolve 401, que NAO e o mesmo que servico fora do ar"
  SUM=""
else
SUM="$($DC exec -T -e T="$TENANT" -e TOK="$D_TOK" agent-registry sh -lc '
  URL="http://127.0.0.1:3300/v1/operational/pools"
  if command -v curl >/dev/null 2>&1; then
    curl -s -H "x-tenant-id: $T" -H "authorization: Bearer $TOK" "$URL"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- --header="x-tenant-id: $T" --header="authorization: Bearer $TOK" "$URL"
  else
    node -e "require(\"http\").get({host:\"127.0.0.1\",port:3300,path:\"/v1/operational/pools\",headers:{\"x-tenant-id\":process.env.T,\"authorization\":\"Bearer \"+process.env.TOK}},r=>{let d=\"\";r.on(\"data\",c=>d+=c);r.on(\"end\",()=>process.stdout.write(d))}).on(\"error\",e=>{console.error(e.message);process.exit(1)})"
  fi' 2>&1 | head -c 200000)"
fi
if [ -z "$SUM" ] || ! printf '%s' "$SUM" | grep -q '"items"'; then
  inc "agent-registry não respondeu um payload utilizável — portão não julgou"
  note "resposta (200 primeiros bytes): $(printf '%s' "$SUM" | head -c 200)"
  note "checar porta e saúde: $DC ps agent-registry; $DC logs --tail=40 agent-registry"
else
  # `grep` no JSON cru de propósito: o que se quer provar é a AUSÊNCIA de um campo,
  # e um parser tolerante devolveria "não encontrado" tanto para campo removido
  # quanto para JSON que ele não soube ler.
  BAD_FIELDS=""
  printf '%s' "$SUM" | grep -q '"shared"'        && BAD_FIELDS="$BAD_FIELDS shared"
  printf '%s' "$SUM" | grep -q '"reserved"'      && BAD_FIELDS="$BAD_FIELDS reserved"
  printf '%s' "$SUM" | grep -q '"reservation"'   && BAD_FIELDS="$BAD_FIELDS reservation"
  if [ -z "$BAD_FIELDS" ]; then
    ok "summary sem \`shared\`/\`reserved\`/\`reservation\`"
  else
    bad "summary ainda expõe:$BAD_FIELDS — agent-registry não foi recriado"
    note "  $DC build agent-registry && $DC up -d --force-recreate agent-registry"
  fi
  if printf '%s' "$SUM" | grep -q '"by_pool"'; then
    ok "\`ai.by_pool\` presente (atribuição reapontada)"
  else
    bad "\`ai.by_pool\` ausente no summary"
  fi
  if printf '%s' "$SUM" | grep -qE '"admission_scope": *"(licensed|unlicensed)"'; then
    ok "\`admission_scope\` no vocabulário novo (licensed|unlicensed)"
  else
    bad "\`admission_scope\` ainda em reserved|shared"
  fi
fi

echo
if [ "$FAIL" -ne 0 ]; then
  echo "RESULTADO: ❌ a fatia 3 não está inteira no sistema vivo."
  exit 1
elif [ "$INCONCLUSIVE" -ne 0 ]; then
  echo "RESULTADO: ⚠️  INCONCLUSIVO — algum portão não chegou a julgar (ver acima)."
  exit 2
else
  echo "RESULTADO: pote misto sem escritor, balde de IA vivo, série e Monitor reapontados."
fi
