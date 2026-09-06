#!/usr/bin/env bash
# probe_orchestrator_tree_nav.sh
#
# F1 do `adr-orchestrator-tree-navigation` (ORQ-01) — navegação de orquestrador
# como árvore de DialogForm.
#
# QUATRO proposições, uma por ramo:
#   A  a forma de navegação está publicada e TEM árvore (controle positivo)
#   B  a projeção desce: raiz → pasta → folha, um nível por vez
#   C  caminho DESCONHECIDO não degrada para a raiz   ← o que sustenta o resto
#   D  o ciclo `menu → … → menu` é ACEITO e o ciclo SEM menu é RECUSADO
#
# ⚠️ O ramo C é o único que não tem cara de teste feliz, e é o que importa. Se
# `optionsAtPath` devolvesse a raiz para um segmento inexistente, a tela voltaria
# ao menu principal, pareceria certa, e o cliente perderia a navegação sem que
# nada ficasse vermelho — o valor plausível mais barato de produzir aqui.
#
# ⚠️ O ramo D carrega a PRÓPRIA MUTAÇÃO: ele não pergunta "o validador roda?",
# pergunta "o validador DISTINGUE?". Um validador que aceitasse os dois fluxos
# passaria num teste de fumaça e não guardaria nada — e a afirmação central do
# ADR (*"o ciclo é sancionado por construção"*) viraria leitura de comentário.
set -u

DIALOG="${PLUGHUB_DIALOG_URL:-http://localhost:3760}"
TENANT="${PLUGHUB_TENANT_ID:-tenant_demo}"
FORM="${PLUGHUB_NAV_FORM:-dialog_navegacao_atendimento_v1}"
RAIZ="$(cd "$(dirname "$0")/.." && pwd)/.."
ENGINE="$RAIZ/packages/skill-flow-engine"
SCHEMAS="$RAIZ/packages/schemas"
SKILL="$ENGINE/skills/skill_navegacao_v1.yaml"

FAIL=0
INCONC=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=1; }
info() { printf '  \033[33m•\033[0m %s\n' "$1"; }

printf '\033[1mprobe_orchestrator_tree_nav — a arvore navega, e o ciclo e guardado\033[0m\n\n'

# node é pré-requisito dos ramos B e D. Ausente ⇒ INCONCLUSIVO NOMEADO, nunca
# verde: é a mesma armadilha do `probe_mcp_permissions_producer`, que saía 1 por
# falta de node e parecia defeito de produto.
NODE="$(command -v node 2>/dev/null || true)"
if [ -z "$NODE" ] && [ -x "$HOME/.nvm/versions/node/v24.14.1/bin/node" ]; then
  NODE="$HOME/.nvm/versions/node/v24.14.1/bin/node"
fi

# ── A: a forma existe, está publicada e tem árvore ──────────────────────────
printf '\033[1mA — a forma de navegacao esta publicada e TEM arvore\033[0m\n'
BODY=$(curl -s "${DIALOG}/v1/dialog/forms/${FORM}?status=published" -H "x-tenant-id: ${TENANT}")
ARV=$(printf '%s' "$BODY" | python3 -c '
import sys, json
try: d = json.load(sys.stdin)
except Exception: print("ERRO"); raise SystemExit
qs = [n for n in d.get("nodes", []) if n.get("kind") == "question"]
tem = any(any("options" in o for o in (q.get("options") or [])) for q in qs)
print("SIM" if tem else "NAO")' 2>/dev/null)
if [ "${ARV:-ERRO}" = "ERRO" ] || [ -z "${ARV:-}" ]; then
  info "forma ${FORM} nao encontrada na dialog-api — SEM AMOSTRA"
  printf '\n\033[33m\033[1mINCONCLUSIVO\033[0m — o dialog-seed nao rodou, ou a stack esta fora.\n'; exit 1
fi
if [ "$ARV" = "SIM" ]; then ok "forma publicada, com subarvore"; else bad "forma publicada mas SEM subarvore — nao ha o que navegar"; fi

# ── B e C: a projeção, sobre a forma REAL ───────────────────────────────────
printf '\n\033[1mB/C — a projecao desce, e o desconhecido NAO vira raiz\033[0m\n'
if [ -z "$NODE" ]; then
  info "node ausente no PATH — ramos B e D nao exercidos"
  INCONC=1
elif [ ! -f "$SCHEMAS/dist/index.js" ]; then
  info "packages/schemas/dist ausente (rode o build) — ramos B e D nao exercidos"
  INCONC=1
else
  OUT=$(printf '%s' "$BODY" | "$NODE" --input-type=module -e '
import pkg from "'"$SCHEMAS"'/dist/index.js"
const { buildRender, optionsAtPath } = pkg
let raw = ""
process.stdin.on("data", d => raw += d)
process.stdin.on("end", () => {
  const form = JSON.parse(raw)
  const q = buildRender(form).questions.find(x => (x.options || []).some(o => o.options))
  if (!q) { console.log("SEM_ARVORE"); return }
  const raizes = optionsAtPath(q.options, [])
  const pasta  = (q.options.find(o => o.options) || {}).id
  const desce  = optionsAtPath(q.options, [pasta])
  const folha  = optionsAtPath(q.options, [pasta, desce.options[0].id])
  const fake   = optionsAtPath(q.options, ["___nao_existe___"])
  console.log([
    raizes.found && !raizes.is_leaf && raizes.options.length > 0 ? "RAIZ_OK" : "RAIZ_RUIM",
    desce.found && !desce.is_leaf && desce.options.length > 0    ? "PASTA_OK" : "PASTA_RUIM",
    folha.found && folha.is_leaf && folha.options.length === 0   ? "FOLHA_OK" : "FOLHA_RUIM",
    !fake.found && fake.options.length === 0                     ? "DESCONHECIDO_OK" : "DESCONHECIDO_DEGRADA",
    raizes.options.length,
  ].join(" "))
})' 2>/dev/null)
  case "$OUT" in
    *RAIZ_OK*PASTA_OK*FOLHA_OK*) ok "raiz → pasta → folha: um nivel por vez ($(printf '%s' "$OUT" | awk '{print $5}') raizes)" ;;
    "") bad "projecao nao respondeu — INCONCLUSIVO no instrumento"; INCONC=1 ;;
    *)  bad "projecao errada: ${OUT}" ;;
  esac
  case "$OUT" in
    *DESCONHECIDO_OK*)      ok "segmento inexistente: found=false e options VAZIO — nao degrada" ;;
    *DESCONHECIDO_DEGRADA*) bad "segmento inexistente DEGRADOU para a raiz — a tela mente sem ficar vermelha" ;;
  esac
fi

# ── D: o ciclo é guardado — com a mutação DENTRO do gate ────────────────────
printf '\n\033[1mD — o ciclo pelo `menu` e aceito; o ciclo SEM menu e recusado\033[0m\n'
if [ -z "$NODE" ] || [ ! -f "$ENGINE/dist/index.js" ]; then
  info "node ou packages/skill-flow-engine/dist ausente — ramo D nao exercido"
  INCONC=1
elif [ ! -f "$SKILL" ]; then
  bad "skill_navegacao_v1.yaml nao existe"
fi
# O payload do fluxo vem do YAML por python3 (o gate já depende dele nos ramos acima).
if [ -n "$NODE" ] && [ -f "$ENGINE/dist/index.js" ] && [ -f "$SKILL" ]; then
  TMPF=$(mktemp) || TMPF=/tmp/navflow.$$.json
  python3 - "$SKILL" "$TMPF" <<'PY' 2>/dev/null
import io, json, sys
try:
    import yaml
except ImportError:
    sys.exit(3)
d = yaml.safe_load(io.open(sys.argv[1], encoding="utf-8").read())
io.open(sys.argv[2], "w", encoding="utf-8", newline="").write(
    json.dumps({"skill_id": d["id"], "entry": d["entry"], "steps": d["steps"]}))
PY
  if [ -s "$TMPF" ]; then
    VER=$(NAVFLOW="$TMPF" "$NODE" --input-type=module -e '
import pkg from "'"$ENGINE"'/dist/index.js"
import { readFileSync } from "node:fs"
const { validateFlow } = pkg
const flow = JSON.parse(readFileSync(process.env.NAVFLOW, "utf8"))
const julga = f => { try { validateFlow(f); return "ACEITO" } catch { return "RECUSADO" } }
const real = julga(flow)
const mut  = JSON.parse(JSON.stringify(flow))
const av   = mut.steps.find(s => s.id === "avaliar")
if (av) av.default = "descer"
console.log(real + " " + julga(mut))
' 2>/dev/null)
    case "${VER:-}" in
      "ACEITO RECUSADO") ok "ciclo pelo menu ACEITO; ciclo sem menu RECUSADO — o validador DISTINGUE" ;;
      "ACEITO ACEITO")   bad "o validador aceitou os DOIS — ele nao guarda ciclo, e o ADR se apoia nisso" ;;
      "RECUSADO "*)      bad "o fluxo REAL foi recusado pelo validador de ciclos: ${VER}" ;;
      "")                info "validador nao respondeu (dist velho?) — ramo D nao exercido"; INCONC=1 ;;
      *)                 bad "veredicto inesperado: ${VER}" ;;
    esac
  else
    info "PyYAML ausente — ramo D nao exercido"
    INCONC=1
  fi
  rm -f "$TMPF"
fi

printf '\n'
if [ "$FAIL" != "0" ]; then
  printf '\033[31m\033[1mVERMELHO\033[0m\n'; exit 1
elif [ "$INCONC" != "0" ]; then
  printf '\033[33m\033[1mINCONCLUSIVO\033[0m — faltou pre-requisito, e ele esta nomeado acima.\n'; exit 1
else
  printf '\033[32m\033[1mVERDE\033[0m — a arvore navega, o desconhecido recusa, e o ciclo e guardado.\n'; exit 0
fi
