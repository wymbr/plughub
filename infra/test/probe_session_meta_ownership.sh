#!/usr/bin/env bash
# probe_session_meta_ownership.sh — MEDIÇÃO, não portão.
#
# Objeto: `session:{id}:meta`, chave com SEIS escritores, nenhum schema, nenhum
# helper, e nenhuma partição de propriedade declarada. Este probe não conserta
# nada; ele decide QUAL dos dois defeitos candidatos existe de fato, porque os
# dois produzem a mesma tela para quem só olha o valor final:
#
#   (a) TRUNCAMENTO DE TTL — a porta webhook escreve `SETEX … 86_400` (24 h) e o
#       bridge, na alocação, reescreve com `SETEX … _stl()` (14_400 = 4 h). O
#       prazo do meta passa a ser MENOR que o da workflow suspensa que ele
#       descreve (delegate/collect usam `timeout_hours*3600 + 3600`, 48 h por
#       default). Depois de 4 h, `resolveSessionTenant` e `conversation_escalate`
#       RECUSAM (arco P2, 08-18) uma sessão que ainda está viva e retomável.
#
#   (b) PERDA DE CAMPO — os quatro escritores de canal usam `SETEX` CEGO (o JSON
#       inteiro), não merge. Se qualquer um deles correr DEPOIS do bridge, os
#       campos que o bridge pôs (`instance_id`, `agent_type_id`, `pool_id`)
#       somem sem erro em lugar nenhum.
#
# A passagem de 08-21 mediu "8/8 metas na faixa ≤ 4 h, 0 acima" e registrou que
# isso NÃO prova (a): o webchat NASCE com 4 h, então a faixa é a mesma nos dois
# mundos. Falta o discriminador — uma sessão que nasceu com 24 h e passou por
# alocação. É exatamente o que P1 monta.
#
# ── PREVISÕES (escritas ANTES de rodar; contar, não estimar) ──────────────────
#   P0  inventário: ≥1 meta no Redis, com histograma por `channel`. Se 0 → INCONCLUSIVO.
#   P1  no trigger de `formfill_demo_ia`:
#         · TTL máximo observado  = 86400  (ou 86399/86398 — a leitura tem latência)
#         · TTL após a alocação   = 14400  (queda de ~72_000 s)
#         ⇒ se o máximo NUNCA passar de 14400, o probe NÃO alcançou a condição:
#           INCONCLUSIVO, não "aprovado". O caminho medido não foi o do webhook.
#   P2  diff dos CAMPOS entre T0 (pré-alocação) e T1 (pós):
#         · previsto GANHAR: agent_type_id, instance_id, pool_id
#         · previsto PERDER: NENHUM — o site do bridge (main.py:4563) é
#           GET+merge+SETEX, então (b) não deve aparecer NESTE caminho.
#         ⇒ se algum campo sumir, (b) também é real e tem site com nome.
#
# Veredicto de TRÊS estados: 0 = mediu · 1 = mediu e o defeito existe · 3 = INCONCLUSIVO
set -uo pipefail

COMPOSE="${COMPOSE:-docker compose -f docker-compose.demo.yml}"
GW="${GW:-http://localhost:8010}"
TENANT="${TENANT:-tenant_demo}"
TRIGGER_POOL="${TRIGGER_POOL:-formfill_demo_ia}"

R()    { $COMPOSE exec -T redis redis-cli "$@" < /dev/null 2>/dev/null | tr -d '\r'; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$@"; }
body() { curl -s --max-time 15 "$@"; }
# Nomes de campo do JSON. `json.dumps` do Python escreve `"k": v` COM espaço e o
# `JSON.stringify` do Node escreve `"k":v` SEM — casar um só dos dois contaria 0
# num dado correto (foi assim que um `case` mentiu em 08-21).
fields() { printf '%s' "$1" | grep -o '"[a-z_]\+"[[:space:]]*:' | tr -d '":' | tr -d ' ' | sort -u; }

echo "══ session:{id}:meta — propriedade e prazo ══"
echo "   gw=$GW tenant=$TENANT trigger_pool=$TRIGGER_POOL"

# ── Preflight ────────────────────────────────────────────────────────────────
[ "$(R PING)" = "PONG" ] || { echo "   ⛔ INCONCLUSIVO — redis inalcançável"; exit 3; }
[ "$(code "$GW/health")" = "200" ] || { echo "   ⛔ INCONCLUSIVO — channel-gateway fora do ar"; exit 3; }

# ── P0 · inventário: histograma channel × faixa de TTL ───────────────────────
echo
echo "── P0 · inventário de metas vivas ────────────────────────────────────────"
KEYS="$(R --scan --pattern 'session:*:meta' | head -400)"
NKEYS="$(printf '%s\n' "$KEYS" | grep -c . )"
if [ "$NKEYS" -eq 0 ]; then
  # NB: sem crases nesta linha — dentro de aspas duplas o bash as trata como
  # substituição de comando (pegadinha registrada no gate_external_resume.sh).
  echo '   ⛔ INCONCLUSIVO — nenhuma chave session:*:meta viva. Rode um contato antes.'
  exit 3
fi
echo "      metas vivas: $NKEYS"
# A coluna pool_id imprime "presente"/"ausente" — nunca só a presença. Um contador
# de ausência sozinho não distingue "campo ausente" de "leitura falhou".
printf '%s\n' "$KEYS" | while read -r k; do
  [ -n "$k" ] || continue
  t="$(R TTL "$k")"; v="$(R GET "$k")"
  ch="$(printf '%s' "$v" | grep -o '"channel"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*"\([^"]*\)"$/\1/')"
  pi="ausente"; printf '%s' "$v" | grep -q '"pool_id"' && pi="presente"
  printf '      %-46s ttl=%-7s channel=%-10s pool_id=%s\n' "${k#session:}" "$t" "${ch:-?}" "$pi"
done
echo
echo "      (leia a coluna pool_id: o adapter webhook se ABSTÉM dela de propósito"
echo "       — meta de webhook COM pool_id só pode ter vindo do bridge)"

# ── P1 · o discriminador, SEM corrida ────────────────────────────────────────
#
# A v1 deste passo tentava pegar o 86400 lendo o TTL logo após o POST, e voltou
# INCONCLUSIVO (máximo observado = 14400). O motivo NÃO é que o W3 não escreve:
# cada `docker compose exec` custa ~0,5 s, e a alocação cabe folgada nesse
# intervalo — eu estava correndo contra o defeito em vez de encurralá-lo.
#
# O controle determinístico troca UM valor no MESMO caminho: `formfill_demo_ia`
# declara `max_concurrent_sessions: 1`. Uma rajada de N triggers faz o 1º ser
# ALOCADO e os demais FICAREM NA FILA. Sessão em fila não recebe escrita do
# bridge, então o meta dela preserva o que a porta webhook escreveu.
#
#   · alguma na faixa 86400  ⇒ TESTEMUNHA: o W3 escreve mesmo 24 h
#   · alguma na faixa 14400  ⇒ TRUNCAMENTO: só a alocação explica a diferença
#   · TODAS em 86400         ⇒ o bridge não trunca; o defeito (a) não existe
#   · TODAS em 14400         ⇒ o W3 não escreve — aí o alvo é outro (ver P1b)
#
# As duas contagens ficam na MESMA tela, que foi o que salvou as duas medições
# erradas de 08-21.
echo
echo "── P1 · rajada: alocada × enfileirada, mesma porta ───────────────────────"
BURST="${BURST:-5}"
SIDS=""
for n in $(seq 1 "$BURST"); do
  s="$(body -X POST "$GW/v1/channels/webhook/pool/$TRIGGER_POOL" \
        -H 'content-type: application/json' \
        -d "{\"tenant_id\":\"$TENANT\",\"payload\":{}}" \
      | sed -E 's/.*"session_id":"([^"]+)".*/\1/')"
  [ -n "$s" ] && [ "${#s}" -ge 6 ] && SIDS="$SIDS $s"
done
NS="$(printf '%s' "$SIDS" | wc -w)"
if [ "$NS" -lt 2 ]; then
  echo "   ⛔ INCONCLUSIVO — a rajada rendeu $NS sessões (esperado $BURST)."
  exit 3
fi
echo "      sessões disparadas: $NS"

# Uma leitura = UM exec, com N comandos TTL por dentro. Sem isto o custo do exec
# domina a janela e o probe volta a medir a própria latência.
CMDS=""; for s in $SIDS; do CMDS="${CMDS}TTL session:$s:meta\n"; done
sample() { $COMPOSE exec -T redis sh -c "printf '$CMDS' | redis-cli" < /dev/null 2>/dev/null | tr -d '\r'; }

MAXTTL=-99; MINTTL=999999; ACIMA=0; ABAIXO=0; AUSENTE=0
for round in 1 2 3 4 5 6 7 8; do
  R_OUT="$(sample)"
  [ "$round" -eq 1 ] && { echo "      1ª leitura (t+~1s):"; printf '%s\n' "$R_OUT" | sed 's/^/         ttl=/'; }
  for t in $R_OUT; do
    case "$t" in ''|*[!0-9-]*) continue ;; esac
    [ "$t" -lt 0 ] && { AUSENTE=$((AUSENTE+1)); continue; }
    [ "$t" -gt "$MAXTTL" ] && MAXTTL="$t"
    [ "$t" -lt "$MINTTL" ] && MINTTL="$t"
  done
  sleep 2
done
FINAL="$(sample)"
echo "      leitura final:"
printf '%s\n' "$FINAL" | sed 's/^/         ttl=/'
for t in $FINAL; do
  case "$t" in ''|*[!0-9-]*) continue ;; esac
  if   [ "$t" -gt 14400 ]; then ACIMA=$((ACIMA+1))
  elif [ "$t" -ge 0 ];     then ABAIXO=$((ABAIXO+1))
  else AUSENTE=$((AUSENTE+1)); fi
done
echo "      TTL máximo observado em qualquer leitura: $MAXTTL"
echo "      TTL mínimo observado em qualquer leitura: $MINTTL"
echo "      na leitura final — acima de 4h: $ACIMA · até 4h: $ABAIXO · ausente: $AUSENTE"

RC=0
if [ "$MAXTTL" -le 14400 ]; then
  echo "   ⛔ INCONCLUSIVO para o truncamento — NENHUMA das $NS sessões foi vista"
  echo "      acima de 4 h. Se nem a enfileirada nasce com 86400, o W3"
  echo "      (webhook.py:595) não está no caminho deste trigger. Ver P1b."
  RC=3
elif [ "$ABAIXO" -gt 0 ] && [ "$ACIMA" -gt 0 ]; then
  echo "   ❌ TRUNCAMENTO CONFIRMADO — $ACIMA sessão(ões) em 24 h e $ABAIXO em 4 h,"
  echo "      mesma porta, mesmo payload. A única variável foi a ALOCAÇÃO."
  RC=1
elif [ "$ABAIXO" -eq 0 ]; then
  echo "   ✅ nenhuma caiu para 4 h — o bridge não encurtou nesta janela."
  echo "      (a fila pode ter drenado depois; releia com BURST maior se quiser)"
else
  echo "   ⛔ INCONCLUSIVO — todas acabaram em 4 h e o máximo foi $MAXTTL:"
  echo "      não dá para separar 'nasceu 4 h' de 'nasceu 24 h e foi truncada'."
  RC=3
fi

# ── P1b · a porta webhook chegou a escrever? ─────────────────────────────────
# Contador de AUSÊNCIA (erro do W3) com TESTEMUNHA de presença ao lado: sem a
# testemunha, "0 erros" e "log fora da janela" dão a mesma tela.
echo
echo "── P1b · o W3 falhou em silêncio? ────────────────────────────────────────"
LOGW="$($COMPOSE logs --since 5m channel-gateway 2>/dev/null | tr -d '\r')"
NLOG="$(printf '%s\n' "$LOGW" | grep -c .)"
NERR="$(printf '%s\n' "$LOGW" | grep -c 'meta NÃO escrito')"
NTRG="$(printf '%s\n' "$LOGW" | grep -c 'webhook trigger')"
echo "      linhas na janela: $NLOG · 'webhook trigger': $NTRG · 'meta NÃO escrito': $NERR"
if [ "$NLOG" -eq 0 ]; then
  echo "      ⛔ janela de log VAZIA — P1b não julga (container recriado?)"
elif [ "$NERR" -gt 0 ]; then
  echo "      ❌ o W3 falhou $NERR vez(es) — o meta de 24 h nunca existiu"
elif [ "$NTRG" -eq 0 ]; then
  echo "      ⛔ nenhuma linha 'webhook trigger' na janela — o logger deste caminho"
  echo "         está em WARNING, então a ausência de erro não prova sucesso"
else
  echo "      ✅ $NTRG trigger(s) logados e 0 falhas de escrita — o W3 rodou"
fi

# ── P2 · campos: sessão MAIS CURTA (alocada) × MAIS LONGA (enfileirada) ──────
#
# Sem corrida também aqui: em vez de fotografar a mesma sessão duas vezes, compara
# DUAS sessões da mesma rajada que diferem só na alocação. O que aparece só na
# curta é escrita do BRIDGE; o que aparece nas duas é escrita da PORTA.
echo
echo "── P2 · campos por dono, derivado da rajada ──────────────────────────────"
CURTA=""; LONGA=""; CURTA_T=999999; LONGA_T=-1
for s in $SIDS; do
  t="$(R TTL "session:$s:meta")"
  case "$t" in ''|*[!0-9-]*) continue ;; esac
  [ "$t" -lt 0 ] && continue
  [ "$t" -lt "$CURTA_T" ] && { CURTA_T="$t"; CURTA="$s"; }
  [ "$t" -gt "$LONGA_T" ] && { LONGA_T="$t"; LONGA="$s"; }
done
if [ -z "$CURTA" ] || [ "$CURTA" = "$LONGA" ]; then
  echo "   ⛔ INCONCLUSIVO — não há par (curta, longa) na rajada; nada a comparar."
  [ "$RC" -eq 0 ] && RC=3
else
  F_CURTA="$(fields "$(R GET "session:$CURTA:meta")")"
  F_LONGA="$(fields "$(R GET "session:$LONGA:meta")")"
  echo "      curta  ttl=$CURTA_T : $(printf '%s' "$F_CURTA" | tr '\n' ' ')"
  echo "      longa  ttl=$LONGA_T : $(printf '%s' "$F_LONGA" | tr '\n' ' ')"
  SO_CURTA="$(comm -23 <(printf '%s\n' "$F_CURTA") <(printf '%s\n' "$F_LONGA") | tr '\n' ' ')"
  SO_LONGA="$(comm -13 <(printf '%s\n' "$F_CURTA") <(printf '%s\n' "$F_LONGA") | tr '\n' ' ')"
  echo "      só na curta (⇒ dono = BRIDGE): ${SO_CURTA:-<nenhum>}"
  echo "      só na longa (⇒ o bridge APAGOU): ${SO_LONGA:-<nenhum>}"
  if [ -n "$SO_LONGA" ]; then
    echo "   ❌ PERDA DE CAMPO — a escrita do bridge não preservou o que a porta pôs."
    RC=1
  elif [ -n "$SO_CURTA" ]; then
    echo "   ✅ merge preserva — a partição de fato é: esses campos são do bridge."
  else
    echo "   ⛔ conjuntos idênticos: ou a longa também foi alocada, ou a curta não."
    [ "$RC" -eq 0 ] && RC=3
  fi
fi

echo
echo "══ veredicto: rc=$RC (0 mediu · 1 defeito confirmado · 3 inconclusivo) ══"
exit "$RC"
