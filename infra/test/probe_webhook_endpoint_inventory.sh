#!/usr/bin/env bash
# probe_webhook_endpoint_inventory.sh — Fase A do ADR adr-webhook-endpoint-single-registry
#
# A PERGUNTA que hoje ninguém consegue responder para um tenant:
# **"quais URLs disparam alguma coisa aqui?"**
#
# O ADR decide que `ChannelEndpoint` é o registro único. Esta sonda é o pré-requisito
# das fases B–F: decidir remoção/migração antes de listar é o erro que a § Lacuna 3
# do TODO registra (item que envelheceu com o conserto errado embutido).
#
# ─── QUATRO FONTES, e o cruzamento é o produto ────────────────────────────────
#   F1  pools com `webhook` em channel_types           → acionáveis por
#                                                        /v1/channels/webhook/{webhook_skill_id}
#                                                        e /v1/channels/webhook/pool/{pool_id}
#   F2  ChannelEndpoint(channel=webhook)               → acionáveis por /channel/webhook/{identifier}
#   F3  workflow.webhooks (token plughub_wh_…)         → acionáveis por /v1/workflow/webhook/{id}
#   F4  cruzamento: quem de F1 tem linha em F2, e — o inverso, que é o achado de
#       2026-08-07 — quem de F2 aponta para pool que **não declara o canal**.
#
# ─── POR QUE F4-inverso importa tanto quanto a cobertura ──────────────────────
# `POST /v1/channel-endpoints` valida só PRESENÇA (`channel-endpoints.ts:93-95`):
# não confere se o pool existe nem se ele declara o canal. E canal é **hard filter**
# no roteamento. Logo o registro que É visível pode prometer um endpoint que o router
# recusa — o espelho exato do problema de invisibilidade. Endpoint que aparece na
# tela e não funciona é pior que endpoint que não aparece: o primeiro tem a
# aparência de conferido.
#
# ─── PREVISÕES, CONTADAS na fonte declarativa antes de rodar (método § 4) ──────
# Autor: sessão 2026-08-07. Contagem por grep em `infra/registry/tenant_demo.yaml`
# (NÃO estimativa) — o store pode divergir do YAML, e essa divergência é resultado
# legítimo, não erro da previsão.
#
#   P1  F1 = **10** pools webhook (`channel_types: [webhook]`, todos com
#       `webhook_skill_id`): gate_promocao_ia, formfill_demo_ia, wrapup_detached_ia,
#       deploy_promote_ia, outbound_demo, outbound_dispatch, outbound_worker,
#       outbound_survey_dispatch, outbound_survey_worker, portabilidade_processo_ia.
#   P2  F2 = **1** endpoint (`crm-callback` → `retencao_humano`).
#   P3  cobertura F1→F2 = **0 de 10**. Nenhum `webhook_skill_id` tem linha no
#       registro; o único identifier cadastrado não é nenhum dos dez.
#   P4  ⚠️ **1 de 1** endpoint registrado aponta para pool que NÃO declara webhook
#       (`retencao_humano` = `[webchat, whatsapp]`). Ou seja: 100% do que está
#       visível é suspeito de não funcionar.
#
#   Se P1/P2 divergirem, o STORE divergiu do YAML — e aí o achado é o drift, não a
#   previsão (mesma família do `config-seed` que aplicou `survey.link_delivery`
#   meses depois).
#
# ─── PREVISÕES DEPOIS DA FASE B (seed idempotente, registradas 2026-08-07) ─────
#   P5  F2 = **11** (1 externo + 10 internos declarados em tenant_demo.yaml).
#   P6  cobertura F1→F2 = **0 sem registro** (era 10 de 10).
#   P7  F4c = **0 com origin errada** — as 10 saem carimbadas `internal`.
#   P8  avisos = **1** (`crm-callback`, higiene) · superfícies = **21** · **exit 0**.
#
#   As 10 linhas são DECLARADAS no YAML, não derivadas do pool pelo syncer. A razão
#   está no ADR e no próprio guard `_validate_webhook_endpoints`: derivar tornaria
#   este probe um teste que não pode reprovar, porque a linha concordaria com o pool
#   por ter sido copiada dele.
#
# Uso:  bash infra/test/probe_webhook_endpoint_inventory.sh [tenant]
# Pré:  agent-registry (3300), postgres. channel-gateway (8010) é OPCIONAL — sem
#       ela o teste de resolução ao vivo não roda, e o script DIZ que não rodou.
# Saída: 0 = inventário completo (tudo registrado e válido) · 1 = mediu e há
#        lacuna · 2 = INCONCLUSIVO (não mediu).

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${1:-tenant_demo}"
REG="http://localhost:3300"
DB="plughub_demo"

psqlq() { $DC exec -T postgres psql -U plughub -d "$DB" -tAc "$1" < /dev/null 2>/dev/null; }
num()   { tr -d '\r\n[:space:]' <<<"${1:-}"; }

# O título já disse "Fase A" por duas fases além dela. O script cresceu: hoje mede o
# inventário (A), a cobertura do seed (B/F4a-c) e a cobertura da tela (D/F5).
echo "══ inventário de webhook (ADR registro único · Fases A/B/D) — tenant=$TENANT ══"
echo

# ── PREFLIGHT — distingue SEM-CONEXÃO de HTTP-erro (lição de 2026-08-07) ──────
POOLS_CODE=$(curl -s -o /tmp/_pools.json -w '%{http_code}' -m 10 \
  "$REG/v1/pools" -H "x-tenant-id: $TENANT" 2>/dev/null)
case "$POOLS_CODE" in
  2*) : ;;
  000) echo "⚠️  INCONCLUSIVO — agent-registry(3300) SEM CONEXÃO."; exit 2 ;;
  *)   echo "⚠️  INCONCLUSIVO — agent-registry devolveu HTTP $POOLS_CODE."; exit 2 ;;
esac

# ── F1 · pools webhook ────────────────────────────────────────────────────────
# O JSON pode vir array puro ou {pools:[…]}; normaliza antes de contar.
POOLS=$(jq -c 'if type=="array" then . else (.pools // []) end' /tmp/_pools.json 2>/dev/null)
F1=$(jq -r '[.[] | select(.channel_types // [] | index("webhook"))] | length' <<<"${POOLS:-[]}")
F1=$(num "${F1:-0}")

echo "── F1 · pools com canal webhook: ${F1}   (P1 previu 10) ──────────────────"
jq -r '.[] | select(.channel_types // [] | index("webhook"))
       | "   \(.pool_id)\twebhook_skill_id=\(.webhook_skill_id // "«VAZIO»")"' <<<"${POOLS:-[]}"
echo

if [ "${F1:-0}" -eq 0 ]; then
  echo "⚠️  INCONCLUSIVO — ZERO pools webhook. Isto é ausência de amostra (ou o"
  echo "    filtro de channel_types mudou), não 'não há endpoints internos'."
  exit 2
fi

# ── F2 · ChannelEndpoint de canal webhook ─────────────────────────────────────
EP=$(curl -sf -m 10 "$REG/v1/channel-endpoints?channel=webhook" \
      -H "x-tenant-id: $TENANT" 2>/dev/null \
     | jq -c 'if type=="array" then . else (.endpoints // []) end' 2>/dev/null)
F2=$(num "$(jq -r 'length' <<<"${EP:-[]}")")

echo "── F2 · ChannelEndpoint(webhook): ${F2}   (P2 previu 1; Fase B previu 11) ─"
jq -r '.[] | "   \(.identifier)\t→ \(.pool_id)\tactive=\(.active)\torigin=\(.origin // "«AUSENTE»")\tauth=\(if .auth_required then "token" else "ANÔNIMO" end)"' <<<"${EP:-[]}"
echo

# ── F3 · legado por token ─────────────────────────────────────────────────────
F3=$(num "$(psqlq "SELECT count(*) FROM workflow.webhooks WHERE tenant_id='$TENANT';")")
echo "── F3 · workflow.webhooks (token): ${F3:-?} ──────────────────────────────"
[ "${F3:-0}" -gt 0 ] && psqlq "
  SELECT '   ' || flow_id || E'\t' || active FROM workflow.webhooks
   WHERE tenant_id='$TENANT' ORDER BY flow_id;"
echo

# ── F4a · cobertura: cada endereço interno tem linha no registro? ─────────────
echo "── F4a · endereços internos SEM registro ─────────────────────────────────"
UNCOVERED=0
while IFS= read -r WSID; do
  [ -z "$WSID" ] || [ "$WSID" = "null" ] && continue
  HIT=$(jq -r --arg i "$WSID" '[.[] | select(.identifier==$i)] | length' <<<"${EP:-[]}")
  if [ "$(num "${HIT:-0}")" -eq 0 ]; then
    UNCOVERED=$(( UNCOVERED + 1 ))
    echo "   ❌ $WSID"
  fi
done <<< "$(jq -r '.[] | select(.channel_types // [] | index("webhook")) | .webhook_skill_id // empty' <<<"${POOLS:-[]}")"
[ "$UNCOVERED" -eq 0 ] && echo "   (nenhum — todos registrados)"
echo

# ── F4b · endpoint registrado aponta para pool EXISTENTE? ─────────────────────
#
# ⚠️ CORRIGIDO 2026-08-07, MESMO DIA: a v1 reprovava (❌, exit 1) endpoint cujo pool
# não declarasse `webhook` em `channel_types`, sob a premissa "canal é hard filter".
# **Falso positivo, provado ao vivo:** `crm-callback` → `retencao_humano`
# (`[webchat, whatsapp]`) recebeu o disparo, foi entregue a um agente humano,
# atendido e encerrado. `router.py:86-92` explica: com `pool_id` explícito,
# `pools = [pool]` SEM filtro de canal — o filtro vive só no ramo legado de
# DESCOBERTA (`:94`). Canal é hard filter sobre descobrir pool, não sobre pool
# endereçado, e um ChannelEndpoint é precisamente um endereçamento.
#
# Portanto: **pool inexistente REPROVA** (não há defesa); **canal não declarado é
# AVISO** — é higiene (o `channel_types` alimenta descoberta e Monitor), não falha.
# Um portão que reprova configuração que funciona é pior que portão nenhum: ensina
# a ignorar o vermelho.
echo "── F4b · endpoints registrados: pool existe? canal declarado? ────────────"
INVALID=0   # reprova
ADVISORY=0  # só avisa
while IFS=$'\t' read -r IDENT POOL; do
  [ -z "$IDENT" ] && continue
  OK=$(jq -r --arg p "$POOL" \
        '[.[] | select(.pool_id==$p) | select(.channel_types // [] | index("webhook"))] | length' \
        <<<"${POOLS:-[]}")
  EXISTS=$(jq -r --arg p "$POOL" '[.[] | select(.pool_id==$p)] | length' <<<"${POOLS:-[]}")
  if [ "$(num "${EXISTS:-0}")" -eq 0 ]; then
    INVALID=$(( INVALID + 1 )); echo "   ❌ $IDENT → pool '$POOL' NÃO EXISTE"
  elif [ "$(num "${OK:-0}")" -eq 0 ]; then
    ADVISORY=$(( ADVISORY + 1 ))
    echo "   ⚠️  $IDENT → pool '$POOL' não declara 'webhook' em channel_types"
    echo "      FUNCIONA (pool endereçado não passa pelo filtro), mas diverge do"
    echo "      que o Monitor exibe e do que a descoberta usaria. Higiene."
  fi
done <<< "$(jq -r '.[] | "\(.identifier)\t\(.pool_id)"' <<<"${EP:-[]}")"
[ "$INVALID" -eq 0 ] && [ "$ADVISORY" -eq 0 ] && echo "   (nenhum — todos consistentes)"
echo

# ── F4c · a linha interna diz que é interna? (Fase B) ─────────────────────────
#
# POR QUE ESTA CHECAGEM EXISTE. "Sem registro: 0" é um valor PLAUSÍVEL que pode ser
# obtido de um jeito errado: se a coluna `origin` não tiver sido aplicada (migration
# não rodada, agent-registry não rebuildado, campo ignorado no POST), a linha ainda
# nasce — só que carimbada `external`, ou sem campo nenhum. A cobertura ficaria VERDE
# descrevendo endereço interno como cadastro de tenant, e a Fase D (read-only na tela)
# herdaria o erro sem nada ter ficado vermelho. Aqui a asserção é sobre o que a Fase B
# efetivamente entrega, não sobre a linha existir.
#
# Regra: identifier que casa um `webhook_skill_id` de pool DEVE ter origin=internal.
# `origin` ausente no JSON ⇒ REPROVA (coluna não aplicada — não é o mesmo que 'external').
echo "── F4c · procedência das linhas internas (origin=internal) ───────────────"
#
# ⚠️ CONTADOR-TESTEMUNHA (`CHECKED`), acrescentado 2026-08-07 na PRIMEIRA execução:
# a v1 deste bloco imprimiu **"(todas carimbadas internal)"** num store sem NENHUMA
# linha interna. O laço pulava (`continue`) cada endereço sem linha — corretamente,
# para não contar a mesma lacuna duas vezes — e caía no `[ BADORIGIN -eq 0 ]`, que
# não distingue *"checei 10 e todas passaram"* de *"não checei nada"*. Zero defeitos
# sobre zero amostras é um valor PLAUSÍVEL, e por isso passou — dentro do teste
# escrito justamente para impedir essa classe de verde. Sem amostra o bloco agora se
# declara INCONCLUSIVO, e nunca aprovado.
BADORIGIN=0
CHECKED=0
while IFS= read -r WSID; do
  [ -z "$WSID" ] || [ "$WSID" = "null" ] && continue
  ORG=$(jq -r --arg i "$WSID" '[.[] | select(.identifier==$i) | .origin // "«AUSENTE»"] | first // empty' <<<"${EP:-[]}")
  [ -z "$ORG" ] && continue          # sem linha — já contado em F4a, não conta duas vezes
  CHECKED=$(( CHECKED + 1 ))
  if [ "$ORG" != "internal" ]; then
    BADORIGIN=$(( BADORIGIN + 1 ))
    echo "   ❌ $WSID → origin=$ORG (esperado 'internal')"
    [ "$ORG" = "«AUSENTE»" ] && \
      echo "      campo AUSENTE na resposta ⇒ migration/rebuild do agent-registry não aplicado."
  fi
done <<< "$(jq -r '.[] | select(.channel_types // [] | index("webhook")) | .webhook_skill_id // empty' <<<"${POOLS:-[]}")"
if [ "$CHECKED" -eq 0 ]; then
  echo "   ⚠️  NÃO CHECADO — nenhum endereço interno tem linha no registro (ver F4a)."
  echo "      Isto NÃO é 'procedência ok': é ausência de amostra."
elif [ "$BADORIGIN" -eq 0 ]; then
  echo "   (${CHECKED} checada(s) — todas carimbadas internal)"
fi
echo

# ── F5 · cobertura da TELA (Fase D) ───────────────────────────────────────────
#
# O critério de reprovação da Fase D é *"endpoint acionável ausente da tela"*. A
# tela (`/config/channels` › Webhook) consome DUAS fontes:
#   · os `ChannelEndpoint(webhook)` — a tabela (F2);
#   · os pools webhook — a seção "endereço implícito de cada pool" (F1), que existe
#     justamente porque `/v1/channels/webhook/pool/{id}` é acionável e NÃO tem linha
#     (registrá-lo seria a função identidade do pool: um inventário incapaz de
#     discordar da fonte).
#
# Some F1 ∪ F2 e sobra exatamente UMA família acionável fora da tela: o registro
# LEGADO por token (`workflow.webhooks`, F3), que vive noutra tabela e tem editor
# numa rota fora do menu. Enquanto ele tiver linhas, a Fase D está incompleta por
# construção — e o conserto é a Fase F (migrar ou aposentar), não maquiagem aqui.
echo "── F5 · cobertura da tela (Fase D) ───────────────────────────────────────"
echo "   na tela: ${F2} registrado(s) + ${F1} endereço(s) por pool"
if [ "${F3:-0}" -gt 0 ]; then
  echo "   ❌ ${F3} webhook(s) por TOKEN acionáveis e FORA da tela (workflow.webhooks)"
  echo "      A Fase D não fecha com eles vivos — decidir na Fase F: migrar p/ o"
  echo "      registro ou aposentar junto com o editor órfão de /workflow/calendar."
else
  echo "   (nenhum acionável fora da tela — F3=0)"
fi
echo

# ── F6 · endpoints ANÔNIMOS (arco webhook-endpoint-auth) ──────────────────────
#
# `auth_required` nasce false, para não converter todo endpoint em uso num 401
# retroativo. O risco conhecido do opt-in é virar proteção que ninguém liga — e o
# antídoto escolhido NÃO é o default agressivo, é esta contagem: a ausência de
# credencial deixa de ser subentendida e passa a aparecer, com nome, toda vez que
# alguém pergunta pelo inventário.
#
# **NÃO REPROVA de propósito.** Um endpoint anônimo pode ser uma escolha legítima
# (ambiente fechado, endereço só alcançável de dentro). Reprovar aqui obrigaria a
# desligar o gate, e gate desligado não mede nada. O que ele não pode é ficar calado.
echo "── F6 · autenticação dos endpoints registrados ───────────────────────────"
ANON=$(jq -r '[.[] | select((.auth_required // false) | not)] | length' <<<"${EP:-[]}")
ANON=$(num "${ANON:-0}")
AUTHED=$(( F2 - ANON ))
echo "   com token: ${AUTHED}    ANÔNIMOS: ${ANON}   de ${F2}"
if [ "${ANON:-0}" -gt 0 ]; then
  jq -r '.[] | select((.auth_required // false) | not) | "   ⚠️  \(.identifier)\t(origin=\(.origin // "external"))"' <<<"${EP:-[]}"
  echo "      Acionáveis por qualquer um que alcance o gateway. Para exigir credencial:"
  echo "      POST /v1/channel-endpoints/{id}/token  (devolve o token UMA vez)."
  echo "      Os endereços por pool (F5) NÃO passam pelo registro e seguem anônimos"
  echo "      por construção — ver ADR §7.6.1."
fi
echo

# ── Veredicto ─────────────────────────────────────────────────────────────────
TOTAL=$(( F1 + F2 + ${F3:-0} ))
echo "══ veredicto ══════════════════════════════════════════════════════════════"
echo "superfícies acionáveis: ${TOTAL}   (F1=${F1} interno · F2=${F2} registrado · F3=${F3:-?} token)"
echo "sem registro: ${UNCOVERED}    pool inexistente: ${INVALID}    avisos: ${ADVISORY}"
echo "procedência: ${BADORIGIN} errada(s) de ${CHECKED} checada(s)$([ "$CHECKED" -eq 0 ] && echo '  ← sem amostra, não é aprovação')"
echo "autenticação: ${ANON} anônimo(s) de ${F2} registrado(s)   (informativo — não reprova)"
echo
if [ "$UNCOVERED" -eq 0 ] && [ "$INVALID" -eq 0 ] && [ "$BADORIGIN" -eq 0 ] && [ "${F3:-0}" -eq 0 ]; then
  echo "✅ inventário COMPLETO — nada a semear (B) e nada acionável fora da tela (D)."
  [ "$ADVISORY" -gt 0 ] && \
    echo "   (${ADVISORY} aviso(s) de higiene acima — NÃO reprovam: a configuração funciona.)"
  exit 0
fi
echo "❌ há lacuna. Próximo passo = fase B do ADR (seed idempotente), e a ORDEM"
echo "   é decisão (D7): semear ANTES de trocar a resolução, remover o fallback"
echo "   POR ÚLTIMO — invertido, todo disparo interno vira 404 mudo."
[ "$INVALID" -gt 0 ] && \
  echo "   E ${INVALID} endpoint(s) aponta(m) para pool INEXISTENTE — conserto de dado" && \
  echo "   + guard no create (D8): validar existência do pool (o canal é só aviso)."
[ "$BADORIGIN" -gt 0 ] && \
  echo "   E ${BADORIGIN} linha(s) interna(s) sem origin='internal' — a linha existe mas" && \
  echo "   MENTE sobre a própria procedência. Ordem de suspeita: migration não aplicada →" && \
  echo "   agent-registry não rebuildado → 'origin' faltando na entrada do YAML."
exit 1
