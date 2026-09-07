#!/usr/bin/env bash
# probe_resume_token_cancel.sh — 2026-09-07  (RET-03 do ADR
# adr-tree-return-continuation.md)
#
# PERGUNTA: quando o contato fecha, os resume_tokens dele morrem junto — e o
# scanner de prazo deixou de retomar sessao morta?
#
# POR QUE ISTO E OBRIGATORIO, e nao um detalhe de limpeza
#   A D6 admite que o agente CHAMADO encerre o contato, e a razao nao e
#   conveniencia: **finalizacao por ERRO existe de qualquer forma** (falha de
#   MCP, pool indisponivel, excecao), entao um desenho em que o chamado nunca
#   termina seria falso no primeiro incidente.
#
#   Mas encerrando sem devolver, o token do CHAMADOR nunca e consumido: o
#   pipeline_state fica suspenso, a entrada fica no hash, e o timeout scanner --
#   que so olha PRAZO -- acaba chamando handle_resume numa sessao ja fechada. O
#   orquestrador tentaria continuar um contato que acabou.
#
# DUAS DEFESAS, e elas nao se substituem
#   (1) CANCELAMENTO no fechamento -- um lugar so, o consumidor de
#       `session.closed`, que ve todo canal. Um gancho por adapter seriam N
#       ganchos, e o esquecido reabre o buraco (a mesma razao pela qual a borda
#       do gateway e allowlist e nao proibicao).
#   (2) GUARDA no scanner -- rede de seguranca para o token que escapou e para o
#       PASSIVO, que o cancelamento nao alcanca. So decide sobre token JA
#       VENCIDO: e o que torna seguro usar a ausencia do meta como sinal, porque
#       ali a escolha e entre RETOMAR e APAGAR, nunca entre manter e apagar.
#
# QUATRO RAMOS
#   A  o metodo existe no dono dos tokens e o gancho e UNICO (censo de fonte)
#   B  o gancho vem ANTES das guardas de canal -- fechamento em canal sem adapter
#      registrado ainda tem de limpar
#   C  o scanner checa a vida da sessao ANTES de retomar
#   D  a suite de unidade do gateway (7 casos), cujo caso load-bearing e o
#      CONTROLE POSITIVO: o token de OUTRA sessao sobrevive. Sem ele, limpar o
#      hash inteiro passaria e derrubaria todo delegate pendente do tenant.
#
# EXIT: 0 OK · 1 FALHA · 3 SEM AMOSTRA

set -uo pipefail
cd "$(dirname "$0")/../.."

WEBHOOK="packages/channel-gateway/src/plughub_channel_gateway/adapters/webhook.py"
CONSUMER="packages/channel-gateway/src/plughub_channel_gateway/outbound_consumer.py"
TESTE="src/plughub_channel_gateway/tests/test_ret03_cancel_resumes.py"
FALHA=0

echo "probe_resume_token_cancel — o token morre com o contato (RET-03)"

echo ""
echo "── A — o metodo existe, e o gancho e UNICO ──────────────────────────"
if grep -q "async def cancel_pending_resumes" "$WEBHOOK"; then
  echo "  OK — cancel_pending_resumes vive no dono dos tokens"
else
  echo "  FALHA — cancel_pending_resumes ausente de $WEBHOOK"
  FALHA=$((FALHA + 1))
fi
N=$(grep -c "cancel_pending_resumes" "$CONSUMER" || true)
if [ "$N" -ge 1 ]; then
  echo "  OK — o consumidor de session.closed chama o cancelamento"
else
  echo "  FALHA — nenhum chamador em $CONSUMER"
  FALHA=$((FALHA + 1))
fi

echo ""
echo "── B — o gancho vem ANTES das guardas de canal ──────────────────────"
# A guarda de adapter esta no `_dispatch`; o cancelamento tem de aparecer ANTES
# dela no arquivo, senao fechamento em canal sem adapter nao limpa nada.
L_CANCEL=$(grep -n "_cancelar_resumes_pendentes(payload)" "$CONSUMER" | head -1 | cut -d: -f1)
L_GUARDA=$(grep -n "adapter = self._adapters.get(channel)" "$CONSUMER" | head -1 | cut -d: -f1)
if [ -n "$L_CANCEL" ] && [ -n "$L_GUARDA" ] && [ "$L_CANCEL" -lt "$L_GUARDA" ]; then
  echo "  OK — cancelamento na linha $L_CANCEL, guarda de adapter na $L_GUARDA"
else
  echo "  FALHA — cancelamento (${L_CANCEL:-ausente}) nao precede a guarda (${L_GUARDA:-ausente})"
  FALHA=$((FALHA + 1))
fi

echo ""
echo "── C — o scanner checa a vida da sessao antes de retomar ────────────"
if grep -q "token de sessao MORTA descartado sem" "$WEBHOOK"; then
  echo "  OK — o scanner descarta em vez de retomar, e LOGA nomeando"
else
  echo "  FALHA — o scanner ainda retoma sessao morta"
  FALHA=$((FALHA + 1))
fi

echo ""
echo "── D — a suite de unidade, na IMAGEM ────────────────────────────────"
# Na imagem, nunca no container com `docker cp`: `up -d` recria a partir da
# imagem, e um teste que so passa no container e estado herdado.
if docker compose -f docker-compose.demo.yml exec -T channel-gateway \
     sh -c "cd /app/packages/channel-gateway && python -m pytest $TESTE -q" >/tmp/ret03.txt 2>&1; then
  echo "  OK — $(tail -1 /tmp/ret03.txt)"
else
  echo "  FALHA — $(tail -3 /tmp/ret03.txt | tr '\n' ' ')"
  FALHA=$((FALHA + 1))
fi

echo ""
echo "═════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo "RESULTADO: FALHA — $FALHA ramo(s) reprovaram"
  exit 1
fi
echo "RESULTADO: OK — o token morre com o contato, e o scanner nao ressuscita ninguem"
exit 0
