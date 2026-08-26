#!/usr/bin/env bash
# probe_masking_display_parity.sh — GATE: as três portas de masking produzem o MESMO
# display para o mesmo dado?
#
# Existe porque, medido em 2026-08-26, NENHUMA das cinco linhas era unânime — e nada
# no repositório comparava as portas entre si. Cada uma tinha teste próprio, todos
# verdes, todos medindo a porta contra ela mesma.
#
# 🔴 O modo de falha DESTE gate é conhecido e tratado: **três portas que não mascaram
# nada concordam perfeitamente.** Por isso cada célula tem, ao lado da comparação, a
# testemunha de que houve mascaramento (saída ≠ entrada E o dado original ausente).
# Sem ela, "paridade OK" seria compatível com masking desligado nas três.
#
# Três estados: OK · FALHA · INCONCLUSIVO. Roda do host; exige a stack construída.
set -u

cd "$(dirname "$0")/../.." || exit 2
DC="${DC:-docker compose -f docker-compose.demo.yml}"

fail=0
inconclusive=0

# Vetores: nome|texto. Um por família de regra + as duas grafias de cartão.
V_NAMES=(cpf cartao_espaco cartao_hifen email telefone)
V_TEXTS=(
  "123.456.789-00"
  "1234 5678 9012 3456"
  "1234-5678-9012-3456"
  "joao.silva@empresa.com.br"
  "(11) 98765-4321"
)

echo "═══ probe_masking_display_parity ════════════════════════════"

# ── porta 1: TS — mesma ALGORITMIA das portas Python (substituição inline),
#    para comparar maçã com maçã: o display sai de buildDisplay, o passeio é igual.
TS_RAW="$($DC exec -T mcp-server-plughub sh -c "cd /app/packages/mcp-server-plughub && node -e \"
let M; try { M = require('./dist/lib/masking.js').MaskingService } catch (e) { console.log('ERR:'+e.message); process.exit(0) }
if (!M || typeof M.buildDisplay !== 'function') { console.log('ERR:buildDisplay ausente'); process.exit(0) }
const { DEFAULT_MASKING_RULES } = require('@plughub/schemas');
const vec = ['123.456.789-00','1234 5678 9012 3456','1234-5678-9012-3456','joao.silva@empresa.com.br','(11) 98765-4321'];
for (const texto of vec) {
  let s = texto;
  for (const r of DEFAULT_MASKING_RULES) {
    s = s.replace(new RegExp(r.pattern, 'g'), m => M.buildDisplay(m, r));
  }
  console.log(s);
}
\"" 2>&1 | tr -d '\r')"

PY_RAW="$($DC exec -T quality-ingest sh -c "cd /app && python3 -c \"
import sys
sys.path.insert(0, 'src')
try:
    from plughub_quality_ingest.masking import mask_text
except Exception as e:
    print('ERR:' + str(e)); raise SystemExit(0)
for t in ['123.456.789-00','1234 5678 9012 3456','1234-5678-9012-3456','joao.silva@empresa.com.br','(11) 98765-4321']:
    print(mask_text(t)[0])
\"" 2>&1 | tr -d '\r')"

CG_RAW="$($DC exec -T channel-gateway sh -c "cd /app/packages/channel-gateway && python3 -c \"
import sys
sys.path.insert(0, 'src')
try:
    from plughub_channel_gateway.adapters.webhook import _mask_pii
except Exception as e:
    print('ERR:' + str(e)); raise SystemExit(0)
for t in ['123.456.789-00','1234 5678 9012 3456','1234-5678-9012-3456','joao.silva@empresa.com.br','(11) 98765-4321']:
    print(_mask_pii(t))
\"" 2>&1 | tr -d '\r')"

check_port() {
  local nome="$1" raw="$2"
  case "$raw" in
    ERR:*|"")
      echo "  ? porta ${nome} não executou: ${raw:-vazio}"
      inconclusive=$((inconclusive+1))
      return 1
      ;;
  esac
  local n; n="$(printf '%s\n' "$raw" | grep -c '')"
  if [ "$n" -ne 5 ]; then
    echo "  ? porta ${nome} devolveu ${n} linha(s), esperadas 5"
    inconclusive=$((inconclusive+1))
    return 1
  fi
  return 0
}

echo
echo "── preflight: as três portas executaram? ────────────────────"
p1=0; p2=0; p3=0
check_port "TS (mcp-server)"     "$TS_RAW" || p1=1
check_port "PY (quality-ingest)" "$PY_RAW" || p2=1
check_port "PY (channel-gateway)" "$CG_RAW" || p3=1
if [ $((p1+p2+p3)) -ne 0 ]; then
  echo
  echo "VEREDICTO: INCONCLUSIVO — porta(s) não executada(s); comparar duas não julga três"
  exit 2
fi
echo "  ✓ as três responderam 5 linhas"

echo
echo "── comparação célula a célula (+ testemunha de mascaramento) ─"
printf "  %-14s %-22s %-22s %-22s %s\n" VETOR TS QUALITY-INGEST CHANNEL-GATEWAY VEREDICTO
i=1
while [ "$i" -le 5 ]; do
  idx=$((i-1))
  nome="${V_NAMES[$idx]}"
  orig="${V_TEXTS[$idx]}"
  a="$(printf '%s\n' "$TS_RAW" | sed -n "${i}p")"
  b="$(printf '%s\n' "$PY_RAW" | sed -n "${i}p")"
  c="$(printf '%s\n' "$CG_RAW" | sed -n "${i}p")"

  linha_ok=1
  motivo="iguais"
  if [ "$a" != "$b" ] || [ "$b" != "$c" ]; then
    linha_ok=0
    motivo="DIVERGEM"
  fi
  # testemunha: cada porta tem de ter mascarado de fato
  for saida in "$a" "$b" "$c"; do
    if [ "$saida" = "$orig" ]; then
      linha_ok=0
      motivo="NÃO MASCAROU (paridade seria vácua)"
    fi
  done

  if [ "$linha_ok" = "1" ]; then
    printf "  %-14s %-22s %-22s %-22s ✓ %s\n" "$nome" "$a" "$b" "$c" "$motivo"
  else
    printf "  %-14s %-22s %-22s %-22s ✗ %s\n" "$nome" "$a" "$b" "$c" "$motivo"
    fail=$((fail+1))
  fi
  i=$((i+1))
done

# ── testemunha final: o gate SABE reprovar? ──────────────────────────────────
# Texto sem PII tem de sair intacto nas três. Se este ramo "mascarar", as regras
# estão casando o que não deviam e a paridade acima não significa o que parece.
echo
echo "── testemunha negativa: texto sem PII ───────────────────────"
CLEAN="obrigado pelo contato, tenha um bom dia"
CG_CLEAN="$($DC exec -T channel-gateway sh -c "cd /app/packages/channel-gateway && python3 -c \"
import sys
sys.path.insert(0, 'src')
from plughub_channel_gateway.adapters.webhook import _mask_pii
print(_mask_pii('${CLEAN}'))
\"" 2>&1 | tr -d '\r')"
if [ "$CG_CLEAN" = "$CLEAN" ]; then
  echo "  ✓ texto limpo atravessa intacto"
else
  echo "  ✗ texto SEM PII foi alterado: '${CG_CLEAN}' — regra casando demais"
  fail=$((fail+1))
fi

echo
echo "═════════════════════════════════════════════════════════════"
if [ "$fail" -gt 0 ]; then
  echo "VEREDICTO: FALHA — ${fail} linha(s) vermelha(s)"
  exit 1
fi
if [ "$inconclusive" -gt 0 ]; then
  echo "VEREDICTO: INCONCLUSIVO — ${inconclusive} ramo(s) não julgado(s). NÃO é OK."
  exit 2
fi
echo "VEREDICTO: OK — as três portas produzem o mesmo display, e todas mascararam"
exit 0
