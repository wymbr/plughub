#!/usr/bin/env bash
# gate_queue_segment_not_born_without_flow.sh — P2/F1.
#
# O DEFEITO. Em `_activate_queue_agent` a ordem era: marcador `queue:agent_active` →
# `participant_joined` (segmento `role='queue'`) → só então `activate_native_agent`, que é
# quem descobre se existe flow. Num pool sem slot `current` o resultado era `{}` em ~3 ms, e
# ficava no banco um segmento de fila que **não enfileirou ninguém**. Segmento é um FATO sobre
# atendimento; abri-lo antes de saber se há o que atender é afirmar o fato antes de ele existir.
#
# F1 move a resolução para ANTES: sem flow, o bridge loga ERROR nomeando o pool e retorna —
# sem marcador e sem segmento. O contato espera em silêncio e o drain re-roteia, que é
# exatamente o comportamento de um pool sem `queue_config`.
#
# F2 (2026-08-18) — o gate passou a julgar TAMBÉM o endereçamento. `queue_config.pool_id`
# endereça um POOL de fila, e o bridge separou os dois fatos que viviam na variável `pool_id`:
#   · destino = onde o contato espera  → é o que o SEGMENTO carimba (dimensão de Fila/SLA)
#   · fila    = de quem é o DEPLOY     → é o slot que resolve o flow
# A asserção é DIFERENCIAL: quando `fila ≠ destino`, tem de existir segmento no destino e
# NENHUM na fila. Um gate que só contasse segmentos não distinguiria isso de nada.
#
# TRÊS LEITURAS, e o gate afirma a relação entre elas — porque um contador de AUSÊNCIA
# sozinho não distingue "parou de criar fantasma" de "quebrei o agente de fila":
#
#   ausência  → pools que logaram "Agente de fila NÃO ativado" NÃO têm segmento na janela
#   presença  → pools cujo flow resolveu CONTINUAM tendo segmento na janela  ← TESTEMUNHA
#   endereço  → segmento no DESTINO, nunca na FILA                           ← F2
#
# Sem nenhuma das duas primeiras, o veredicto é INCONCLUSIVO: não houve amostra, e
# "0 fantasmas" seria ausência de reprodução, não conserto.
#
# REPRODUÇÃO É MANUAL (como foi na família A). Instante ABSOLUTO, nunca `--since 300s`:
# janela por duração soma a execução anterior e faz duas reproduções virarem uma.
#
#     T0=$(date -u +%FT%TZ); echo "$T0"
#     …abra um contato que ENFILEIRE (pool humano sem ninguém logado)…
#     bash infra/test/gate_queue_segment_not_born_without_flow.sh "$T0"
#
# Sai: 0 = VERDE · 1 = REPROVOU · 2 = INCONCLUSIVO

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
T0="${1:-}"
TENANT="${2:-tenant_demo}"
DB="${DB:-plughub_demo}"
SRC="/app/packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py"

[ -n "$T0" ] || {
  echo "uso: bash infra/test/gate_queue_segment_not_born_without_flow.sh <T0-ISO-UTC> [tenant]"
  echo "     T0=\$(date -u +%FT%TZ)  ← pegue ANTES do fluxo"; exit 2; }

chq() { $DC exec -T clickhouse clickhouse-client -d "$DB" --query "$1" < /dev/null 2>&1; }

PASS=0; FAIL=0
ok()  { echo "   ✅ $1"; PASS=$((PASS+1)); }
bad() { echo "   ❌ $1"; FAIL=$((FAIL+1)); }
die() { echo "   ⛔ INCONCLUSIVO: $1"; exit 2; }

echo "══ P2/F1 — segmento de fila NÃO nasce sem flow · janela desde $T0 ══"
echo

# ── 0. PREFLIGHT — o que RODA, não o que está no repo ────────────────────────
# O container não monta o fonte: sem `build`, este gate mediria a imagem antiga e o
# vermelho seria do código velho, não do mundo.
if ! $DC exec -T orchestrator-bridge grep -q 'destino=%s fila=%s' "$SRC" < /dev/null 2>/dev/null; then
  die "a F1/F2 não está no fonte que roda (procurado: o par 'destino=/fila=' das linhas de
        ativação e recusa, que é o TOKEN que este gate parseia — não o identificador). Rodar:
        docker compose -f docker-compose.demo.yml build orchestrator-bridge
        docker compose -f docker-compose.demo.yml up -d orchestrator-bridge
        (\`restart\` NÃO basta — recarrega a imagem antiga)"
fi
echo "   preflight: a F1/F2 está na imagem em execução"
echo

# ── 0.5. O CONTATO CHEGOU A ENFILEIRAR? ──────────────────────────────────────
#
# ⚠️ Sem esta seção o gate tinha um PONTO CEGO, medido em 2026-08-18: três situações
# muito diferentes produziam a MESMA janela vazia —
#   (i)   o contato nunca enfileirou (montagem do teste: havia agente livre);
#   (ii)  enfileirou, mas o bridge não recebeu `conversations.queued`;
#   (iii) enfileirou, o bridge foi chamado e desistiu no ramo "pool sem queue_config
#         e sem default de tenant", que logava em DEBUG num serviço rodando em INFO.
# Só (ii) e (iii) são achados; (i) é refazer o teste. Distinguir exige perguntar ao
# ROUTING, que é quem sabe se houve fila — não ao bridge, que é o suspeito.
echo "── 0.5. routing: houve fila na janela? ───────────────────────────────────"
FILA=$($DC logs --no-log-prefix -t --since "$T0" routing-engine 2>/dev/null \
       | grep -Ei 'Queued session|Queue drain|QUEUE TIMEOUT' || true)
if [ -n "$FILA" ]; then
  printf '%s\n' "$FILA" | sed 's/^/   /'
else
  echo "   (nenhum evento de fila no routing)"
fi
N_FILA=$(printf '%s' "$FILA" | grep -ci 'Queued session' || true)
echo "   sessões enfileiradas na janela: ${N_FILA:-0}"
echo

# ── 1. os pools que RECUSARAM ativar (ausência esperada) ─────────────────────
echo "── 1. bridge: recusas de ativação na janela ──────────────────────────────"
RECUSAS=$($DC logs --no-log-prefix -t --since "$T0" orchestrator-bridge 2>/dev/null \
          | grep -F 'Agente de fila NÃO ativado' || true)
if [ -n "$RECUSAS" ]; then
  printf '%s\n' "$RECUSAS" | sed 's/^/   /'
else
  echo "   (nenhuma recusa na janela)"
fi
# O pool a casar contra os segmentos é o de DESTINO — é ele que o segmento carimbaria se
# nascesse. (Até a F2 o log dizia `pool=`; casar pelo token antigo devolveria "nenhuma
# recusa" em silêncio, que é um gate incapaz de reprovar.)
POOLS_RECUSA=$(printf '%s' "$RECUSAS" | grep -oE 'destino=[^ ]+' | cut -d= -f2 | sort -u)
N_RECUSA=$(printf '%s' "$POOLS_RECUSA" | grep -c . || true)
echo
echo "   pools que recusaram: ${POOLS_RECUSA:-∅}  (distintos: ${N_RECUSA:-0})"
echo

# ── 2. as ativações que DERAM CERTO (presença — a testemunha) ────────────────
echo "── 2. bridge: ativações concluídas na janela (testemunha de presença) ────"
$DC logs --no-log-prefix -t --since "$T0" orchestrator-bridge 2>/dev/null \
  | grep -F 'Activating queue agent' | sed 's/^/   /' || true
ATIVACOES=$($DC logs --no-log-prefix -t --since "$T0" orchestrator-bridge 2>/dev/null \
            | grep -F 'Activating queue agent' || true)
N_ATIVOU=$(printf '%s' "$ATIVACOES" | grep -cF 'Activating queue agent' || true)
echo "   ativações: ${N_ATIVOU:-0}"
# F2 — os PARES (destino, fila) de cada ativação. Montados no bash, um por linha, para
# não empurrar quoting shell→SQL mais de uma vez.
PARES=$(printf '%s\n' "$ATIVACOES" \
        | grep -oE 'destino=[^ ]+ fila=[^ ]+' \
        | sed 's/destino=//; s/ fila=/ /' | sort -u | grep . || true)
echo

# ── 3. os segmentos de fila nascidos na janela ───────────────────────────────
T0_CH=$(printf '%s' "$T0" | tr 'T' ' ' | tr -d 'Z')
echo "── 3. segmentos role='queue' nascidos na janela ──────────────────────────"
chq "
  SELECT substring(g.session_id,1,8)           AS sessao,
         g.pool_id                             AS pool,
         g.started_at                          AS abriu,
         ifNull(toString(g.ended_at),'ABERTO') AS fechou,
         ifNull(toString(g.duration_ms),'—')   AS dur_ms,
         ifNull(g.outcome,'∅')                 AS outcome
    FROM $DB.segments AS g FINAL
   WHERE g.tenant_id='$TENANT' AND g.role='queue' AND g.started_at >= '$T0_CH'
   ORDER BY g.started_at
   FORMAT PrettyCompactMonoBlock"
echo

N_SEG=$(chq "SELECT count() FROM $DB.segments FINAL
              WHERE tenant_id='$TENANT' AND role='queue' AND started_at >= '$T0_CH'" | tr -d '\r')
# Assinatura do FANTASMA: nasceu e morreu em menos de 100 ms sem outcome. É o que a F1
# existe para não deixar nascer.
N_FANTASMA=$(chq "SELECT count() FROM $DB.segments FINAL
                   WHERE tenant_id='$TENANT' AND role='queue' AND started_at >= '$T0_CH'
                     AND duration_ms < 100 AND (outcome IS NULL OR outcome = '')" | tr -d '\r')

echo "── 4. contadores ─────────────────────────────────────────────────────────"
echo "   segmentos de fila na janela = ${N_SEG:-?}"
echo "   destes, com assinatura de FANTASMA (<100 ms, sem outcome) = ${N_FANTASMA:-?}"
echo

case "${N_SEG:-x}" in ''|*[!0-9]*) die "contagem de segmentos não numérica ('$N_SEG')" ;; esac
case "${N_FANTASMA:-x}" in ''|*[!0-9]*) die "contagem de fantasmas não numérica" ;; esac

# ── VEREDICTO ────────────────────────────────────────────────────────────────
echo "── veredicto ─────────────────────────────────────────────────────────────"

if [ "${N_RECUSA:-0}" -eq 0 ] 2>/dev/null && [ "${N_ATIVOU:-0}" -eq 0 ] 2>/dev/null; then
  # O bridge ficou mudo. O routing decide QUAL dos três casos é — e só um deles
  # significa "refaça o teste".
  if [ "${N_FILA:-0}" -eq 0 ] 2>/dev/null; then
    die "o bridge ficou mudo E o routing não enfileirou ninguém: NÃO HOUVE REPRODUÇÃO.
        Provavelmente havia agente livre no pool e o contato foi roteado direto.
        '0 fantasmas' aqui é ausência de amostra. Refaça garantindo que o pool de
        destino esteja SEM instância pronta (nenhum humano logado)."
  fi
  die "ACHADO, não falta de amostra: o routing enfileirou $N_FILA sessão(ões) e o bridge
        não disse NADA na janela. Três leituras possíveis, e elas pedem consertos
        diferentes:
          (a) o pool não tem \`queue_config\` nem default de tenant — o ramo que
              registra isso passou a logar em INFO em 2026-08-18; se esta janela é
              ANTERIOR ao build dessa mudança, refaça depois de:
                docker compose -f docker-compose.demo.yml build orchestrator-bridge
          (b) o bridge não está consumindo \`conversations.queued\`;
          (c) \`get_pool_config\` falhou (loga warning) — conferir na janela:
                docker compose -f docker-compose.demo.yml logs --since \"$T0\" \\
                  orchestrator-bridge | grep -i 'pool config'"
fi

# (a) o ramo da AUSÊNCIA — só julgável se houve recusa
if [ "${N_RECUSA:-0}" -gt 0 ] 2>/dev/null; then
  SOBRA=0
  for P in $POOLS_RECUSA; do
    N_P=$(chq "SELECT count() FROM $DB.segments FINAL
                WHERE tenant_id='$TENANT' AND role='queue' AND started_at >= '$T0_CH'
                  AND pool_id='$P'" | tr -d '\r')
    [ "${N_P:-0}" -gt 0 ] 2>/dev/null && {
      bad "o pool '$P' recusou ativar E MESMO ASSIM tem $N_P segmento(s) de fila na
       janela — a F1 não pegou, ou há um segundo produtor de segmento de fila"
      SOBRA=1; }
  done
  [ "$SOBRA" -eq 0 ] && ok "todos os $N_RECUSA pool(s) que recusaram ficaram SEM segmento de fila"
else
  echo "   ⚠️  nenhuma recusa na janela: o ramo da ausência não foi exercitado."
  echo "      Para exercitá-lo, enfileire num pool sem slot (ex.: retencao_humano,"
  echo "      que declara skill_fila_v1 e não tem slot). Sem isso o verde abaixo diz"
  echo "      apenas 'não quebrei o caminho feliz'."
fi

# (b) a TESTEMUNHA de presença
#
# ⚠️ O ramo `else` NÃO é decorativo (medido 2026-08-18): numa execução com recusa e
# `ativações: 0`, o gate saía VERDE sem uma palavra sobre a metade que não julgou.
# Um portão que cala sobre o que não mediu vende cobertura que não tem — é a mesma
# família do "teste que não pode reprovar".
if [ "${N_ATIVOU:-0}" -gt 0 ] 2>/dev/null; then
  [ "${N_SEG:-0}" -gt 0 ] 2>/dev/null \
    && ok "TESTEMUNHA: houve $N_ATIVOU ativação(ões) e $N_SEG segmento(s) — o caminho
       feliz continua criando segmento de fila (não foi isso que a F1 desligou)" \
    || bad "houve $N_ATIVOU ativação(ões) e NENHUM segmento de fila: a F1 desligou o
       caminho feliz junto com o fantasma. Isto é regressão, não conserto."
else
  echo "   ⚠️  TESTEMUNHA DE PRESENÇA NÃO EXERCITADA: nenhuma ativação bem-sucedida na"
  echo "      janela. O verde abaixo diz 'a recusa não deixa segmento nascer'; ele NÃO"
  echo "      diz 'o caminho feliz continua criando segmento'. Para cobrir as duas"
  echo "      metades, a mesma janela precisa conter um pool que RECUSA e um que ATIVA"
  echo "      (pool com slot promovido, ou default de tenant configurado)."
fi

# (b2) F2 — os DOIS fatos continuam separados?
#
# Só é julgável quando `fila ≠ destino`; com os dois iguais (pool sem `queue_config.pool_id`)
# a igualdade é o comportamento legado e não prova nada — dizer isso em voz alta é o que
# impede um verde de comprar cobertura que não tem.
N_PAR_DIF=0
if [ -n "${PARES:-}" ]; then
  while read -r DEST FILA_P; do
    [ -n "$DEST" ] || continue
    if [ "$DEST" = "$FILA_P" ]; then
      echo "   ⚠️  ativação com fila=destino ('$DEST'): endereço legado, F2 não julgável aqui."
      continue
    fi
    N_PAR_DIF=$((N_PAR_DIF+1))
    N_D=$(chq "SELECT count() FROM $DB.segments FINAL
                WHERE tenant_id='$TENANT' AND role='queue' AND started_at >= '$T0_CH'
                  AND pool_id='$DEST'" | tr -d '\r')
    N_F=$(chq "SELECT count() FROM $DB.segments FINAL
                WHERE tenant_id='$TENANT' AND role='queue' AND started_at >= '$T0_CH'
                  AND pool_id='$FILA_P'" | tr -d '\r')
    case "${N_D:-x}${N_F:-x}" in *[!0-9]*) die "contagem por pool não numérica (destino='$N_D' fila='$N_F')" ;; esac
    if [ "$N_D" -gt 0 ] && [ "$N_F" -eq 0 ]; then
      ok "F2 ENDEREÇO: fila='$FILA_P' executou o deploy e o segmento ficou no destino
       '$DEST' ($N_D) — nenhum segmento carimbado na fila (0), como manda o invariante
       'never store a narrower-scope fact in a wider-scope field'"
    elif [ "$N_F" -gt 0 ]; then
      bad "F2 ENDEREÇO: $N_F segmento(s) de fila carimbado(s) com o pool de FILA
       ('$FILA_P') em vez do de DESTINO ('$DEST') — os dois fatos voltaram a colapsar"
    else
      bad "F2 ENDEREÇO: houve ativação (fila='$FILA_P', destino='$DEST') e NENHUM segmento
       em nenhum dos dois pools — a ativação não produziu ledger de fila"
    fi
  done <<EOF
$PARES
EOF
fi
[ "$N_PAR_DIF" -eq 0 ] && {
  echo "   ⚠️  F2 NÃO EXERCITADA: nenhuma ativação com pool de fila distinto do destino na"
  echo "      janela. Para exercitá-la, o pool de destino precisa de \`queue_config.pool_id\`"
  echo "      apontando um pool com slot \`current\` promovido (ex.: fila_humano)."
}

# (c) fantasma remanescente — vale mesmo sem recusa no log
[ "${N_FANTASMA:-0}" -eq 0 ] 2>/dev/null \
  && ok "nenhum segmento com assinatura de fantasma na janela" \
  || bad "$N_FANTASMA segmento(s) de fila com <100 ms e sem outcome nasceram na janela:
       a assinatura do defeito continua aparecendo"

echo
echo "   ✅ $PASS · ❌ $FAIL"
echo
echo '   ⚠️  LIMITE: este gate julga a ORDEM (nascer só depois de resolver) e o ENDEREÇO'
echo '      (deploy da fila × carimbo do destino). Ele NÃO julga o CONTEÚDO do'
echo '      atendimento de fila: que o flow certo rodou, que o cliente recebeu as'
echo '      mensagens, que o `__agent_available__` escalou para o destino. Isso é'
echo '      reprodução ao vivo, não contagem de segmento.'
[ "$FAIL" -eq 0 ] && { echo "   ✅ O SEGMENTO DE FILA NÃO NASCE SEM FLOW"; exit 0; }
echo "   ❌ REPROVOU"; exit 1
