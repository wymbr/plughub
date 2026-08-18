#!/usr/bin/env bash
# apply_queue_pool_address.sh — P2/F3: aponta o `queue_config` de um pool de destino
# para o POOL de fila que tem o deploy.
#
# POR QUE UM SCRIPT E NÃO O YAML. `infra/registry/*.yaml` é seed-if-absent: editar o
# bloco `queue_config` de um pool que JÁ existe no DB é no-op, e o modo de falha é o
# pior possível — sucesso pelo caminho antigo (o boot loga DRIFT, a tela segue mostrando
# o valor velho, e quem editou jura ter aplicado). O YAML foi editado do mesmo jeito,
# porque é ele que vale em instalação limpa; este script é o aplicador do DB.
#
#   bash infra/scripts/apply_queue_pool_address.sh retencao_humano fila_humano
#
# Lê de volta e IMPRIME o estado resultante: aplicar sem conferir seria trocar uma
# config decorativa por outra.
set -uo pipefail

DEST="${1:?uso: apply_queue_pool_address.sh <pool_destino> <pool_fila> [max_wait_s]}"
FILA="${2:?falta o pool de fila}"
MAXW="${3:-1800}"
AR="${AGENT_REGISTRY_URL:-http://localhost:3300}"
TENANT="${TENANT:-tenant_demo}"
SVC="${AGENT_REGISTRY_SERVICE_TOKEN:-changeme_agent_registry_service_token_demo}"

H=(-H "x-tenant-id: $TENANT" -H "x-service-token: $SVC" -H 'content-type: application/json')

echo "══ F3 — endereço do agente de fila: $DEST → $FILA (tenant=$TENANT) ══"

# ── 0. PREFLIGHT: o pool de fila TEM slot `current`? ─────────────────────────
# Sem slot promovido o agente de fila não roda (F1 recusa antes do segmento), e o
# endereço novo seria tão decorativo quanto o antigo. Falhar aqui move o erro para
# onde ele tem resposta.
SLOTS=$(curl -s "$AR/v1/pools/$FILA/slots" "${H[@]}")
case "$SLOTS" in
  *'"current"'*) : ;;
  *) echo "⛔ INCONCLUSIVO: '$FILA' não devolveu slots (resposta: ${SLOTS:0:200})"; exit 2 ;;
esac
if ! printf '%s' "$SLOTS" | grep -q '"skill_id"'; then
  echo "❌ '$FILA' não tem skill no slot \`current\` — promova antes:"
  echo "   bash infra/scripts/deploy_skill_to_slot.sh <skill.yaml> $FILA"
  exit 1
fi
echo "   preflight: '$FILA' tem slot com skill — ok"

# ── 1. PUT preservando o endereço legado ─────────────────────────────────────
# `agent_type_id`/`skill_id` viajam junto de propósito: são retrocompat e, no ramo
# sem `pool_id`, ainda são a identidade do segmento. Omiti-los aqui apagaria config
# em silêncio (o PUT é parcial no POOL, não dentro do `queue_config`).
BODY=$(printf '{"queue_config":{"pool_id":"%s","agent_type_id":"agente_fila_v1","skill_id":"skill_fila_v1","max_wait_s":%s}}' \
       "$FILA" "$MAXW")
CODE=$(curl -s -o /tmp/f3_put.json -w '%{http_code}' -X PUT "$AR/v1/pools/$DEST" "${H[@]}" -d "$BODY")
echo "   PUT /v1/pools/$DEST → HTTP $CODE"
[ "$CODE" = "200" ] || { echo "❌ falhou: $(head -c 300 /tmp/f3_put.json)"; exit 1; }

# ── 2. LEITURA DE VOLTA — "foi escrito" ≠ "mudou" ────────────────────────────
echo "   estado resultante:"
curl -s "$AR/v1/pools/$DEST" "${H[@]}" \
  | tr ',' '\n' | grep -A0 -E 'pool_id|skill_id|agent_type_id|max_wait_s' | sed 's/^/     /'
echo
echo '   ⚠️  O bridge cacheia flow por pool (_pool_flow_cache); o registry.changed do PUT'
echo '      invalida. Se a reprodução usar sessão já em voo, reinicie o bridge.'
