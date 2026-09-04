#!/usr/bin/env bash
# mut_ctx_read_audience.sh — falseabilidade do probe da CTX-01/CTX-02.
#
# Cada mutação e' aplicada com ASSERT. Sem ele, "a mutação não casou a âncora" e
# "o gate é robusto" produzem o mesmo verde — foi o que aconteceu na primeira
# bateria do gate das superfícies, algumas horas antes.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2

G="infra/test/probe_ctx_read_audience.sh"
COMPOSE="docker-compose.demo.yml"
CENSO="infra/test/q_ctx_read_audience_census.ts"
FALHAS=0

julga() {
  local nome="$1" ramo="$2" out="$3" rc="$4"
  if [ "$rc" = "0" ]; then
    echo "  FALHOU    $nome — gate VERDE com a mutação aplicada"; FALHAS=$((FALHAS+1)); return
  fi
  if printf '%s' "$out" | grep -qE "REPROVA +$ramo\."; then
    echo "  OK        $nome -> ramo $ramo reprovou"
    printf '%s' "$out" | grep -E "REPROVA +$ramo\." | head -1 | sed 's/^/       /'
  else
    echo "  PARCIAL   $nome — reprovou, mas não pelo ramo $ramo"; FALHAS=$((FALHAS+1))
    printf '%s' "$out" | grep -E 'REPROVA|INCONCLUSIVO' | head -3 | sed 's/^/       /'
  fi
}

echo "falseabilidade — probe_ctx_read_audience"
echo

# ── M1: o compose deixa de declarar a env ────────────────────────────────────
# O container em execução CONTINUA com ela, o que é exatamente o cenário
# perigoso: funciona hoje e some no próximo `up -d`.
cp "$COMPOSE" "$COMPOSE.bak"
python3 - <<'PY'
import io, sys
p = "docker-compose.demo.yml"
t = io.open(p, encoding="utf-8").read()
a = "      CONFIG_API_URL: http://config-api:3600\n"
if t.count(a) < 1:
    sys.exit("MUTACAO NAO APLICADA: ancora ausente")
# remove só a ocorrência do skill-flow-service (a que segue MCP_AUTH_URL)
i = t.index("MCP_AUTH_URL:   http://mcp-server-auth:3150")
j = t.index(a, i)
io.open(p, "w", encoding="utf-8", newline="").write(t[:j] + t[j + len(a):])
PY
OUT=$(bash "$G" 2>&1); RC=$?
julga "M1 compose sem a env (container ainda tem)" "D" "$OUT" "$RC"
cp "$COMPOSE.bak" "$COMPOSE"; rm -f "$COMPOSE.bak"

# ── M2: uma exceção declarada que não se aplica mais ─────────────────────────
cp "$CENSO" "$CENSO.bak"
python3 - <<'PY'
import io, sys
p = "infra/test/q_ctx_read_audience_census.ts"
t = io.open(p, encoding="utf-8").read()
a = "const LEGITIMOS: Array<{ skill: string; tag: string; porque: string }> = [\n"
if a not in t:
    sys.exit("MUTACAO NAO APLICADA: ancora ausente")
io.open(p, "w", encoding="utf-8", newline="").write(
    t.replace(a, a + '  { skill: "nao_existe.yaml", tag: "session.fantasma", porque: "MUTACAO M2" },\n', 1))
PY
OUT=$(bash "$G" 2>&1); RC=$?
julga "M2 exceção declarada órfã" "C" "$OUT" "$RC"
cp "$CENSO.bak" "$CENSO"; rm -f "$CENSO.bak"

# ── M3: o censo deixa de enxergar os skills ──────────────────────────────────
cp "$CENSO" "$CENSO.bak"
python3 - <<'PY'
import io, sys
p = "infra/test/q_ctx_read_audience_census.ts"
t = io.open(p, encoding="utf-8").read()
a = 'const SKILLS = "/w/packages/skill-flow-engine/skills"'
if a not in t:
    sys.exit("MUTACAO NAO APLICADA: ancora ausente")
io.open(p, "w", encoding="utf-8", newline="").write(
    t.replace(a, 'const SKILLS = "/w/infra/dialog"', 1))
PY
OUT=$(bash "$G" 2>&1); RC=$?
julga "M3 censo sem amostra (testemunha de presença)" "A" "$OUT" "$RC"
cp "$CENSO.bak" "$CENSO"; rm -f "$CENSO.bak"

echo
OUT=$(bash "$G" 2>&1); RC=$?
if [ "$RC" = "0" ]; then echo "restaurado: gate VERDE"
else echo "restaurado: gate NÃO voltou ao verde (rc=$RC)"; printf '%s' "$OUT" | tail -8; FALHAS=$((FALHAS+1)); fi

echo
[ "$FALHAS" -gt 0 ] && { echo "BATERIA REPROVADA ($FALHAS)"; exit 1; }
echo "BATERIA OK — 3 mutações, 3 ramos reprovando"
