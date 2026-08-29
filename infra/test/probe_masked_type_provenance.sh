#!/usr/bin/env bash
# probe_masked_type_provenance.sh — GATE da fase T3 (ADR adr-masked-typed-declaration)
#
# O que a T3 entrega: o TIPO de cada campo mascarado por DECLARACAO viaja do engine
# ao bridge e e REGISTRADO como dado na transcricao duravel.
#
# Por que como DADO e nao no texto: o placeholder de campo mascarado e produzido em
# TRES casas independentes (orchestrator-bridge, channel-gateway/webchat,
# platform-ui/AgentAssistPage). Embutir o tipo no texto numa delas faria os caminhos
# divergirem. O ramo D conta as tres e reprova se aparecer uma QUARTA.
#
# Por que importa: sem a coluna, "mascaramos e sabiamos o que" e "ninguem olhou" sao
# leituras IDENTICAS na transcricao — a assinatura de valor plausivel.
#
# CINCO ramos, e nenhum julga sozinho:
#   P0. preflight de CONTEUDO — a imagem tem o simbolo da T3? (imagem x fonte)
#   A.  as CINCO camadas declaram o campo (schema, produtor, parser, ESCRITOR, DDL)
#   B.  a coluna EXISTE na tabela viva
#   C.  ORACULO — a ausencia e distinguivel do vazio? (map vazio != campo ausente)
#   D.  censo das casas de placeholder — reprova a quarta
#
# Tres estados: OK / FALHA / INCONCLUSIVO (nunca OK com ramo inconclusivo).
set -u

cd "$(dirname "$0")/../.." || exit 2
DC="${DC:-docker compose -f docker-compose.demo.yml}"
CH="${CH:-plughub-demo-clickhouse-1}"
CHDB="${CHDB:-plughub_demo}"

fail=0
inconclusive=0
say()  { echo "  $*"; }
bad()  { echo "  x $*"; fail=$((fail+1)); }
ok()   { echo "  v $*"; }
huh()  { echo "  ? $*"; inconclusive=$((inconclusive+1)); }

echo "=== probe_masked_type_provenance — T3 do \`masked\` tipado ==="

# -- P0. preflight de CONTEUDO ------------------------------------------------
# O simbolo tem de estar na IMAGEM, nao so no fonte. Sem este ramo, um build que
# reprova e um `up -d` que sobe a imagem anterior fazem os ramos abaixo julgarem
# codigo velho — foi exatamente o que aconteceu na T1.
echo
echo "-- P0. preflight de conteudo (imagem x fonte) ----------------"
SRC_HAS="$(grep -c 'masked_types' packages/schemas/src/platform-events.ts 2>/dev/null || echo 0)"
IMG_HAS="$($DC exec -T mcp-server-plughub sh -c "cd /app/packages/mcp-server-plughub && node -e \"const s=require('@plughub/schemas'); const r=s.ConversationMessageSentSchema.safeParse({event_type:'message_sent',session_id:'s',tenant_id:'t',masked_types:{a:'cpf'}}); console.log(r.success && r.data.masked_types ? 'SIM' : 'NAO')\"" 2>&1 | tr -d '\r')"
if [ "${SRC_HAS:-0}" = "0" ]; then
  huh "o fonte nao declara masked_types — nada a medir"
  echo; echo "VEREDICTO: INCONCLUSIVO — nada medido"; exit 2
elif [ "$IMG_HAS" = "SIM" ]; then
  ok "schema da imagem aceita e PRESERVA masked_types"
else
  huh "IMAGEM DESATUALIZADA (obtido: '${IMG_HAS}') — os ramos abaixo julgariam codigo velho"
  say "   => \$DC build mcp-server-plughub && \$DC up -d mcp-server-plughub"
  echo; echo "VEREDICTO: INCONCLUSIVO — nada medido"; exit 2
fi

# -- A. as QUATRO camadas -----------------------------------------------------
# Uma camada faltando basta para o campo se perder em silencio.
#
# Sao CINCO, e a quinta foi descoberta na propria T3, depurando: com schema,
# produtor, parser e DDL corretos, a coluna gravava `{}` em TODA linha. O escritor
# (`_MESSAGE_COLS` + `_message_row`) monta o INSERT a partir de uma lista FIXA de
# colunas — chave extra no dict do parser e ignorada, sem erro, sem log. E a camada
# que descarta calada, e por isso ela tem ramo proprio aqui.
echo
echo "-- A. as cinco camadas declaram o campo ----------------------"
declare_check() {
  local nome="$1" arq="$2" pat="$3"
  if [ ! -f "$arq" ]; then huh "arquivo ausente: $arq"; return; fi
  if grep -q "$pat" "$arq"; then ok "$nome"; else bad "$nome — '$pat' ausente de $arq"; fi
}
declare_check "schema canonico" "packages/schemas/src/platform-events.ts"                       "masked_types: z.record"
declare_check "produtor (bridge)" "packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py" '_analytics_event\["masked_types"\]'
declare_check "parser (analytics)" "packages/analytics-api/src/plughub_analytics_api/models.py"  '"masked_types"'
declare_check "DDL + migration"    "packages/analytics-api/src/plughub_analytics_api/clickhouse.py" "masked_types Map(String, String)"
declare_check "escritor: coluna no INSERT" "packages/analytics-api/src/plughub_analytics_api/clickhouse.py" '"masked_types",'
declare_check "escritor: valor na linha"   "packages/analytics-api/src/plughub_analytics_api/clickhouse.py" 'd.get("masked_types")'

# As duas metades do escritor tem de andar JUNTAS: coluna sem valor desalinha o
# INSERT inteiro (as colunas passam a receber o valor da vizinha); valor sem coluna
# levanta erro de aridade. Nenhuma das duas falha do jeito que se espera.
N_COLS_DECL="$(grep -c '"masked_types",' packages/analytics-api/src/plughub_analytics_api/clickhouse.py 2>/dev/null || echo 0)"
N_ROW_DECL="$(grep -c 'd.get("masked_types")' packages/analytics-api/src/plughub_analytics_api/clickhouse.py 2>/dev/null || echo 0)"
if [ "$N_COLS_DECL" = "$N_ROW_DECL" ]; then
  ok "escritor pareado (coluna=${N_COLS_DECL}, valor=${N_ROW_DECL})"
else
  bad "escritor DESPAREADO (coluna=${N_COLS_DECL}, valor=${N_ROW_DECL}) — INSERT desalinha"
fi

# O ALTER e separado do CREATE de proposito: instalacao limpa pega pelo CREATE,
# base existente so pega pelo ALTER. Ter um sem o outro e o defeito assimetrico.
if grep -q "ADD COLUMN IF NOT EXISTS masked_types" packages/analytics-api/src/plughub_analytics_api/clickhouse.py; then
  ok "migration idempotente para base EXISTENTE (o CREATE so cobre instalacao limpa)"
else
  bad "sem ALTER: instalacao limpa teria a coluna e esta base NAO"
fi

# -- B. a coluna existe na tabela VIVA ----------------------------------------
echo
echo "-- B. coluna na tabela viva ----------------------------------"
COLS="$(docker exec "$CH" clickhouse-client -q "SELECT count() FROM system.columns WHERE database='${CHDB}' AND table='messages'" 2>/dev/null | tr -d '\r')"
HASCOL="$(docker exec "$CH" clickhouse-client -q "SELECT count() FROM system.columns WHERE database='${CHDB}' AND table='messages' AND name='masked_types'" 2>/dev/null | tr -d '\r')"
case "$COLS" in
  ''|*[!0-9]*) huh "ClickHouse nao respondeu — coluna nao medida" ;;
  0)           huh "tabela messages sem colunas?! nada a julgar" ;;
  *)
    say "colunas em messages = ${COLS}   (testemunha de presenca)"
    if [ "${HASCOL:-0}" = "1" ]; then ok "masked_types presente"
    else bad "masked_types AUSENTE na tabela viva — o ALTER nao rodou (\$DC restart analytics-api)"; fi
    ;;
esac

# -- C. ORACULO: ausencia e distinguivel de vazio? ----------------------------
# O ponto INTEIRO do campo. Se `{}` fosse gravado em toda mensagem, "nao havia
# campo mascarado" e "ninguem olhou" voltariam a ser identicos — a patologia que
# a coluna existe para remover. O produtor OMITE quando vazio; aqui se confere que
# o codigo diz isso, e que o Map vazio nao e o unico estado possivel.
echo
echo "-- C. oraculo — ausencia != vazio ----------------------------"
if grep -q "if all_masked_types:" packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py; then
  ok "produtor OMITE o campo quando vazio (nao grava {} em toda mensagem)"
else
  bad "produtor grava o campo incondicionalmente — ausencia volta a ser indistinguivel de vazio"
fi
if [ "${HASCOL:-0}" = "1" ]; then
  NEMPTY="$(docker exec "$CH" clickhouse-client -q "SELECT count() FROM ${CHDB}.messages WHERE length(masked_types) > 0" 2>/dev/null | tr -d '\r')"
  NTOT="$(docker exec "$CH" clickhouse-client -q "SELECT count() FROM ${CHDB}.messages" 2>/dev/null | tr -d '\r')"
  say "linhas com masked_types nao-vazio = ${NEMPTY:-?} de ${NTOT:-?}"
  say "   (ZERO e o esperado ate um contato NOVO com form mascarado — a coluna nasce"
  say "    vazia para a historia anterior, e isso e honesto: nao havia o dado)"
fi

# -- D. censo das casas de placeholder ----------------------------------------
# A T3 nao consolidou as tres; ela decidiu NAO tocar no texto justamente por elas
# existirem. Este ramo congela o numero: uma QUARTA casa reprova.
echo
echo "-- D. casas que produzem o placeholder de campo mascarado ----"
# --include restringe a FONTE: sem isso o `grep -rl` conta os `.pyc` do __pycache__,
# que sao a MESMA casa compilada N vezes (medido: 3 copias de webchat.py e 1 de
# main.py inflavam 3 para 7). Contador de casas tem de contar CASAS.
CASAS="$(grep -rlE '"•{6}"|•{6}' \
          --include=*.py --include=*.ts --include=*.tsx \
          packages/orchestrator-bridge/src packages/channel-gateway/src \
          packages/platform-ui/src/modules/agent-assist 2>/dev/null \
        | grep -vE '/tests?/|\.test\.|__tests__|__pycache__' | sort -u)"
N_CASAS="$([ -z "$CASAS" ] && echo 0 || echo "$CASAS" | wc -l | tr -d ' ')"
say "encontradas: ${N_CASAS}"
[ -n "$CASAS" ] && echo "$CASAS" | sed 's/^/      . /'
if [ "$N_CASAS" = "3" ]; then
  ok "as 3 conhecidas — bridge, webchat adapter, echo do Console"
elif [ "$N_CASAS" -lt 3 ] 2>/dev/null; then
  huh "MENOS de 3: alguma casa sumiu ou o padrao de busca nao alcanca mais — recontar"
else
  bad "${N_CASAS} casas: apareceu uma NOVA. Consolidar antes de crescer o numero"
fi

# -- veredicto ----------------------------------------------------------------
echo
echo "==============================================================="
if [ "$fail" -gt 0 ]; then
  echo "VEREDICTO: FALHA — ${fail} verificacao(oes) vermelha(s)"; exit 1
fi
if [ "$inconclusive" -gt 0 ]; then
  echo "VEREDICTO: INCONCLUSIVO — ${inconclusive} ramo(s) nao julgado(s). NAO e OK."; exit 2
fi
echo "VEREDICTO: OK"
exit 0
