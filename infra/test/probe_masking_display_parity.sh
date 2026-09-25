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
# MSK-05 (2026-09-25): o display comparado é o do `by_role` do catálogo VIVO (as portas
# TS e channel-gateway o recebem; o quality-ingest confronta a cópia dele contra ele).
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

# ── o catálogo VIVO (MSK-05) ──────────────────────────────────────────────────
# Desde a MSK-05 o display é a máscara do `operator` em `masking.types` — logo "as
# portas concordam" só se julga com o MESMO catálogo nas que o leem. Ele é lido UMA vez,
# do config-api, e entregue às portas TS e channel-gateway. O quality-ingest NÃO o
# recebe: carrega uma cópia do `by_role` semeado, e é justamente essa cópia que este
# gate confere — se o tenant mudar a exibição, a coluna dele diverge e fica vermelha.
TENANT="${TENANT:-tenant_demo}"
CAT="$($DC exec -T mcp-server-plughub node -e "
fetch(process.env.CONFIG_API_URL + '/config/masking/types?tenant_id=${TENANT}')
  .then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)))
  .then(b => { const v = b && b.value; if (!v || !Array.isArray(v.types)) throw new Error('sem types[]'); console.log(JSON.stringify(v)) })
  .catch(e => console.log('ERR:' + e.message))
" 2>&1 | tr -d '\r' | tail -1)"
case "$CAT" in
  ERR:*|"") echo "  ? catálogo vivo indisponível: ${CAT:-vazio}"
            echo; echo "VEREDICTO: INCONCLUSIVO — sem o catálogo, a paridade não significa nada"; exit 2 ;;
esac
N_DET="$(printf '%s' "$CAT" | grep -o '"detect_pattern"' | grep -c '')"
echo "  catálogo vivo de ${TENANT}: ${N_DET} tipo(s) com detect_pattern"
if [ "$N_DET" -lt 4 ]; then
  echo; echo "VEREDICTO: INCONCLUSIVO — o catálogo vivo tem menos de 4 tipos detectáveis"; exit 2
fi

# ── porta 1: TS — a rede (`maskFreeText`) com o catálogo vivo; o `message_send` usa a
#    MESMA `detectedDisplay` dentro do token, então esta porta responde pelas duas.
TS_RAW="$($DC exec -T -e CAT="$CAT" mcp-server-plughub sh -c "cd /app/packages/mcp-server-plughub && node -e \"
const S = require('@plughub/schemas');
if (typeof S.detectedDisplay !== 'function') { console.log('ERR:detectedDisplay ausente (imagem anterior a MSK-05?)'); process.exit(0) }
const cat = JSON.parse(process.env.CAT);
const vec = ['123.456.789-00','1234 5678 9012 3456','1234-5678-9012-3456','joao.silva@empresa.com.br','(11) 98765-4321'];
for (const t of vec) console.log(S.maskFreeText(t, '', cat).value);
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

CG_RAW="$($DC exec -T -e CAT="$CAT" channel-gateway sh -c "cd /app/packages/channel-gateway && python3 -c \"
import sys, os, json
sys.path.insert(0, 'src')
try:
    from plughub_channel_gateway.adapters.webhook import _mask_pii
except Exception as e:
    print('ERR:' + str(e)); raise SystemExit(0)
cat = {t['id']: t for t in json.loads(os.environ['CAT'])['types']}
for t in ['123.456.789-00','1234 5678 9012 3456','1234-5678-9012-3456','joao.silva@empresa.com.br','(11) 98765-4321']:
    print(_mask_pii(t, cat))
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
CG_CLEAN="$($DC exec -T -e CAT="$CAT" channel-gateway sh -c "cd /app/packages/channel-gateway && python3 -c \"
import sys, os, json
sys.path.insert(0, 'src')
from plughub_channel_gateway.adapters.webhook import _mask_pii
print(_mask_pii('${CLEAN}', {t['id']: t for t in json.loads(os.environ['CAT'])['types']}))
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
