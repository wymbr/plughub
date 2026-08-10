#!/usr/bin/env bash
# wait_registry_converged.sh — bloqueia até o provisionamento do registro terminar.
#
# A PERGUNTA: *"o RegistrySyncer já rodou?"* — que hoje não tem resposta em lugar nenhum.
#
# ─── POR QUE ISTO EXISTE ──────────────────────────────────────────────────────
# O `orchestrator-bridge` é o ÚLTIMO a convergir: espera agent-registry e
# skill-flow-service ficarem healthy e só então sincroniza skills → pools →
# channel_endpoints. Ele **não tem healthcheck**, então `docker compose ps` não sabe
# dizer se o provisionamento acabou — e `rebuild-all.sh` termina em `up -d` imprimindo
# "Acompanhe a convergência", ou seja, delegando isso ao olho humano.
#
# O mesmo erro já custou três sessões, sempre com número PLAUSÍVEL:
#   · ADR §7.4 — `F2=1` lido antes do seed. É exatamente o valor pré-Fase-B, então
#     pareceu "o seed não aplicou" em vez de "medi cedo demais".
#   · `up -d <serviço>` sobe só o subgrafo de dependências; o resto da stack fica fora
#     e os gates saem INCONCLUSIVO logo após um rebuild, parecendo culpa da mudança.
#   · 2026-08-10 — bateria inteira INCONCLUSIVO após `--wipe`, bridge em "Up Less than
#     a second".
# *Um comando que retorna não é uma ação concluída.* Este script é a prova que faltava.
#
# ─── O CRITÉRIO É QUIESCÊNCIA, NÃO CONTAGEM FIXA ──────────────────────────────
# Poderia esperar "10 pools e 11 endpoints". Não espera, por duas razões:
#
#   1. Isso faria do helper um teste do tenant demo — envelheceria no primeiro pool
#      novo, e o modo de falha seria um timeout enganoso ("não convergiu") sobre uma
#      stack perfeitamente saudável.
#   2. **Prontidão não é a mesma pergunta que inventário.** Se este script julgasse
#      conteúdo, duplicaria o `probe_webhook_endpoint_inventory.sh` — e duas fontes
#      para o mesmo veredicto divergem, que é o defeito que o próprio ADR combate.
#      Aqui responde-se "o escritor parou de escrever"; se o que ele escreveu está
#      certo é do probe.
#
# Convergiu ⇔ duas amostras consecutivas IGUAIS e **> 0**. O `> 0` é o que impede o
# caso degenerado: registro vazio também é "estável", e sem essa condição o script
# aprovaria na primeira medição de uma base que nunca foi semeada.
#
# ⚠️ LIMITAÇÃO ASSUMIDA: se o syncer pausar por mais que o intervalo entre amostras,
# duas leituras podem cair dentro da pausa e o script declara convergência cedo. Por
# isso o intervalo é 3 s (as 13 escritas do demo saem em ~90 ms) e existe `STABLE_N`
# para exigir mais amostras onde isso importar. Não é impossível de enganar — é caro
# o suficiente para não acontecer por acidente, e a alternativa (um sinal explícito de
# fim publicado pelo bridge) é mudança no bridge, não neste script.
#
# ─── EXIGIR NÚMERO EXATO, quando o chamador o conhece ─────────────────────────
# `EXPECT_WEBHOOK_POOLS` / `EXPECT_WEBHOOK_ENDPOINTS` transformam o helper em asserção:
# convergiu num valor diferente ⇒ exit 1 (mediu e reprovou), distinto de exit 2 (não
# mediu). Sem elas, o script NÃO opina sobre conteúdo — e diz isso na saída, em vez de
# deixar o silêncio parecer aprovação.
#
# Uso:   bash infra/test/wait_registry_converged.sh [tenant]
# Env:   REG=http://localhost:3300   TIMEOUT_S=180   INTERVAL_S=3   STABLE_N=2
#        EXPECT_WEBHOOK_POOLS=10     EXPECT_WEBHOOK_ENDPOINTS=11
# Saída: 0 = convergiu (e bateu o EXPECT, se houver)
#        1 = convergiu num valor DIFERENTE do EXPECT (mediu e reprovou)
#        2 = INCONCLUSIVO — registro inalcançável, ou não convergiu no timeout

set -uo pipefail

TENANT="${1:-tenant_demo}"
REG="${REG:-http://localhost:3300}"
TIMEOUT_S="${TIMEOUT_S:-180}"
INTERVAL_S="${INTERVAL_S:-3}"
STABLE_N="${STABLE_N:-2}"

echo "══ aguardando convergência do registro — tenant=$TENANT ══"
echo "   critério: ${STABLE_N} amostras iguais e > 0 · intervalo ${INTERVAL_S}s · timeout ${TIMEOUT_S}s"

_pools() {
  curl -s -m 5 "$REG/v1/pools" -H "x-tenant-id: $TENANT" 2>/dev/null \
    | jq -r 'if type=="array" then . else (.pools // []) end
             | [.[] | select(.channel_types // [] | index("webhook"))] | length' 2>/dev/null
}
_endpoints() {
  curl -s -m 5 "$REG/v1/channel-endpoints?channel=webhook" -H "x-tenant-id: $TENANT" 2>/dev/null \
    | jq -r 'if type=="array" then . else (.endpoints // []) end | length' 2>/dev/null
}

DEADLINE=$(( $(date +%s) + TIMEOUT_S ))
PREV=""            # amostra anterior, no formato "P/E"
STABLE=0           # quantas amostras consecutivas iguais já vimos
REACHED=0          # o registro chegou a responder alguma vez? (distingue 000 de vazio)
P=""; E=""

while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  P=$(_pools); E=$(_endpoints)

  # Resposta vazia/não-numérica = registro fora do ar ou devolvendo lixo. NÃO conta
  # como amostra: tratá-la como "0" faria duas indisponibilidades seguidas parecerem
  # estabilidade — dois erros iguais entre si, que é o falso verde do preflight.
  if ! [[ "$P" =~ ^[0-9]+$ ]] || ! [[ "$E" =~ ^[0-9]+$ ]]; then
    printf '   registro sem resposta…\n'
    PREV=""; STABLE=0
    sleep "$INTERVAL_S"; continue
  fi
  REACHED=1

  CUR="$P/$E"
  if [ "$CUR" = "$PREV" ] && [ "$P" -gt 0 ] && [ "$E" -gt 0 ]; then
    STABLE=$(( STABLE + 1 ))
  else
    STABLE=1
  fi
  printf '   pools webhook=%-3s endpoints=%-3s  (estável %d/%d)\n' "$P" "$E" "$STABLE" "$STABLE_N"
  PREV="$CUR"

  if [ "$STABLE" -ge "$STABLE_N" ] && [ "$P" -gt 0 ] && [ "$E" -gt 0 ]; then
    echo "✅ convergiu — pools webhook=$P · endpoints webhook=$E"

    FAIL=0
    if [ -n "${EXPECT_WEBHOOK_POOLS:-}" ] && [ "$P" != "$EXPECT_WEBHOOK_POOLS" ]; then
      echo "❌ pools webhook = $P, esperado $EXPECT_WEBHOOK_POOLS"; FAIL=1
    fi
    if [ -n "${EXPECT_WEBHOOK_ENDPOINTS:-}" ] && [ "$E" != "$EXPECT_WEBHOOK_ENDPOINTS" ]; then
      echo "❌ endpoints webhook = $E, esperado $EXPECT_WEBHOOK_ENDPOINTS"
      echo "   Se faltar 1 e o ausente for um endpoint EXTERNO declarado no YAML, a"
      echo "   suspeita nº1 é o 422 da §7.10: entrada sem \`auth_required\` explícito."
      echo "   Confira:  docker compose -f docker-compose.demo.yml logs orchestrator-bridge | grep -i channel_endpoint"
      FAIL=1
    fi
    [ "$FAIL" -eq 1 ] && exit 1

    if [ -z "${EXPECT_WEBHOOK_POOLS:-}${EXPECT_WEBHOOK_ENDPOINTS:-}" ]; then
      echo "   (sem EXPECT_* — este script NÃO julgou o conteúdo, só a quiescência."
      echo "    O inventário é do probe_webhook_endpoint_inventory.sh.)"
    fi
    exit 0
  fi

  sleep "$INTERVAL_S"
done

echo
if [ "$REACHED" -eq 0 ]; then
  echo "⚠️  INCONCLUSIVO — o agent-registry ($REG) nunca respondeu em ${TIMEOUT_S}s."
  echo "    Não é 'não convergiu': é não ter medido. Confira se ele está no ar."
elif [ "${P:-0}" -eq 0 ] || [ "${E:-0}" -eq 0 ]; then
  echo "⚠️  INCONCLUSIVO — registro responde, mas segue VAZIO após ${TIMEOUT_S}s"
  echo "    (pools=$P endpoints=$E). O syncer não escreveu nada. Ordem de suspeita:"
  echo "      1. o bridge não subiu ou morreu no boot   → logs orchestrator-bridge"
  echo "      2. ele espera dependência que não ficou healthy (agent-registry,"
  echo "         skill-flow-service) → docker compose ps"
  echo "      3. credencial de serviço ausente → POSTs voltando 401/403 no log"
else
  echo "⚠️  INCONCLUSIVO — não estabilizou em ${TIMEOUT_S}s (última: pools=$P endpoints=$E)."
  echo "    O registro está sendo escrito e não parou. Ou o syncer está lento, ou algo"
  echo "    reescreve em laço — o log do bridge distingue os dois."
fi
exit 2
