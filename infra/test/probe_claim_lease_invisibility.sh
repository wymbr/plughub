#!/usr/bin/env bash
# ==============================================================================
# probe_claim_lease_invisibility.sh — a lacuna 2: a janela em que o item some
# ==============================================================================
#
# O QUE ELE MEDE
# --------------
#   A  a JANELA existe? item reivindicado e abandonado sai da fila e NENHUM outro
#      agente o alcanca depois que a lease vence (com controle POSITIVO ao lado)
#   B  ALCANCE das redes: o CrashDetector devolve o item quando a instancia morre?
#   C  DANO historico: algum item chegou ao prazo sem ninguem (`acw_expired`)?
#
# POR QUE ELE EXISTE (PUL-01)
# ---------------------------
# A ficha diz, desde 2026-08-03, *"ninguem a mediu e nao ha reaper"*. Medir era o
# trabalho — nao consertar. E a afirmacao central vinha de um DOCSTRING
# (`registry.py:105`), que e a familia que ja mentiu duas vezes NESTE MESMO
# ARQUIVO: prometeu "TTL renovado por heartbeat + auto-release" (mecanismo que
# nunca existiu), foi corrigido para "ate o reap de ocupantes orfaos passar" (que
# tambem nao alcanca: `reap_stale_occupants` so colhe sessao FECHADA, e no claim
# abandonado o delegate esta SUSPENSO). O proprio comentario registra: *"uma
# correcao que troca uma rede inexistente por outra e mais cara que o erro
# original"*. Por isso aqui nada e lido — tudo e exercido.
#
# ⚠️ **EXPOSICAO e DANO sao grandezas separadas** (licao da D14.1, na direcao
# inversa): o ramo A prova que a janela EXISTE; o ramo C conta quem SOFREU. Um
# relatorio fiel so ao ramo A publicaria um defeito que talvez nao machuque
# ninguem — e um fiel so ao C chamaria de inocua uma janela de 24 h.
#
# ⚠️ **VERDE NAO SIGNIFICA "lacuna fechada".** Ela esta aberta por decisao
# registrada. Este probe afere a LINHA DE BASE (mesmo padrao do `BASELINE_TOTAL=0`
# do gate de tasks): verde = o mundo continua como a ficha descreve. Vermelho =
# algo MUDOU e a ficha precisa ser relida — inclusive quando a mudanca e boa (a
# janela fechou). Um gate que reprovasse por a lacuna existir nasceria
# permanentemente vermelho, e gate assim ensina todo mundo a ignora-lo.
#
# ⚠️ **O pool do experimento NAO termina em `-int`, de proposito.** O ramo C conta
# a populacao de wrap-up (`%-int`), e o claim publica `conversations.routed`, que
# gera segmento: semear no mesmo sufixo contaminaria o numero que este mesmo probe
# mede. A lacuna e da familia PULL inteira (a propria ficha diz: *"aprovacao
# tambem"*), entao medi-la fora do wrap-up e mais fiel, nao menos.
#
# SAIDA: 0 = VERDE (linha de base intacta) · 1 = VERMELHO (mudou) · 2 = INCONCLUSIVO
# ==============================================================================
set -u

cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }

REDIS_C="${REDIS_C:-plughub-demo-redis-1}"
ROUTING_C="${ROUTING_C:-plughub-demo-routing-engine-1}"
CH_C="${CH_C:-plughub-demo-clickhouse-1}"
TENANT="${TENANT:-tenant_demo}"
CH_DB="${CH_DB:-plughub_demo}"

POOL=probe_pul01_pool
SID=probe-pul01-item
IA=human-probe_pul01_a
IB=human-probe_pul01_b

FALHOU=0
ok()   { printf '  \033[32mOK\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mMUDOU\033[0m %s\n' "$1"; FALHOU=1; }
nota() { printf '  \033[33m%s\033[0m %s\n' "$1" "$2"; }

R() { docker exec "$REDIS_C" redis-cli "$@" 2>/dev/null; }

# O arbitro nao expoe 3550 ao host — a chamada sai de DENTRO da rede do compose.
# Sem curl na imagem; urllib basta e evita instalar coisa no container.
arbitro() { # $1=rota  $2=json
  printf '%s' "$2" | docker exec -i "$ROUTING_C" python3 -c '
import json, sys, urllib.request, urllib.error
rota = sys.argv[1]
body = sys.stdin.read().encode()
req  = urllib.request.Request(
    "http://localhost:3550/v1/work_queue/" + rota,
    data=body, headers={"Content-Type": "application/json"})
try:
    print(urllib.request.urlopen(req, timeout=25).read().decode())
except urllib.error.HTTPError as e:
    # Um 4xx com corpo JSON E a API respondendo — devolver um envelope proprio
    # ("http_error") esconderia a resposta de quem le, e foi assim que o
    # preflight deste probe falhou na primeira execucao.
    cru = e.read().decode()
    try:    print(json.dumps({**json.loads(cru), "_status": e.code}))
    except Exception: print(json.dumps({"_status": e.code, "body": cru[:200]}))
except Exception as e:
    print(json.dumps({"erro": type(e).__name__ + ": " + str(e)}))
' "$1" 2>/dev/null
}

campo() { python3 -c 'import json,sys
try:  print(json.load(sys.stdin).get(sys.argv[1], ""))
except Exception: print("")' "$1"; }

# ⚠️ A limpeza da primeira versao deste probe era PARCIAL, e o defeito era meu:
# eu apagava so o que TINHA SEMEADO, e o `claim` publica `conversations.routed` —
# o downstream (bridge, analytics) escreve dezenas de chaves e grava sessao e
# segmento. Medido apos a primeira execucao: 22 chaves alem das minhas, 1 sessao
# e 1 segmento em ClickHouse. **Probe que suja a tabela que outros probes medem
# corrompe medicao alheia**, e o custo aparece longe, na forma de um numero que
# ninguem consegue explicar.
limpar() {
  # Redis: tudo que carregue a marca do probe, nao so o que eu escrevi.
  # SCAN (nunca KEYS): este e o mesmo Redis do routing.
  for pat in '*probe_pul01*' '*probe-pul01*'; do
    docker exec "$REDIS_C" redis-cli --scan --pattern "$pat" 2>/dev/null       | while read -r k; do [ -n "$k" ] && docker exec "$REDIS_C" redis-cli DEL "$k" >/dev/null 2>&1; done
  done
  R SREM "${TENANT}:pool:${POOL}:instances" "$IA" "$IB" > /dev/null

  # ClickHouse: mutacao ancorada em DUAS condicoes (tenant + marca do probe).
  # Uma so seria filtro largo demais para um DELETE.
  for tbl in sessions segments; do
    docker exec "$CH_C" clickhouse-client -q "
      ALTER TABLE ${CH_DB}.${tbl}
      DELETE WHERE tenant_id = '${TENANT}'
         AND (session_id LIKE 'probe-pul01%' OR pool_id = '${POOL}')" >/dev/null 2>&1
  done
}
trap limpar EXIT

# ── preflight ────────────────────────────────────────────────────────────────
docker ps --format '{{.Names}}' | grep -qx "$ROUTING_C" || {
  echo "INCONCLUSIVO: $ROUTING_C fora do ar"; exit 2; }
[ "$(arbitro holder '{}' | campo error)" = "missing_fields" ] || {
  echo "INCONCLUSIVO: a API do arbitro nao respondeu como esperado em 3550"; exit 2; }

AGORA_MS=$(( $(date -u +%s) * 1000 ))
PRAZO=$(date -u -d '+2 hours' +%Y-%m-%dT%H:%M:%S+00:00)

semear_instancia() { # $1 = instance_id
  R SET "${TENANT}:instance:$1" \
    "{\"instance_id\":\"$1\",\"agent_type_id\":\"human_agent_${POOL}\",\"user_id\":\"$1\",\"tenant_id\":\"${TENANT}\",\"pool_id\":\"${POOL}\",\"pools\":[\"${POOL}\"],\"execution_model\":\"stateful\",\"max_concurrent\":3,\"current_sessions\":0,\"status\":\"ready\",\"source\":\"human_login\"}" \
    > /dev/null
  R SADD "${TENANT}:pool:${POOL}:instances" "$1" > /dev/null
}

enfileirar() {
  R SET "${TENANT}:queue_contact:${SID}" \
    "{\"session_id\":\"${SID}\",\"tenant_id\":\"${TENANT}\",\"pool_id\":\"${POOL}\",\"channel\":\"webchat\",\"queued_at_ms\":${AGORA_MS},\"work_item_deadline\":\"${PRAZO}\",\"conference_id\":\"\"}" \
    > /dev/null
  R ZADD "${TENANT}:pool:${POOL}:queue" "$AGORA_MS" "$SID" > /dev/null
  R SET "${TENANT}:work_task:${SID}" \
    "{\"pool_id\":\"${POOL}\",\"queue_session_id\":\"${SID}\",\"resume_token\":\"probe\",\"step_id\":\"probe\",\"assigned_to\":\"\",\"deadline\":\"${PRAZO}\",\"created_at\":\"$(date -u +%Y-%m-%dT%H:%M:%S+00:00)\"}" \
    > /dev/null
}

corpo() { printf '{"tenant_id":"%s","pool_id":"%s","session_id":"%s","instance_id":"%s"}' \
  "$TENANT" "$POOL" "$SID" "$1"; }

limpar
semear_instancia "$IA"
semear_instancia "$IB"
enfileirar

# ══ A. a janela existe? ══════════════════════════════════════════════════════
printf '\n\033[1mA. a janela: item reivindicado e abandonado some da fila\033[0m\n'

RES=$(arbitro claim "$(corpo "$IA")")
[ "$(printf '%s' "$RES" | campo claimed)" = "True" ] || {
  nota "INCONCLUSIVO" "o claim inicial falhou: $RES"; exit 2; }
ok "A reivindicou o item"

NA_FILA=$(R ZSCORE "${TENANT}:pool:${POOL}:queue" "$SID")
if [ -z "$NA_FILA" ]; then
  ok "o claim tirou o item da fila (ZREM), como o codigo declara"
else
  bad "o item CONTINUA na fila apos o claim — o ZREM do passo 3 nao aconteceu"
fi

# A lease vencer e' exatamente isto: a chave deixa de existir. Apagar e esperar
# 180 s medem a mesma coisa; esperar so tornaria o probe caro.
R DEL "${TENANT}:pool:${POOL}:claim:${SID}" > /dev/null
[ -n "$(R GET "${TENANT}:pool:${POOL}:claim_record:${SID}")" ] \
  && ok "o registro duravel sobreviveu a lease (Fase A/D6)" \
  || nota "AVISO" "registro duravel ausente — o submit degradaria para permissivo"

RES=$(arbitro claim "$(corpo "$IB")")
MOTIVO=$(printf '%s' "$RES" | campo reason)
# ⚠️ MEDIDO em 2026-09-09: o motivo e `already_claimed`, nao `not_in_queue`. Eu
# esperava o segundo e o produto devolve o primeiro — o pacote do contato
# (`queue_contact`) SOBREVIVE ao claim, entao a recusa vem do ZREM do passo 3
# (nenhum vencedor), nao da leitura do passo 2.
#
# Os dois significam "B nao leva", mas NAO significam a mesma coisa para quem
# diagnostica: `already_claimed` e o motivo de quem PERDE UMA CORRIDA, e aqui nao
# ha corrida nenhuma — o dono sumiu, a lease venceu e o item nao volta. O sinal
# mais visivel da lacuna 2 se le como concorrencia saudavel. Por isso o probe
# aceita os dois e NOMEIA qual observou: trocar de um para o outro e' mudanca de
# comportamento que merece ser vista.
case "$MOTIVO" in
  not_in_queue|already_claimed)
    ok "OUTRO agente nao alcanca o item: reason=$MOTIVO  <- A JANELA"
    [ "$MOTIVO" = "already_claimed" ] && printf '        (le-se como corrida perdida, e nao ha corrida: o dono sumiu)
'
    ;;
  *)
    if [ "$(printf '%s' "$RES" | campo claimed)" = "True" ]; then
      bad "B REIVINDICOU o item — a janela pode ter FECHADO; releia a PUL-01"
    else
      nota "INCONCLUSIVO" "B falhou por motivo NOVO: $RES"; exit 2
    fi
    ;;
esac

# ── controle POSITIVO: sem ele o vermelho acima passa pelo motivo errado ──────
RES=$(arbitro release "$(corpo "$IA")")
RES=$(arbitro claim "$(corpo "$IB")")
if [ "$(printf '%s' "$RES" | campo claimed)" = "True" ]; then
  ok "controle positivo: apos o release, B reivindica — o probe CONSEGUE ver item livre"
else
  nota "INCONCLUSIVO" "controle positivo falhou ($RES): o 'not_in_queue' acima nao prova nada"
  exit 2
fi
arbitro release "$(corpo "$IB")" > /dev/null

# ══ B. alcance das redes ═════════════════════════════════════════════════════
printf '\n\033[1mB. alcance: o CrashDetector devolve o item quando a instancia morre?\033[0m\n'
printf '   (a ficha diz "nao ha reaper"; ha um detector, e a pergunta e se ele\n'
printf '    alcanca ITEM DE FILA — ele republica em conversations.inbound, e o\n'
printf '    bridge declara que item pull volta pelo work_task_release, "nunca por\n'
printf '    re-publish em conversations.inbound")\n'

enfileirar
RES=$(arbitro claim "$(corpo "$IA")")
if [ "$(printf '%s' "$RES" | campo claimed)" != "True" ]; then
  nota "INCONCLUSIVO" "nao consegui reivindicar para o teste de queda: $RES"
else
  R DEL "${TENANT}:pool:${POOL}:claim:${SID}" > /dev/null
  R DEL "${TENANT}:instance:${IA}"            > /dev/null   # a instancia "morre"
  ESPERA="${ESPERA:-40}"
  printf '   aguardando %ss (crash_check_interval_s=15)...\n' "$ESPERA"
  sleep "$ESPERA"
  VOLTOU=$(R ZSCORE "${TENANT}:pool:${POOL}:queue" "$SID")
  if [ -n "$VOLTOU" ]; then
    bad "o item VOLTOU a fila apos a queda — o alcance mudou; releia a PUL-01"
  else
    ok "o item NAO volta a fila apos a queda da instancia: a rede do CrashDetector"
    printf '        nao alcanca item de fila pull (ele republica a CONVERSA, nao o ITEM)\n'
  fi
fi

# ══ C. dano historico ════════════════════════════════════════════════════════
printf '\n\033[1mC. dano: algum item chegou ao prazo sem ninguem?\033[0m\n'
CENSO=$(docker exec "$CH_C" clickhouse-client -q "
  SELECT
    countIf(close_reason = 'acw_expired')            AS venceu,
    countIf(close_reason = 'acw_supervisor_closed')  AS supervisor,
    countIf(close_reason = 'task_submitted')         AS submetido,
    count()                                          AS total
  FROM ${CH_DB}.segments FINAL
  WHERE tenant_id = '${TENANT}' AND pool_id LIKE '%-int'
  FORMAT TabSeparated" 2>/dev/null)

if [ -z "$CENSO" ]; then
  nota "SEM AMOSTRA" "ClickHouse nao respondeu — o ramo C nao mediu nada"
else
  VENCEU=$(printf '%s' "$CENSO" | cut -f1)
  SUP=$(printf '%s'    "$CENSO" | cut -f2)
  SUB=$(printf '%s'    "$CENSO" | cut -f3)
  TOT=$(printf '%s'    "$CENSO" | cut -f4)
  printf '   populacao de wrap-up: %s itens · %s submetidos · %s encerrados pelo supervisor\n' \
    "$TOT" "$SUB" "$SUP"
  if [ "${VENCEU:-0}" -eq 0 ] 2>/dev/null; then
    ok "DANO = 0: nenhum item chegou ao prazo (a janela e' EXPOSICAO, nao perda medida)"
  else
    bad "DANO = $VENCEU item(ns) venceram sem ninguem — a lacuna deixou de ser latente"
  fi
fi

# ══ veredicto ════════════════════════════════════════════════════════════════
printf '\n'
if [ "$FALHOU" != 0 ]; then
  printf '\033[31mVERMELHO\033[0m — a linha de base MUDOU. Isto nao significa "quebrou":\n'
  printf 'pode ser a janela fechando (boa noticia) ou o dano aparecendo (ma). Releia\n'
  printf 'a PUL-01 antes de mexer em codigo.\n'
  exit 1
fi
printf '\033[32mVERDE\033[0m — linha de base intacta: a janela EXISTE (exposicao), nenhuma\n'
printf 'rede a alcanca, e o dano medido continua ZERO. A lacuna segue LATENTE.\n'
exit 0
