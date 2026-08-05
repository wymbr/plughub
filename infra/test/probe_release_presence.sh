#!/usr/bin/env bash
# probe_release_presence.sh — 2026-08-05
#
# PERGUNTA (uma só, e ela FECHA sem a tela):
#   existe alguma sessão que está NA FILA e ao mesmo tempo carrega o marcador de
#   presença de humano do bridge?
#
# Por que essa pergunta decide o achado 2 sozinha, e o probe de capacidade não
# decidia: `probe_claim_capacity_sources.sh` lê só o lado do ÁRBITRO, então o
# número que faltava (cartões na tela) era entrada humana e o veredicto tinha de
# sair INCONCLUSIVO. Aqui os DOIS lados estão no Redis:
#
#   · lado do árbitro   — ZSET `{t}:pool:{p}:queue`. O claim é um ZREM, logo
#                         "membro do ZSET" significa, por construção, SEM DONO.
#     (mesma leitura que o `work_task_holder` usa para fechar o veredicto de posse:
#      `found=false, in_queue=true` = "ninguém detém")
#   · lado do bridge    — `session:{sid}:human_agent`, escrito por
#                         `activate_human_agent` e apagado no desmonte do segmento.
#                         Presente significa "há humano anexado a esta sessão".
#
# As duas afirmações são incompatíveis. Item na fila com marcador de presença é o
# achado 2 em estado puro: foi devolvido, mas o bridge ainda acha que tem dono — e
# por isso o guard de dedup vai engolir o `conversations.routed` do próximo claim,
# gastando a vaga sem gerar cartão.
#
# ATENÇÃO AO PREFIXO: o marcador NÃO leva tenant (`session:{sid}:human_agent`, não
# `{tenant}:session:...`). Conferido no código que escreve, orchestrator-bridge
# `activate_human_agent`. Ler a chave errada devolve 0, e 0 passa por medição —
# foi assim que uma afirmação de "sessões vivas" entrou errada no handoff anterior.
#
# USO:
#   bash infra/test/probe_release_presence.sh            # todos os pools com fila
#   bash infra/test/probe_release_presence.sh formfill_demo
#
# SAÍDA: 0 = VERDE (nenhuma violação) · 1 = VERMELHO (violação) · 2 = INCONCLUSIVO.

set -u   # sem -e de propósito: ramo AUSENTE imprime INCONCLUSIVO, não morre

TENANT="${TENANT:-tenant_demo}"
COMPOSE="${COMPOSE_FILE:-docker-compose.demo.yml}"
ONLY_POOL="${1:-}"

r() { docker compose -f "$COMPOSE" exec -T redis redis-cli "$@" < /dev/null; }

echo "== probe: presença de humano × item na fila (tenant=$TENANT) =="
echo

# ── Preflight 1: PROVAR QUE O LEITOR LÊ ──────────────────────────────────────
PING="$(r PING)"
if [ "$PING" != "PONG" ]; then
  echo "PREFLIGHT FALHOU: redis-cli não respondeu PING (obtido: '$PING')."
  echo "  compose usado: $COMPOSE  (rodar da RAIZ do repo, ou COMPOSE_FILE=/caminho/…)"
  echo "VEREDICTO: INCONCLUSIVO — o leitor não lê; nada abaixo vale."
  exit 2
fi

# ── Preflight 2: PROVAR QUE O LEITOR LÊ ESTE FORMATO DE CHAVE ────────────────
# Um EXISTS contra prefixo inventado devolve 0 para sempre, e 0 é indistinguível
# de "está limpo" — que é exatamente o veredicto VERDE deste script. Então o
# leitor tem de acertar uma chave de valor CONHECIDO no mesmo formato antes de
# qualquer medição valer.
PROBE_SID="__probe_presence_$$"
r SETEX "session:${PROBE_SID}:human_agent" 30 "1" > /dev/null
SELFTEST="$(r EXISTS "session:${PROBE_SID}:human_agent")"
r DEL "session:${PROBE_SID}:human_agent" > /dev/null
if [ "$SELFTEST" != "1" ]; then
  echo "PREFLIGHT FALHOU: escrevi session:${PROBE_SID}:human_agent e o EXISTS devolveu"
  echo "  '$SELFTEST' em vez de 1 — o leitor não enxerga este formato de chave."
  echo "VEREDICTO: INCONCLUSIVO — um VERDE daqui seria cegueira, não limpeza."
  exit 2
fi
echo "preflight: redis-cli responde e enxerga session:{sid}:human_agent (self-test OK)"
echo "           (chave sintética do self-test já removida)"
echo

# ── Pools com fila ───────────────────────────────────────────────────────────
POOLS="$(r --scan --pattern "${TENANT}:pool:*:queue" \
        | sed "s|^${TENANT}:pool:||; s|:queue$||" | sort -u)"
if [ -n "$ONLY_POOL" ]; then
  POOLS="$(echo "$POOLS" | grep -x "$ONLY_POOL" || true)"
fi

if [ -z "$POOLS" ]; then
  echo "Nenhum ZSET de fila em ${TENANT}${ONLY_POOL:+ para o pool '$ONLY_POOL'}."
  echo "VEREDICTO: INCONCLUSIVO — sem item na fila não há o que confrontar."
  echo "           (rodar infra/test/smoke_formfill_renderer.sh para criar um)"
  exit 2
fi

VIOLATIONS=0
QUEUED_TOTAL=0
RETURNED_TOTAL=0   # itens que JÁ voltaram à fila — os únicos em que a pergunta morde
QUEUED_SIDS=" "    # acumulador p/ não repetir na seção de contexto (ver abaixo)

for POOL in $POOLS; do
  MEMBERS="$(r ZRANGE "${TENANT}:pool:${POOL}:queue" 0 -1)"
  echo "── pool: ${POOL}"
  if [ -z "$MEMBERS" ]; then
    echo "   fila vazia"
    echo
    continue
  fi

  while IFS= read -r SID; do
    [ -z "$SID" ] && continue
    QUEUED_TOTAL=$((QUEUED_TOTAL + 1))
    QUEUED_SIDS="${QUEUED_SIDS}${SID} "

    MARK="$(r EXISTS "session:${SID}:human_agent")"
    SETM="$(r SMEMBERS "session:${SID}:human_agents" | tr '\n' ' ' | sed 's/ *$//')"
    REC="$(r EXISTS "${TENANT}:pool:${POOL}:claim_record:${SID}")"

    # ── Item VIRGEM × item DEVOLVIDO ─────────────────────────────────────────
    # Sem esta distinção o script tem um VERDE vazio: numa fila recém-semeada
    # nenhum item jamais teve humano, então "nenhum item na fila tem presença" é
    # verdade por construção e não diz nada sobre o conserto. O discriminador já
    # existe no Redis: `first_queued` é gravado NX no primeiro enfileiramento e o
    # score do ZSET é reescrito a cada re-enfileiramento (o `add_queued_contact`
    # do `work_task_release` carimba `now`). Score > first_queued ⇒ este item já
    # voltou à fila pelo menos uma vez — que é exatamente a situação do achado 2.
    #   (que o score seja reescrito é, ele próprio, o item (a) do TODO — aqui a
    #    consequência é aproveitada como sinal, não endossada como desenho.)
    SCORE="$(r ZSCORE "${TENANT}:pool:${POOL}:queue" "$SID" | awk -F. '{print $1}')"
    FIRSTQ="$(r GET "${TENANT}:queue:first_queued:${SID}" | awk -F. '{print $1}')"
    KIND="indeterminado"
    if [ -n "$SCORE" ] && [ -n "$FIRSTQ" ]; then
      if [ "$((SCORE - FIRSTQ))" -gt 1000 ] 2>/dev/null; then
        KIND="devolvido"; RETURNED_TOTAL=$((RETURNED_TOTAL + 1))
      else
        KIND="virgem"
      fi
    fi

    if [ "$MARK" = "1" ] || [ -n "$SETM" ]; then
      VIOLATIONS=$((VIOLATIONS + 1))
      echo "   [VIOLAÇÃO] ${SID}  (${KIND})"
      echo "              na fila (sem dono, por construção do ZREM do claim),"
      echo "              mas human_agent=${MARK} e human_agents=[${SETM:-vazio}]"
      echo "              → o próximo claim desta sessão será ENGOLIDO pelo guard"
      echo "                de dedup: vaga gasta, nenhum cartão."
      # Contexto, não veredicto: registro de posse vivo junto do item na fila é
      # um segundo desacordo (o árbitro devolveu, o registro ficou) e vale citar.
      [ "$REC" = "1" ] && echo "              (obs.: claim_record TAMBÉM presente — árbitro e registro discordam)"
    else
      echo "   [ok]       ${SID}  (${KIND}) — na fila, sem presença de humano"
    fi
  done <<< "$MEMBERS"
  echo
done

# ── Contexto: presença SEM estar na fila é o estado NORMAL de item reivindicado ─
# Impresso como informação, nunca como violação — confundir os dois transformaria
# todo atendimento em curso em alarme.
#
# O filtro por `QUEUED_SIDS` NÃO é cosmético. Na primeira execução (2026-08-05) a
# sessão acusada logo acima reapareceu aqui sob o rótulo "estado normal" — a mesma
# linha dizendo violação e normalidade sobre o mesmo id. Um rótulo que descreve o
# oposto do que a medição achou é pior que rótulo nenhum: quem lesse só esta seção
# concluiria que estava tudo bem.
echo "── presença de humano fora da fila (estado normal de item reivindicado) ──"
FOUND_ANY=0
while IFS= read -r K; do
  [ -z "$K" ] && continue
  SID="${K#session:}"; SID="${SID%:human_agent}"
  case "$QUEUED_SIDS" in *" $SID "*) continue ;; esac   # já reportado na fila acima
  FOUND_ANY=1
  echo "   ${SID}  human_agents=[$(r SMEMBERS "session:${SID}:human_agents" | tr '\n' ' ' | sed 's/ *$//')]"
done <<< "$(r --scan --pattern "session:*:human_agent")"
[ "$FOUND_ANY" -eq 0 ] && echo "   (nenhuma — ou todas já listadas como item na fila acima)"
echo

echo "== veredicto =="
if [ "$QUEUED_TOTAL" -eq 0 ]; then
  echo "INCONCLUSIVO — nenhum item na fila; a pergunta não chegou a ser feita."
  exit 2
fi

if [ "$VIOLATIONS" -gt 0 ]; then
  echo "VERMELHO — ${VIOLATIONS} de ${QUEUED_TOTAL} item(ns) na fila carregam presença de humano."
  echo "Este é o achado 2. Cada id acima gastará uma vaga sem gerar cartão no re-claim."
  exit 1
fi

# VERDE sem item DEVOLVIDO é verdade por construção, não evidência: item que nunca
# teve humano obviamente não tem marcador. Sair "aprovado" aqui seria um veredicto
# que não pode reprovar o caso principal — a falha que este arquivo existe para não
# repetir. Ver CLAUDE.md § Postura de Engenharia.
if [ "$RETURNED_TOTAL" -eq 0 ]; then
  echo "INCONCLUSIVO — ${QUEUED_TOTAL} item(ns) na fila, e NENHUM deles foi devolvido"
  echo "(todos com score == first_queued, ou sem um dos dois para comparar)."
  echo
  echo "  Nenhum item na fila jamais teve humano anexado, então 'sem presença' é"
  echo "  verdade por construção e não testa o conserto. Falta o número: pelo menos"
  echo "  um item que tenha passado por 'Return to queue'."
  echo "  Rode: reivindicar no Console → 'Return to queue' → rodar este probe."
  exit 2
fi

echo "VERDE — ${QUEUED_TOTAL} item(ns) na fila (${RETURNED_TOTAL} já devolvido(s)),"
echo "        nenhum com presença de humano."
echo
echo "  O que este VERDE afirma: nenhuma sessão está simultaneamente sem dono (na"
echo "  fila) e com humano anexado (marcador) — inclusive as que voltaram por"
echo "  'Return to queue'. É a condição que o guard de dedup do bridge precisa para"
echo "  não engolir o re-claim."
echo "  O que ele NÃO afirma: que o cartão apareceu. Isso é a tela — reivindicar e"
echo "  contar, com infra/test/probe_claim_capacity_sources.sh ao lado."
