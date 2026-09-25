#!/usr/bin/env bash
# probe_skill_yaml_strict.sh — SFE-07 (2026-09-25)
#
# PERGUNTA: todo campo que o autor escreve num skill YAML chega ao que roda?
#
# POR QUE EXISTE
#   O `SkillFlowSchema` é Zod, e `z.object` DESCARTA chave desconhecida em silêncio. O
#   registry grava o resultado do parse, então o campo some do artefato e do snapshot sem
#   erro em lugar nenhum. Já custou duas vezes: o `issue_status` do `complete` (SFE-02,
#   37 de 96 steps o declaravam e 0 segmentos o tinham) e, medido na SFE-07, a
#   `description`/`items` do `output_schema` do `reason` (4 steps) e o `context_tags` do
#   `menu` (2 steps — o e-mail que o YAML prometia à aba Contexto nunca foi gravado).
#   Os schemas corrigidos são `.strict()` e RECUSAM; este gate pega os que ainda não são.
#
# RAMOS
#   A  todo skill YAML é convertido e validado (testemunha de presença: população > 0)
#   B  nenhum skill REPROVA no parse — um `.strict()` recusando é skill que o registry
#      não aceitaria publicar
#   C  nenhum campo é DESCARTADO pelo parse (os schemas ainda não-estritos)
#   D  falseabilidade: um `description` plantado num `output_schema` de CÓPIA reprova (B),
#      e um campo inventado num step de schema não-estrito reprova (C)
#
# Precisa de `packages/schemas/dist` compilado (a regra é a do registry, não uma cópia).
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO
set -u
cd "$(dirname "$0")/../.." || exit 2

FAIL=0
ok()  { echo "  v $1"; }
bad() { echo "  x $1"; FAIL=1; }
huh() { echo "  ? $1"; [ "$FAIL" = 0 ] && FAIL=2; }

[ -f packages/schemas/dist/index.js ] || { huh "packages/schemas/dist ausente — compile @plughub/schemas"; exit 2; }
command -v node >/dev/null || { huh "node ausente"; exit 2; }

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
to_json() {  # $1 = diretório de YAMLs · $2 = destino
  mkdir -p "$2"
  python3 - "$1" "$2" <<'PY'
import glob, json, os, sys, yaml
src, dst = sys.argv[1], sys.argv[2]
n = 0
for f in sorted(glob.glob(os.path.join(src, "*.yaml"))):
    y = yaml.safe_load(open(f, encoding="utf-8"))
    fl = y.get("flow") if isinstance(y, dict) and "flow" in y else y
    if isinstance(fl, dict) and "steps" in fl:
        json.dump(fl, open(os.path.join(dst, os.path.basename(f)[:-5] + ".json"), "w", encoding="utf-8"),
                  ensure_ascii=False, default=str)
        n += 1
print(n)
PY
}

echo "probe_skill_yaml_strict — campo do YAML que não chega ao que roda"
N=$(to_json packages/skill-flow-engine/skills "$TMP/real")
OUT=$(node infra/test/_skill_yaml_strict.js "$TMP/real")

# A
[ "${N:-0}" -gt 0 ] && ok "A — $N skill YAML(s) convertidos e validados pelo SkillFlowSchema compilado" \
                    || { huh "A — nenhum skill YAML encontrado"; exit 2; }
# B
PF=$(printf '%s\n' "$OUT" | grep '^PARSE_FAIL' || true)
[ -z "$PF" ] && ok "B — nenhum skill reprova no parse" \
             || { bad "B — skill que o registry não aceitaria publicar:"; printf '%s\n' "$PF" | sed 's/^/        /'; }
# C
LO=$(printf '%s\n' "$OUT" | grep '^LOST' || true)
[ -z "$LO" ] && ok "C — nenhum campo descartado pelo parse" \
             || { bad "C — campo que o autor escreveu e o parse jogou fora:"; printf '%s\n' "$LO" | sed 's/^/        /'; }

# D — falseabilidade, sobre CÓPIAS
mkdir -p "$TMP/mut"
python3 - "$TMP/real" "$TMP/mut" <<'PY'
import json, os, sys
src, dst = sys.argv[1], sys.argv[2]
reason = notify = None
for f in sorted(os.listdir(src)):
    fl = json.load(open(os.path.join(src, f), encoding="utf-8"))
    for st in fl["steps"]:
        if reason is None and st.get("type") == "reason" and st.get("output_schema"):
            k = next(iter(st["output_schema"]))
            st["output_schema"][k]["description"] = "plantada pelo probe"
            json.dump(fl, open(os.path.join(dst, "mut_reason.json"), "w", encoding="utf-8"), ensure_ascii=False)
            reason = f
            break
    fl = json.load(open(os.path.join(src, f), encoding="utf-8"))
    for st in fl["steps"]:
        if notify is None and st.get("type") == "notify":
            st["campo_inventado_pelo_probe"] = 1
            json.dump(fl, open(os.path.join(dst, "mut_notify.json"), "w", encoding="utf-8"), ensure_ascii=False)
            notify = f
            break
PY
MOUT=$(node infra/test/_skill_yaml_strict.js "$TMP/mut")
printf '%s\n' "$MOUT" | grep -q '^PARSE_FAIL mut_reason .*output_schema' \
  && ok "D — description plantada no output_schema de um reason → REPROVA no parse (.strict())" \
  || bad "D — description plantada no output_schema passou: o ReasonOutputFieldSchema não é estrito"
printf '%s\n' "$MOUT" | grep -q '^LOST mut_notify .*campo_inventado_pelo_probe\|^PARSE_FAIL mut_notify' \
  && ok "D — campo inventado num notify → acusado (descartado ou recusado)" \
  || bad "D — campo inventado num notify passou calado: o ramo C não enxerga descarte"

echo
case $FAIL in 0) echo "OK";; 1) echo "FALHA";; *) echo "INCONCLUSIVO";; esac
exit $FAIL
