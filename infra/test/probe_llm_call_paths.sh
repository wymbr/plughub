#!/usr/bin/env bash
# probe_llm_call_paths.sh — T0 do ADR `adr-relatorios-duas-superficies-e-lentes.md`.
#
# A T0 é MEDIR antes de construir. A pergunta: por quais caminhos o ai-gateway
# consome LLM, e quantos deles publicam o consumo em `usage.events`?
#
# Ela existe porque a resposta era contraintuitiva. O único emissor
# (`emit_llm_tokens`, chamado de `inference.py`) está atrás de `POST /inference`,
# **que não tem chamador algum** — e o caminho que roda de verdade (`/v1/reason`)
# tem os números na mão e não os publica. Construir a lente de token antes disso
# daria uma tela de zeros, ou pior: um número plausível vindo de caminho marginal.
#
# DUAS METADES, e a estática vale sozinha:
#   A) INVENTÁRIO estático — todo site que chama um provider está DECLARADO, e
#      quantos deles emitem. Roda sem serviço de pé.
#   B) VOLUME runtime — quantas chamadas cada caminho serviu. Precisa da stack.
#
# Uso:  bash infra/test/probe_llm_call_paths.sh
# Env:  AI_GW_CONTAINER=plughub-demo-ai-gateway-1
#
# Veredicto de TRÊS estados: 0 = OK · 1 = REPROVOU · 3 = INCONCLUSIVO
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$ROOT/packages/ai-gateway/src/plughub_ai_gateway"
CONTAINER="${AI_GW_CONTAINER:-plughub-demo-ai-gateway-1}"

FAIL=0
echo "══ caminhos de consumo de LLM — ai-gateway ══"

[ -d "$SRC" ] || { echo "   ⛔ INCONCLUSIVO — $SRC não encontrado."; exit 3; }

# ── A) INVENTÁRIO DECLARADO ───────────────────────────────────────────────────
# arquivo:linha → caminho|emite. Site novo sem linha aqui reprova: um caminho de
# consumo entrando calado é exatamente como o `/v1/reason` passou meses sem medir.
#
# `emite` = há uma chamada a `emit_llm_tokens` no fluxo deste site.
# O campo `chamador` é o que separa ROTA MORTA de AMBIENTE OCIOSO — distinção que
# o volume da seção B NÃO consegue fazer sozinho, e que decide o escopo da T1.
declare -A SITES=(
  ["reason.py"]="POST /v1/reason|SIM|VIVO — skill-flow-service + engine-runner"
  ["sentiment_analyzer.py"]="interno, dentro do /v1/reason|SIM|VIVO — sem rota própria; era 42% das chamadas e não publicava"
  ["copilot_emitter.py"]="POST /v1/copilot/analyze|SIM|VIVO — chamador em mcp-server-plughub/src/server.ts:3394"
  ["main.py"]="sonda de credencial no boot|EXCLUIDO|VIVO — consumo de PROCESSO: usage_events exige tenant_id NOT NULL e não há tenant a quem atribuir. Exclusão CONTADA no log do boot, nunca silenciosa"
  ["inference.py"]="POST /inference|SIM|MORTA — zero chamadores em packages/"
  ["gateway.py"]="POST /v1/turn|nao|MORTA — zero chamadores; não emite por não ter chamador, não por esquecimento"
)
# Depois da T1, contar não basta: cada caminho marcado SIM tem de conter, ELE MESMO,
# uma chamada ao emissor. Um contador global passaria com 5 emissores se um caminho
# vivo perdesse o seu — e "5 de 6" pareceria saúde.
BASELINE_EMITTERS=4

echo
echo "── A) sites que chamam um provider (estático) ──"
mapfile -t FOUND < <(
  grep -rlE '(provider|self\._provider)\.call\(|_call_with_fallback\(' \
    --include=*.py "$SRC" 2>/dev/null \
  | grep -v '/tests/' | grep -v '/providers/' | grep -v 'account_selector.py' \
  | xargs -r -n1 basename | sort -u
)
if [ "${#FOUND[@]}" -lt 3 ]; then
  echo "   ⛔ INCONCLUSIVO — achei ${#FOUND[@]} arquivos com site de provider (esperado ≥ 3)."
  echo "      O padrão deste probe deixou de casar com o código; verde aqui não mediria nada."
  exit 3
fi

UNDECLARED=()
for f in "${FOUND[@]}"; do
  if [ -n "${SITES[$f]+x}" ]; then
    IFS='|' read -r path emits note <<< "${SITES[$f]}"
    printf '   %-24s %-42s emite=%s\n' "$f" "$path" "$emits"
    [ -n "$note" ] && printf '   %-24s   └─ %s\n' "" "$note"
  else
    UNDECLARED+=("$f")
  fi
done
if [ ${#UNDECLARED[@]} -gt 0 ]; then
  FAIL=1
  echo "   ❌ ${#UNDECLARED[@]} site(s) de consumo NÃO declarado(s):"
  for f in "${UNDECLARED[@]}"; do echo "      · $f"; done
  echo "      Todo caminho que gasta token tem de estar nesta tabela — com 'emite' respondido."
fi

# Quantos arquivos realmente chamam o emissor.
mapfile -t EMITTERS < <(
  grep -rlE '\bemit_llm_tokens\(' --include=*.py "$SRC" 2>/dev/null \
  | grep -v '/tests/' | grep -v 'usage_emitter.py' \
  | xargs -r -n1 basename | sort -u
)
N_EMIT="${#EMITTERS[@]}"
echo
echo "   emissores de usage.events: $N_EMIT de ${#FOUND[@]} caminhos  (linha de base: $BASELINE_EMITTERS)"
for f in "${EMITTERS[@]}"; do echo "      · $f"; done
if [ "$N_EMIT" -lt "$BASELINE_EMITTERS" ]; then
  FAIL=1
  echo "   ❌ a cobertura de emissão DIMINUIU — era $BASELINE_EMITTERS, virou $N_EMIT."
fi

# Checagem POR CAMINHO — o que a contagem global não pega.
echo
echo "   cada caminho declarado 'SIM' emite?"
for f in "${!SITES[@]}"; do
  IFS='|' read -r path emits note <<< "${SITES[$f]}"
  [ "$emits" = "SIM" ] || continue
  if grep -qE '\bemit_llm_tokens\(' "$SRC/$f" 2>/dev/null; then
    printf '      ✅ %s\n' "$f"
  else
    FAIL=1
    printf '      ❌ %s — declarado SIM e SEM chamada a emit_llm_tokens\n' "$f"
    echo   "         Um caminho que gasta e não publica subconta o custo em silêncio."
  fi
done
# E o contrário: quem emite sem estar declarado como emissor.
for f in "${EMITTERS[@]}"; do
  IFS='|' read -r _p e _n <<< "${SITES[$f]:-|nao|}"
  [ "$e" = "SIM" ] && continue
  FAIL=1
  printf '      ❌ %s — EMITE mas está declarado como "%s" na tabela\n' "$f" "$e"
done

# ── B) VOLUME RUNTIME ─────────────────────────────────────────────────────────
echo
echo "── B) volume por caminho (runtime) ──"
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
  echo "   ⛔ metade RUNTIME inconclusiva — container '$CONTAINER' não está de pé."
  echo "      A metade A acima vale sozinha; o veredicto abaixo cobre só ela."
  [ "$FAIL" -eq 0 ] && { echo; echo "✅ APROVADO (só estático)"; exit 0; }
  echo; echo "❌ REPROVOU — ver seção A"; exit 1
fi

LOGS="$(docker logs "$CONTAINER" 2>&1)"
WINDOW_FROM="$(printf '%s' "$LOGS" | grep -oE '^20[0-9]{2}-[0-9]{2}-[0-9]{2}' | head -1)"
WINDOW_TO="$(printf '%s' "$LOGS"   | grep -oE '^20[0-9]{2}-[0-9]{2}-[0-9]{2}' | tail -1)"
echo "   janela do log: ${WINDOW_FROM:-?} → ${WINDOW_TO:-?}"
echo '   ⚠️ é a janela do LOG do container, não do sistema: um "up -d" a zera.'
echo
printf '   %-34s %s\n' "caminho" "chamadas"
for r in /inference /v1/turn /v1/reason /v1/copilot/analyze; do
  n="$(printf '%s' "$LOGS" | grep -cE "POST $r HTTP")"
  printf '   %-34s %s\n' "POST $r" "$n"
done
N_SENT="$(printf '%s' "$LOGS" | grep -c 'sentiment: medido')"
printf '   %-34s %s\n' "sentiment (dentro do /v1/reason)" "$N_SENT"
N_REASON="$(printf '%s' "$LOGS" | grep -cE 'POST /v1/reason HTTP')"

TOTAL=$(( N_REASON + N_SENT ))
if [ "$TOTAL" -gt 0 ]; then
  PCT=$(( N_SENT * 100 / TOTAL ))
  echo
  echo "   ACHADO: $N_SENT de $TOTAL chamadas ($PCT%) vêm de um caminho SEM ROTA PRÓPRIA."
  echo "   Consequência para a T1: um produtor ligado ao HANDLER da rota perderia essa"
  echo "   fatia inteira. O emissor tem de ficar no site que fala com o provider."
else
  echo
  echo "   ⚠️ zero chamadas na janela — o volume não distingue 'caminho morto' de"
  echo "      'ambiente ocioso'. Para a T1, vale o inventário da seção A, não este número."
fi

# ── C) COBERTURA DE ATRIBUIÇÃO (T2) ───────────────────────────────────────────
# A seção A prova que cada caminho EMITE. Não prova que o evento é ATRIBUÍVEL: um
# chamador que pare de propagar o `segment_id` continuaria emitindo, e o custo
# passaria a cair num balde sem dono — sem nada ficar vermelho.
#
# O corte pela época (`analytics-api/usage_attribution.py`) não é detalhe: linha
# anterior à T2 tem os campos vazios por AUSÊNCIA DE MECANISMO, e somá-la com as de
# agora esconderia o defeito dentro da história. Foi assim que o alvo de SLA
# expirado se escondeu até ganhar contador próprio.
CH_CONTAINER="${CH_CONTAINER:-plughub-demo-clickhouse-1}"
CH_DB="${CH_DB:-plughub_demo}"
EPOCH="$(grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' \
  "$ROOT/packages/analytics-api/src/plughub_analytics_api/usage_attribution.py" 2>/dev/null | head -1)"
echo
echo "── C) cobertura de atribuição (pós-época ${EPOCH:-?}) ──"
if [ -z "$EPOCH" ]; then
  echo "   ⛔ INCONCLUSIVO — não achei a época; sem ela, contar linhas vazias"
  echo "      mistura 'não medíamos' com 'não informado', que são coisas diferentes."
elif ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CH_CONTAINER"; then
  echo "   ⛔ metade RUNTIME inconclusiva — '$CH_CONTAINER' fora do ar."
else
  # A época é DATA porque marca uma versão de CÓDIGO, e cada ambiente a implanta
  # numa hora diferente — um timestamp fixo mentiria fora daqui. Isso deixa uma
  # ambiguidade real no DIA da implantação, e ela não se resolve com relógio: as
  # linhas vazias de hoje podem vir de (a) caminho que burla o emissor — defeito
  # em curso — ou (b) ingest anterior às colunas — história de algumas horas.
  #
  # Quem separa os dois é a ORDEM, não a data: se o evento MAIS RECENTE tem
  # `source`, o produtor está são e o que sobrou é retrato do passado. Se o mais
  # recente está vazio, o defeito é AGORA. Comparar os dois máximos responde a
  # pergunta certa sem inventar precisão que a época não tem.
  Q="SELECT count(), countIf(segment_id = ''), countIf(source = ''), toUnixTimestamp(maxIf(timestamp, source != '')), toUnixTimestamp(maxIf(timestamp, source = '')) FROM ${CH_DB}.usage_events WHERE dimension LIKE 'llm_tokens%' AND timestamp >= toDateTime('${EPOCH} 00:00:00')"
  RES="$(docker exec "$CH_CONTAINER" clickhouse-client -q "$Q" 2>/dev/null | tr '\t' ' ')"
  read -r TOT NOSEG NOSRC LAST_OK LAST_BAD <<< "$RES"
  if [ -z "${TOT:-}" ]; then
    echo "   ⛔ INCONCLUSIVO — ClickHouse não respondeu à contagem."
  elif [ "${TOT:-0}" -eq 0 ]; then
    echo "   ⛔ INCONCLUSIVO — 0 eventos pós-época. Sem amostra, um verde aqui não"
    echo "      distinguiria 'atribuição perfeita' de 'produtor parado'."
  else
    echo "   $TOT eventos · sem segment_id: $NOSEG · sem source: $NOSRC"
    if [ "${NOSRC:-0}" -gt 0 ] && [ "${LAST_BAD:-0}" -gt "${LAST_OK:-0}" ]; then
      FAIL=1
      echo "   ❌ o evento MAIS RECENTE não tem source — defeito EM CURSO: há caminho"
      echo "      emitindo sem passar pelo emissor, ou consumidor sem as colunas."
    elif [ "${NOSRC:-0}" -gt 0 ]; then
      echo "   ⚠️  $NOSRC sem source, todos ANTERIORES ao último evento atribuído:"
      echo "      retrato do ingest antigo, não defeito em curso. Não reprova."
    fi
    if [ "${NOSEG:-0}" -gt 0 ]; then
      echo "   ⚠️  $NOSEG sem segment_id: chamador que não propaga a chave. Não reprova"
      echo "      (copiloto e sonda não têm segmento por natureza), mas se crescer é o"
      echo "      reason perdendo a atribuição — e o custo perde o dono."
    fi
  fi
fi

# ── D) TESTEMUNHA NEGATIVA (T3) ───────────────────────────────────────────────
#
# A seção C prova que o que É emitido carrega a chave. Esta prova o outro lado, e é
# o lado que nenhum produtor testa sozinho: **contato que não chamou LLM não pode
# ter linha nenhuma**. Nunca uma linha valendo 0.
#
# É a regra da casa sobre guarda de produtor — *"o teste que importa não é 'registrou
# o fato', é 'NÃO registrou o não-fato'"* — e ela tem endereço aqui porque a T3 passou
# a LER esses eventos: um `quantity: 0` viraria um contato que "usou IA e gastou zero"
# na lente de token, indistinguível de um que usou de verdade e é barato.
#
# Sem esta seção o gate ficaria verde sobre um produtor que emite a cada turno,
# gastando linha e mentindo sobre a população que consumiu IA.
echo
echo "── D) testemunha negativa: o não-fato não vira linha ──"
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CH_CONTAINER"; then
  echo "   ⛔ INCONCLUSIVO — '$CH_CONTAINER' fora do ar; nada a medir."
else
  QZ="SELECT countIf(quantity = 0), count() FROM ${CH_DB}.usage_events \
      WHERE dimension LIKE 'llm_tokens%'"
  RZ="$(docker exec "$CH_CONTAINER" clickhouse-client -q "$QZ" 2>/dev/null | tr '\t' ' ')"
  ZEROS="$(echo "$RZ" | awk '{print $1}')"; TOTZ="$(echo "$RZ" | awk '{print $2}')"
  if [ -z "${TOTZ:-}" ]; then
    echo "   ⛔ INCONCLUSIVO — a consulta não respondeu."
  elif [ "${TOTZ:-0}" -eq 0 ]; then
    echo "   ⛔ INCONCLUSIVO — 0 eventos de token. 'Nenhum zero' entre nenhum evento"
    echo "      não é evidência: é a mesma leitura de um produtor parado."
  elif [ "${ZEROS:-0}" -gt 0 ]; then
    FAIL=1
    echo "   ❌ $ZEROS de $TOTZ eventos com quantity = 0."
    echo "      Um não-fato virou linha: na lente de token isso é um contato que"
    echo "      'usou IA e gastou zero', indistinguível de um que usou e é barato."
  else
    echo "   ✅ $TOTZ eventos, nenhum com quantity = 0"
  fi
fi

echo
if [ "$FAIL" -eq 0 ]; then echo "✅ APROVADO — todo caminho de consumo está declarado"; exit 0; fi
echo "❌ REPROVOU — ver seções acima"; exit 1
