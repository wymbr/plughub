#!/usr/bin/env bash
# probe_reason_required_has_reader.sh — SFE-08 (2026-09-28)
#
# PERGUNTA: todo campo que um step `reason` EXIGE do modelo tem LEITOR no fluxo?
#
# POR QUE EXISTE
#   O `agente_avaliacao_v1` exigia `dimension_threads` e `overall_score` — que não chegavam
#   ao modelo por caminho nenhum e que nenhum step lia. Nada ficava vermelho: o tool-use
#   ignora o `output_schema`, e o fallback cobraria do modelo um número que ninguém usa.
#   O critério de leitor e as fontes da exigência estão no `_reason_required_reader.py`.
#
# RAMOS
#   A  há steps `reason` para julgar (testemunha de presença — zero é INCONCLUSIVO)
#   B  nenhum campo exigido sem leitor
#   C  falseabilidade: um campo exigido PLANTADO numa cópia é acusado
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO. Não precisa de stack.
set -u
cd "$(dirname "$0")/../.." || exit 2

FAIL=0
ok()  { echo "  v $1"; }
bad() { echo "  x $1"; FAIL=1; }
huh() { echo "  ? $1"; [ "$FAIL" = 0 ] && FAIL=2; }

SK=packages/skill-flow-engine/skills
RUN=infra/test/_reason_required_reader.py
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "=== probe_reason_required_has_reader — SFE-08 (exigido do modelo tem leitor) ==="
echo

if ! python3 "$RUN" "$SK" > "$TMP/out.txt" 2> "$TMP/err.txt"; then
  huh "A: o leitor falhou — $(head -1 "$TMP/err.txt")"; echo; echo "INCONCLUSIVO"; exit 2
fi
N=$(sed -n 's/^TOTAL //p' "$TMP/out.txt")
if [ -z "$N" ] || [ "$N" -lt 1 ] 2>/dev/null; then
  huh "A: nenhum step reason encontrado em $SK"
else
  ok "A: $N step(s) reason julgado(s)"
fi

if grep -q '^SEM_LEITOR' "$TMP/out.txt"; then
  bad "B: campo exigido do modelo SEM leitor no fluxo:"
  grep '^SEM_LEITOR' "$TMP/out.txt" | sed 's/^SEM_LEITOR /       /'
else
  ok "B: todo campo exigido tem leitor (ou o objeto é repassado inteiro)"
fi

# C — planta um campo exigido que ninguém lê num step reason de uma CÓPIA dos skills
mkdir -p "$TMP/sk"
cp "$SK"/*.yaml "$TMP/sk/"
python3 - "$TMP/sk" > "$TMP/plant.txt" 2>&1 <<'EOF'
import glob, os, sys, yaml
base = sys.argv[1]
for f in sorted(glob.glob(os.path.join(base, "*.yaml"))):
    d = yaml.safe_load(open(f, encoding="utf-8")) or {}
    for s in d.get("steps") or []:
        if isinstance(s, dict) and s.get("type") == "reason" and "json_schema" not in s:
            s.setdefault("output_schema", {})["campo_plantado_sfe08"] = {"type": "string", "required": True}
            yaml.safe_dump(d, open(f, "w", encoding="utf-8"), allow_unicode=True, sort_keys=False)
            print(os.path.basename(f)); sys.exit(0)
print("NENHUM")
EOF
ALVO=$(head -1 "$TMP/plant.txt")
if [ "$ALVO" = "NENHUM" ] || [ -z "$ALVO" ]; then
  huh "C: não havia step reason sem json_schema onde plantar"
elif python3 "$RUN" "$TMP/sk" | grep -q '^SEM_LEITOR .*campo_plantado_sfe08'; then
  ok "C: o campo plantado em ${ALVO%.yaml} é acusado"
else
  bad "C: o campo plantado em ${ALVO%.yaml} NÃO foi acusado — o gate não sabe reprovar"
fi

echo
case "$FAIL" in
  0) echo "OK"; exit 0 ;;
  2) echo "INCONCLUSIVO"; exit 2 ;;
  *) echo "FALHA"; exit 1 ;;
esac
