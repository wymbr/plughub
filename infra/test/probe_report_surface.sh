#!/usr/bin/env bash
# probe_report_surface.sh — F0 do ADR `adr-relatorios-duas-superficies-e-lentes.md`.
#
# POR QUE ESTE PROBE EXISTE
# -------------------------
# A patologia que ele guarda já foi diagnosticada NESTE repositório, com o argumento
# certo, em `platform-ui/src/app/routes.tsx` (comentário da F4):
#
#   "Import órfão não é inofensivo: mantém a página compilando e viva no bundle, e
#    sugere ao próximo leitor que existe um caminho até ela."
#
# Foi diagnosticada, escrita — e **recorreu**. Em 2026-08-28 a medição achou 8 páginas
# órfãs vivas na árvore, 5 delas de relatório/monitoração cuja rota já era `Navigate`.
# Diagnóstico sem mecanismo não segura nada (a mesma família do DDL de
# `participation_intervals`, que prometia em prosa a ordenação que ninguém impunha).
#
# Ele NÃO testa produto e NÃO precisa de serviço de pé: é análise estática da árvore.
# Faz três perguntas, e cada uma reprova em silêncio hoje se ninguém perguntar:
#
#   A) toda rota de relatório/monitoração está CLASSIFICADA no de-para da D7?
#   B) existe página órfã NOVA (arquivo vivo, nenhum import)?
#   C) rota não-redirect existe sem entrada no menu (alcançável só por URL)?
#
# Uso:  bash infra/test/probe_report_surface.sh
# Veredicto de TRÊS estados: 0 = OK · 1 = REPROVOU · 3 = INCONCLUSIVO
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UI="$ROOT/packages/platform-ui/src"
ROUTES="$UI/app/routes.tsx"
SIDEBAR="$UI/shell/Sidebar.tsx"

FAIL=0
echo "══ superfície de relatórios — platform-ui ══"

# Um defeito CONFIRMADO vence um "não consegui medir".
#
# Descoberto pela bateria de mutação da F2: ao declarar a fonte errada numa lente, a
# seção D acusou o defeito (❌) e a seção E, logo em seguida, não achou métrica para
# comparar e saiu 3. O chamador via INCONCLUSIVO — isto é, "não medi" — sobre uma
# execução que tinha acabado de MEDIR o defeito. Rebaixar um vermelho a cinza é a
# forma mais fácil de um gate perder o achado que ele mesmo produziu.
inconclusive() {
  if [ "$FAIL" -ne 0 ]; then
    echo
    echo "❌ REPROVOU — houve defeito CONFIRMADO acima; a medição seguinte ficou"
    echo "   inconclusiva, mas isso não rebaixa o que já foi medido."
    exit 1
  fi
  exit 3
}

[ -f "$ROUTES" ] && [ -f "$SIDEBAR" ] || {
  echo "   ⛔ INCONCLUSIVO — routes.tsx ou Sidebar.tsx não encontrados sob $UI."
  echo "      Sem eles nenhuma das três perguntas é respondível."; exit 3; }

# ── A) CLASSIFICAÇÃO DECLARADA (de-para da D7) ────────────────────────────────
# Rota → classe|destino. Editar isto é uma DECISÃO, e é o ponto do probe: rota de
# relatório nova sem linha aqui reprova, em vez de entrar calada.
#
# Classes:
#   superficie   — hospeda uma das duas superfícies (Contatos × Recursos)
#   fica         — unidade de análise diferente; sobrevive por mérito próprio
#   monitor      — mede AGORA, não período; fora do escopo da revisão
#   redirect     — endereço histórico preservado; não é página
declare -A CLASS=(
  [analise/sessions]="superficie|Superfície A · Contatos (níveis journey>session>segment)"
  [analise/agents]="superficie|vira MODO comparar da Superfície B na F3 (D6)"
  [analise/pools]="superficie|vira LENTES da Superfície B na F3"
  [analise/wrapup]="redirect|F2 - absorvido como a lente de disposicao (?lens=disposition) de /analise/sessions"
  [analise/surveys]="superficie|vira DRILL de /analise/customer-voice na F4"
  [analise/quality]="fica|avaliação — unidade de análise distinta (Arc 6)"
  [analise/customer-voice]="fica|sinal do cliente — unidade distinta; absorve surveys na F4"
  [analise/customers]="fica|cliente — ADR próprio (adr-customer-360-two-surfaces), fora de escopo"
  [analise/events]="fica|Arc 12 business events — categoria hierárquica, investigação por session_id"
  [dashboards]="fica|composição do operador sobre o catálogo de DisplayTool"
  [flow/monitor]="monitor|sessões AGORA"
  [contacts/agents]="monitor|agentes AGORA"
  [contacts/pools]="monitor|pools AGORA"
  [monitor/schedules]="monitor|régua de disparos AGORA (Scheduler F3)"
  [monitor/work-items]="monitor|pendências de wrap-up AGORA (I5)"
  [analise/processos]="redirect|absorvido por /analise/sessions?journey= (F3.3)"
  [analise/contatos]="redirect|legado"
  [analise/agentes]="redirect|legado"
  [analise/qualidade]="redirect|legado"
  [contacts]="redirect|legado"
  [contacts/sessions]="redirect|legado"
  [contacts/events]="redirect|F0/D7 — era o MESMO componente de /analise/events"
  [monitor]="redirect|legado"
  [reports]="redirect|legado"
  [agent-flow/monitor]="redirect|Arc 19"
  [agent-flow/report]="redirect|Arc 19"
  [workflow/monitor]="redirect|Arc 19"
  [workflow/report]="redirect|Arc 19"
  [workflows]="redirect|Arc 19"
  [campaigns]="redirect|legado"
  [config/agent-reports]="redirect|legado → /analise/agents"
)
# Prefixos sob os quais uma rota é considerada "de relatório/monitoração".
SCOPE_RE='^(analise/|contacts$|contacts/|flow/|monitor$|monitor/|dashboards$|reports$|agent-flow/(monitor|report)$|workflow/(monitor|report)$|workflows$|campaigns$|config/agent-reports$)'

echo
echo "── A) rotas de relatório classificadas ──"
UNCLASSIFIED=()
while read -r p; do
  [[ "$p" =~ $SCOPE_RE ]] || continue
  if [ -n "${CLASS[$p]+x}" ]; then
    printf '   %-26s %s\n' "$p" "${CLASS[$p]%%|*}"
  else
    UNCLASSIFIED+=("$p")
  fi
done < <(grep -oE "path: '[^']+'" "$ROUTES" | sed -E "s/path: '([^']+)'/\1/" | sort -u)

if [ ${#UNCLASSIFIED[@]} -gt 0 ]; then
  FAIL=1
  echo "   ❌ ${#UNCLASSIFIED[@]} rota(s) de relatório SEM linha no de-para da D7:"
  for p in "${UNCLASSIFIED[@]}"; do echo "      · $p"; done
  echo "      Classificar em CLASS acima é a decisão; entrar calada é o defeito."
else
  echo "   ✅ nenhuma rota de relatório fora do de-para"
fi

# ── B) PÁGINAS ÓRFÃS ──────────────────────────────────────────────────────────
# LINHA DE BASE MEDIDA em 2026-08-28, DEPOIS da F0 (que removeu 5: AnaliseComparacaoPage,
# MetricSelector, AgentReportsPage, AnaliseAgentesPage, ProcessosPage).
#
# ⚠️ Esta lista é DÍVIDA RECONHECIDA, não permissão. Ela não pode CRESCER — órfã nova
#    reprova.
#
# São **17** desde a F2, que removeu a `AnaliseTab` (ver a linha comentada abaixo).
# Nenhuma é página de RELATÓRIO com rota — a F0b esgotou essa família. São componentes
# e abas do agent-assist/service/config, cada uma com dono e história próprios.
#
# ⚠️ Duas afirmações antigas deste bloco estavam ERRADAS e saíram na F2: *"Cinco das
# oito são páginas de relatório"* (eram oito quando a lista tinha oito; virou 18 e
# depois 17, e a frase não acompanhou) e a contagem "18" logo abaixo. Comentário que
# carrega número é comentário que envelhece — o número que vale é o do array.
#
# Duas contam a mesma mentira e valem revisão de alguém: `ContextoTab.tsx:6` afirma
# que o `EstadoTab` "was removed in Fase C" (não foi — foi desligado, e o CLAUDE.md
# ainda o descreve como superfície viva de sentimento), e `ChannelsPage.tsx:3` diz
# ter sido substituída pelo `GatewayConfigPanel` — que também está órfão. O
# substituto e o substituído morreram juntos.
BASELINE_ORPHANS=(
  "modules/_placeholder/PlaceholderPage.tsx"
  "modules/agent-assist/components/CloseModal.tsx"
  "modules/agent-assist/components/JourneyPanel.tsx"
  "modules/agent-assist/components/PresenceSidebar.tsx"
  "modules/agent-assist/components/WebRTCSupervisorView.tsx"
  "modules/agent-assist/components/tabs/AgentesTab.tsx"
  "modules/agent-assist/components/tabs/CapacidadesTab.tsx"
  "modules/agent-assist/components/tabs/EstadoTab.tsx"
  "modules/agent-assist/components/tabs/OrchestrationTab.tsx"
  "modules/agent-assist/hooks/useAgentWebSocket.ts"
  "modules/agent-assist/hooks/useMentionableProcesses.ts"
  "modules/config-channels/GatewayConfigPanel.tsx"
  "modules/config-recursos/ChannelsPage.tsx"
  "modules/config-recursos/JourneyTypesPage.tsx"
  "modules/contacts/tabs/AgentsTab.tsx"
  # `modules/contacts/tabs/AnaliseTab.tsx` SAIU na F2 (2026-08-28) — não por limpeza,
  # por substituição medida: ela era a única consumidora viva de
  # `/reports/timeseries/{volume,handle_time}` na área de contatos, e agregava KPIs no
  # CLIENTE sobre um `FETCH_LIMIT = 1000` — isto é, um total que parava de crescer aos
  # mil sem dizer nada. As lentes `volume`/`duration` da superfície A respondem a mesma
  # pergunta no servidor, sob os filtros da barra e com `sample` por bucket.
  "modules/service/components/HeatmapGrid.tsx"
  "modules/service/components/MetricsPanel.tsx"
)

echo
echo "── B) órfãos (arquivo vivo, nenhum import) ──"
# A alcançabilidade é decidida por RESOLUÇÃO de import (`_ui_orphans.py`), não por
# `grep` de basename. Medido na F0b: casar nome tem duas classes de erro, e as duas
# existiam nesta árvore — falso negativo por colisão (`campaigns/CampaignsPage`
# escondida pela homônima em `evaluation/`) e, ao "consertar" com caminho, falso
# positivo em `index.tsx` (import de diretório) e em irmãos (`./Base`).
ORPHAN_TOOL="$ROOT/infra/test/_ui_orphans.py"
[ -f "$ORPHAN_TOOL" ] || { echo "   ⛔ INCONCLUSIVO — $ORPHAN_TOOL ausente."; inconclusive; }
ORPHANS_RAW="$(python3 "$ORPHAN_TOOL" "$UI" 2>/dev/null)" || {
  echo "   ⛔ INCONCLUSIVO — o resolvedor de imports falhou; um verde aqui não mediria nada."
  inconclusive; }
N_FILES="$(find "$UI/modules" -name "*.tsx" | wc -l)"
if [ "${N_FILES:-0}" -lt 20 ]; then
  echo "   ⛔ INCONCLUSIVO — só $N_FILES arquivos sob modules/ (esperado ≥ 20)."; inconclusive; fi

NEW_ORPHANS=()
KNOWN_STILL=0
while read -r f; do
  [ -n "$f" ] || continue
  known=0
  for k in "${BASELINE_ORPHANS[@]}"; do [ "$k" = "$f" ] && known=1 && break; done
  if [ "$known" -eq 1 ]; then KNOWN_STILL=$((KNOWN_STILL+1)); else NEW_ORPHANS+=("$f"); fi
done <<< "$ORPHANS_RAW"

echo "   $N_FILES arquivos varridos · dívida reconhecida ainda presente: $KNOWN_STILL de ${#BASELINE_ORPHANS[@]}"
if [ ${#NEW_ORPHANS[@]} -gt 0 ]; then
  FAIL=1
  echo "   ❌ ${#NEW_ORPHANS[@]} órfã(s) NOVA(s) — fora da linha de base:"
  for f in "${NEW_ORPHANS[@]}"; do echo "      · $f"; done
  echo "      Ou apague o arquivo, ou ligue-o a uma rota. Deixá-lo vivo e inalcançável"
  echo "      é o defeito que este probe existe para impedir."
else
  echo "   ✅ nenhuma órfã nova"
fi
cd "$ROOT" || inconclusive

# ── C) ROTA VIVA E INVISÍVEL NO MENU ──────────────────────────────────────────
# Exceções DECLARADAS: rotas legitimamente sem item de menu (drill, detalhe, destino
# de deep-link). Rota que não estiver aqui nem no Sidebar é alcançável só por URL —
# foi assim que `/analise/agents-legacy` e `/flow/processos` sobreviveram invisíveis.
declare -A NAV_EXEMPT=(
)
echo
echo "── C) rotas de relatório vivas e ausentes do menu ──"
INVISIBLE=()
for p in "${!CLASS[@]}"; do
  cls="${CLASS[$p]%%|*}"
  [ "$cls" = "redirect" ] && continue
  [ -n "${NAV_EXEMPT[$p]+x}" ] && continue
  grep -q "href: '/$p'" "$SIDEBAR" || INVISIBLE+=("$p")
done
if [ ${#INVISIBLE[@]} -gt 0 ]; then
  FAIL=1
  echo "   ❌ ${#INVISIBLE[@]} rota(s) viva(s) sem entrada no Sidebar:"
  for p in "${INVISIBLE[@]}"; do echo "      · /$p — ${CLASS[$p]#*|}"; done
  echo "      Ou entra no menu, ou vira redirect, ou ganha linha em NAV_EXEMPT."
else
  echo "   ✅ toda rota viva tem menu ou isenção declarada"
fi

# ── D) CONTRATO DE LENTE × BACKEND (F1) ───────────────────────────────────────
# O contrato mora no platform-ui porque é lá que é consumido (o platform-ui não
# importa `@plughub/schemas`, e o backend é Python — pôr a declaração no pacote de
# schemas a deixaria sem nenhum leitor). A coerência com o backend, então, não pode
# ser convenção: é medida aqui. Lente declarada num lado e ausente no outro é
# exatamente o tipo de deriva que só aparece quando alguém abre a tela.
CONTRACT="$UI/modules/analise/lens-contract.ts"
PYLENS="$ROOT/packages/analytics-api/src/plughub_analytics_api/reports_query.py"
echo
echo "── D) contrato de lente × _COMPARE_LENSES do backend ──"
if [ ! -f "$CONTRACT" ] || [ ! -f "$PYLENS" ]; then
  echo "   ⛔ INCONCLUSIVO — contrato ou reports_query.py ausente; nada a comparar."
  inconclusive
fi
# ⚠️ A extração do lado TS é um PARSER (`_lens_census.py`), não `grep`. Desde a F2
# há duas fontes de lente, e o discriminador (`source`) vive DEPOIS do `id` no mesmo
# bloco — casar os dois exige acompanhar o bloco. Com o grep antigo, cada lente da
# superfície A faria esta seção ficar vermelha por não estar no `_COMPARE_LENSES` da
# mesa, que é reprovar pelo motivo errado.
CENSUS="$(python3 "$(dirname "$0")/_lens_census.py" "$CONTRACT" 2>&1)" || {
  echo "   ⛔ INCONCLUSIVO — censo de lentes falhou:"; echo "$CENSUS" | sed 's/^/      /'
  inconclusive
}
if echo "$CENSUS" | grep -q " MISSING$"; then
  FAIL=1
  echo "   ❌ lente sem \`source\` declarado (o tipo exige; o gate confere):"
  echo "$CENSUS" | grep " MISSING$" | sed 's/^/      · /'
fi
# Só as lentes servidas pela MESA precisam existir no `_COMPARE_LENSES`. As demais
# são declaradas com a própria fonte, e a exceção é NOMEADA em vez de silenciosa.
TS_LENSES="$(echo "$CENSUS" | awk '$2 == "agents_compare" || $2 == "backend_only" {print $1}' | sort -u)"
OTHER_TS="$(echo "$CENSUS" | awk '$2 != "agents_compare" && $2 != "backend_only" && $2 != "MISSING" {print $1" ("$2")"}' | sort)"
PY_LENSES="$( sed -n '/^_COMPARE_LENSES = {/,/}/p' "$PYLENS" | grep -oE '"[a-z_]+"' | tr -d '"' | sort -u )"
ONLY_TS="$(comm -23 <(echo "$TS_LENSES") <(echo "$PY_LENSES"))"
ONLY_PY="$(comm -13 <(echo "$TS_LENSES") <(echo "$PY_LENSES"))"
N_TS="$(echo "$TS_LENSES" | grep -c .)"; N_PY="$(echo "$PY_LENSES" | grep -c .)"
if [ "${N_TS:-0}" -lt 5 ] || [ "${N_PY:-0}" -lt 5 ]; then
  echo "   ⛔ INCONCLUSIVO — extraí $N_TS lentes de mesa do TS e $N_PY do Python (esperado ≥ 5)."
  echo "      O parser deste probe deixou de casar com a forma dos arquivos; um verde"
  echo "      aqui não significaria nada."; inconclusive
fi
echo "   mesa — TS: $N_TS lentes · Python: $N_PY lentes"
if [ -n "$ONLY_TS" ] || [ -n "$ONLY_PY" ]; then
  FAIL=1
  [ -n "$ONLY_TS" ] && { echo "   ❌ declarada no contrato e AUSENTE no backend:"; \
                         for l in $ONLY_TS; do echo "      · $l"; done; }
  [ -n "$ONLY_PY" ] && { echo "   ❌ servida pelo backend e AUSENTE no contrato:"; \
                         for l in $ONLY_PY; do echo "      · $l"; done
                         echo "      Se for lente buscada e não plotável, o lugar dela é"
                         echo "      BACKEND_ONLY_LENSES — declarada, não esquecida."; }
else
  echo "   ✅ os dois lados declaram exatamente as mesmas lentes de mesa"
fi
if [ -n "$OTHER_TS" ]; then
  echo "   outras fontes (fora do escopo do _COMPARE_LENSES, por declaração):"
  echo "$OTHER_TS" | sed 's/^/      · /'
fi

# ── E) MÉTRICAS DA SUPERFÍCIE A: contrato TS × `_SERIES` do Python (F2) ───────
# Mesmo mecanismo da D, um nível abaixo. O `ContactLensChart` plota pelo que a
# DECLARAÇÃO diz, e o backend devolve `values` chaveado pelo que o `_SERIES` diz. Se
# os dois divergirem, o gráfico fica em branco com a resposta cheia — que é a forma
# mais cara de "vazio ≠ zero": não há erro, não há aviso, e o dado chegou.
CS="$ROOT/packages/analytics-api/src/plughub_analytics_api/contacts_series.py"
echo
echo "── E) métricas da superfície A: contrato × backend ──"
if [ ! -f "$CS" ]; then
  echo "   ⛔ INCONCLUSIVO — contacts_series.py ausente."; inconclusive
fi
METRICS="$(python3 "$(dirname "$0")/_lens_census.py" --metrics "$CONTRACT" "$CS" 2>&1)" || {
  echo "   ⛔ INCONCLUSIVO — censo de métricas falhou:"; echo "$METRICS" | sed 's/^/      /'
  inconclusive
}
TS_METRICS="$(echo "$METRICS" | awk '$1 == "ts" {print $2}' | sort -u)"
PY_METRICS="$(echo "$METRICS" | awk '$1 == "py" {print $2}' | sort -u)"
N_TM="$(echo "$TS_METRICS" | grep -c .)"; N_PM="$(echo "$PY_METRICS" | grep -c .)"
# O TS é subconjunto do Python de propósito: `peak_max` é servido e não plotado.
FALTA="$(comm -23 <(echo "$TS_METRICS") <(echo "$PY_METRICS"))"
echo "   TS plota $N_TM métricas · backend serve $N_PM"
if [ -n "$FALTA" ]; then
  FAIL=1
  echo "   ❌ a UI plota métrica que o backend NÃO devolve (gráfico em branco, sem erro):"
  for m in $FALTA; do echo "      · $m"; done
else
  echo "   ✅ toda métrica plotada é servida pelo backend"
fi

# ── F) A LENTE E A LISTA FALAM DA MESMA POPULAÇÃO (F2, contra dado real) ──────
# A asserção central da superfície A: `Σ sample` da série == `total_contacts` da
# lista, sob os MESMOS filtros. Um teste com mock não pode prová-la (devolve o que
# se mandou); só o ClickHouse da instalação responde.
#
# ⚠️ INCONCLUSIVO, nunca verde, quando a API não responde — um gate que passa por
# não ter conseguido medir é o defeito que ele existe para caçar.
echo
echo "── F) série × lista sob os mesmos filtros (dado real) ──"
# shellcheck source=/dev/null
if ! . "$(dirname "$0")/_auth.sh" 2>/dev/null; then
  echo "   ⛔ INCONCLUSIVO — _auth.sh ausente."; inconclusive
fi
AN="${AN:-http://localhost:3500}"
if ! curl -sf -o /dev/null --max-time 5 "$AN/v1/health" 2>/dev/null; then
  echo "   ⛔ INCONCLUSIVO — analytics-api não responde em $AN/v1/health."
  echo "      Sem ela, 'as duas dizem 0' passaria por concordância."
  inconclusive
fi
W="tenant_id=$TENANT&from_dt=2026-01-01&to_dt=2030-01-01"
DIVERGE=0; MEDIDO=0
for FILTRO in "" "channel=webchat" "direction=inbound" "outcome=resolved"; do
  Q="$W${FILTRO:+&$FILTRO}"
  L="$(acurl -s --max-time 20 "$AN/reports/sessions?$Q&page_size=1" | jq -r '.meta.total_contacts // "x"')"
  S="$(acurl -s --max-time 20 "$AN/reports/contacts/series?$Q&metric=volume&interval=1440" \
       | jq -r '[.buckets[].sample] | add // 0')"
  case "$L" in ''|x|null) echo "   ⛔ INCONCLUSIVO — a lista não respondeu para '${FILTRO:-sem filtro}'."; inconclusive ;; esac
  [ "$L" -gt 0 ] && MEDIDO=$((MEDIDO + 1))
  if [ "$L" = "$S" ]; then
    printf "   ✅ %-22s lista=%-6s série=%s\n" "${FILTRO:-sem filtro}" "$L" "$S"
  else
    DIVERGE=1; FAIL=1
    printf "   ❌ %-22s lista=%-6s série=%s  ← populações diferentes\n" "${FILTRO:-sem filtro}" "$L" "$S"
  fi
done
# Testemunha de PRESENÇA: se toda medição deu 0, "concordam" não prova nada — os
# dois lados podem estar quebrados do mesmo jeito. Vale o mesmo caveat do aging.
if [ "$MEDIDO" -eq 0 ]; then
  echo "   ⛔ INCONCLUSIVO — todos os filtros devolveram 0 contatos."
  echo "      Concordância entre dois zeros não é evidência de nada."
  inconclusive
fi
[ "$DIVERGE" -eq 0 ] && echo "   ($MEDIDO de 4 filtros com população não-vazia)"

# ── Fases futuras — declaradas para NÃO parecerem cobertas ────────────────────
echo
echo "── não coberto ainda (por fase, não por esquecimento) ──"
echo "   · F2/F3: a lente de DISPOSIÇÃO honra só o intervalo (agrega sobre pools"
echo "         internos). O contrato o declara (\`honors: period_only\`) e a tela o diz;"
echo "         estender \`/reports/wrapup-summary\` aos demais filtros é trabalho próprio"
echo "   · F3: o DISPATCH do gráfico ainda é cascata de if — o contrato já declara"
echo "         entidade/evidência/comparabilidade, mas a forma do gráfico só entra"
echo "         quando a mesa virar modo (D6)"
echo "   · F3: a superfície de RECURSOS (e com ela a lente de token do lado da oferta)."
echo "         A T3 entregou a lente de token da superfície A; a metade B espera a F3,"
echo "         que é onde a entidade deixa de ser o contato"

echo
if [ "$FAIL" -eq 0 ]; then
  echo "✅ APROVADO — superfície de relatórios classificada, sem órfã nova, sem rota invisível"
  exit 0
fi
echo "❌ REPROVOU — ver seções acima"
exit 1
