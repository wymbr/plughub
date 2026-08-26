#!/usr/bin/env bash
# q_masking_display_parity.sh — MEDIÇÃO (não gate): as três portas de masking
# produzem o MESMO display para o mesmo dado?
#
# Achado de 2026-08-26 que motivou este script: o `buildDisplay` do TS
# (mcp-server-plughub/src/lib/masking.ts) NÃO usa `replacement` salvo como fallback —
# ele constrói `"*" * (n_digitos - N) + cauda`. As portas Python usam
# `replacement[:-len(cauda)] + cauda`. As duas semânticas divergem, e o docstring da
# porta de quality-ingest declara fidelidade que o código contradiz.
#
# Este script NÃO decide qual é o canônico. Ele imprime as três saídas lado a lado,
# para que a decisão seja tomada sobre o dado. Um veredicto aqui seria opinião com
# cara de medição.
#
# Roda do host. Exige a stack de pé.
set -u

cd "$(dirname "$0")/../.." || exit 2
DC="${DC:-docker compose -f docker-compose.demo.yml}"

VECTORS=(
  "cpf|123.456.789-00"
  "cartao_espaco|1234 5678 9012 3456"
  "cartao_hifen|1234-5678-9012-3456"
  "email|joao.silva@empresa.com.br"
  "telefone|(11) 98765-4321"
)

echo "═══ paridade de DISPLAY entre as três portas de masking ═════"
echo

# ── porta 1: TS canônico (o que vai para o token do stream) ───────────────────
echo "── porta 1: TS — MaskingService.buildDisplay (mcp-server) ───"
TS_OUT="$($DC exec -T mcp-server-plughub sh -c "cd /app/packages/mcp-server-plughub && node -e \"
const path='./dist/lib/masking.js';
let M; try { M = require(path).MaskingService } catch (e) { console.log('ERR:'+e.message); process.exit(0) }
if (!M || typeof M.buildDisplay !== 'function') { console.log('ERR:buildDisplay ausente (private compilado?)'); process.exit(0) }
const { DEFAULT_MASKING_RULES } = require('@plughub/schemas');
const vec = [['cpf','123.456.789-00'],['cartao_espaco','1234 5678 9012 3456'],['cartao_hifen','1234-5678-9012-3456'],['email','joao.silva@empresa.com.br'],['telefone','(11) 98765-4321']];
for (const [nome, texto] of vec) {
  let saida = 'SEM_REGRA_QUE_CASE';
  for (const r of DEFAULT_MASKING_RULES) {
    const m = new RegExp(r.pattern).exec(texto);
    if (m) { saida = M.buildDisplay(m[0], r) + '   [' + r.category + ']'; break }
  }
  console.log(nome + '\t' + saida);
}
\"" 2>&1 | tr -d '\r')"
echo "$TS_OUT" | sed 's/^/  /'
echo

# ── porta 2: quality-ingest (a que se declara "faithful port") ────────────────
echo "── porta 2: Python — quality-ingest/masking.py ──────────────"
# ⚠️ quality-ingest NÃO segue o layout /app/packages/<pkg> dos outros serviços:
# seu Dockerfile faz WORKDIR /app + COPY src/ ./src/, então o pacote está em /app/src.
PY_OUT="$($DC exec -T quality-ingest sh -c "cd /app && python3 -c \"
import sys
sys.path.insert(0, 'src')
from plughub_quality_ingest.masking import mask_text
vec = [('cpf','123.456.789-00'),('cartao_espaco','1234 5678 9012 3456'),('cartao_hifen','1234-5678-9012-3456'),('email','joao.silva@empresa.com.br'),('telefone','(11) 98765-4321')]
for nome, texto in vec:
    masked, cats = mask_text(texto)
    print(nome + chr(9) + masked + '   [' + ','.join(cats) + ']')
\"" 2>&1 | tr -d '\r')"
echo "$PY_OUT" | sed 's/^/  /'
echo

# ── porta 3: channel-gateway (net-pass de edição de aprovação) ────────────────
echo "── porta 3: Python — channel-gateway _mask_pii ──────────────"
CG_OUT="$($DC exec -T channel-gateway sh -c "cd /app/packages/channel-gateway && python3 -c \"
import sys
sys.path.insert(0, 'src')
from plughub_channel_gateway.adapters.webhook import _mask_pii
vec = [('cpf','123.456.789-00'),('cartao_espaco','1234 5678 9012 3456'),('cartao_hifen','1234-5678-9012-3456'),('email','joao.silva@empresa.com.br'),('telefone','(11) 98765-4321')]
for nome, texto in vec:
    print(nome + chr(9) + str(_mask_pii(texto)))
\"" 2>&1 | tr -d '\r')"
echo "$CG_OUT" | sed 's/^/  /'
echo
echo "═════════════════════════════════════════════════════════════"
echo "Leitura: linha a linha, as três colunas TÊM de coincidir. Onde não"
echo "coincidem, o mesmo dado pessoal tem display diferente conforme a porta"
echo "por onde o texto entrou — e nenhum teste hoje compara as três."
