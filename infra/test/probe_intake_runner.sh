#!/usr/bin/env bash
# probe_intake_runner.sh — 2026-09-14  (PID-04)
#
# PERGUNTA: a porta do limite é o runner de PLATAFORMA, com tudo o que é do processo na
#           config do pool — e o pedido novo nasce sob a identidade de quem provou?
#
# O QUE FOI MEDIDO ANTES
#   O `skill_limite_entrada_v1` fazia identificação, prova, pendência e coleta num YAML de
#   domínio (194 sessões; os três acessos, OTP e degradação ao SAC). E disparava o processo
#   com `context_json` montado por TEMPLATE de texto com o que o cliente digitou: ao vivo,
#   um número de cartão `4111…", "session.cpf": "52900000000", "x": "1` fez o processo
#   nascer com o CPF de outra pessoa, depois de o cliente provar o próprio por OTP.
#
# DOIS RAMOS
#   A  CENSO — runner sem pool/forma literal de domínio; sem `context_json`; todo nó de
#      roteiro referenciado (runner + continuidade) existe na forma que o `limite_ia`
#      configura; config obrigatória declarada no seed; nada do intake antigo; o
#      `limite_retorno` lê a journey; o `choice` enxerga `$.config.*` e lê `@ctx.journey.*`
#      na journey. Controle da cópia limpa e seis mutações sobre CÓPIA.
#   B  AO VIVO — o slot `current` do `limite_ia` roda o runner com a config exigida; a
#      forma publicada cobre os nós; e o ACESSO 1 pelo chat com a mesma injeção: o
#      processo nasce com o CPF de quem provou, e o texto injetado chega literal.
#
# A paridade dos acessos 2 e 3 é provada pelos probes que já os exercem pelo chat:
# `probe_customer_cancel` (continuidade + cancelamento), `probe_resume_requirement` E
# (entrega do resultado) e `probe_journey_merge_status_access` (pertença).
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

TENANT="${TENANT:-tenant_demo}"
COMPOSE="docker compose -p plughub-demo -f docker-compose.demo.yml"
REG="${REG:-http://localhost:3300}"
DIALOG="${DIALOG:-http://localhost:3760}"
CENSO=infra/test/_intake_runner_census.py
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
jexpr() { printf '%s' "$1" | python3 -c 'import json,sys
try:
    d = json.loads(sys.stdin.read())
    print(eval(sys.argv[1]))
except Exception as e:
    print("__ERRO__ %s" % e)' "$2"; }

LIMPO="d['runner_presente'] and not d['literais_de_dominio'] and not d['context_json_no_runner'] and d['refs_by_node'] >= 12 and not d['nos_ausentes'] and not d['config_faltando'] and not d['intake_antigo'] and not d['retorno_le_sessao'] and d['choice_le_config'] and d['choice_le_journey'] and d['continuidade_le_journey'] == ['limite_solicitado', 'numero_cartao']"

echo "════════════════════════════════════════════════════════════════════"
echo " a porta é de plataforma, e o pedido nasce sob quem provou?"
echo "════════════════════════════════════════════════════════════════════"

echo ""
echo "── A · CENSO ──────────────────────────────────────────────────────────"
C=$(python3 "$CENSO" .)
echo "   $C"
if [ "$(jexpr "$C" "d['runner_presente']")" != "True" ]; then
  incon "o censo não achou o runner — não mediu nada"
elif [ "$(jexpr "$C" "$LIMPO")" = "True" ]; then
  ok "runner sem domínio, nós e config cobertos, intake antigo fora, retorno na journey"
else
  falha "censo sujo: $C"
fi

COPIA=$(mktemp -d)
trap 'rm -rf "$COPIA"' EXIT
copia_limpa() {
  rm -rf "$COPIA"/*
  for f in packages/skill-flow-engine/skills packages/skill-flow-engine/src/steps/choice.ts \
           infra/registry/tenant_demo.yaml infra/dialog; do
    mkdir -p "$COPIA/$(dirname "$f")"
    cp -r "$f" "$COPIA/$f"
  done
}
muta() {  # $1 rótulo · $2 arquivo relativo · $3 expressão python sobre `s`
  copia_limpa
  python3 - "$COPIA/$2" "$3" <<'PY'
import sys
p, expr = sys.argv[1], sys.argv[2]
s = open(p, encoding="utf-8").read()
novo = eval(expr)
assert novo != s, "mutação no-op"
open(p, "w", encoding="utf-8", newline="").write(novo)
PY
  if [ $? -ne 0 ]; then incon "mutação '$1' não se aplicou (âncora sumiu)"; return; fi
  M=$(python3 "$CENSO" "$COPIA")
  if [ "$(jexpr "$M" "$LIMPO")" = "False" ]; then ok "mutação '$1' → censo sujo"
  else falha "mutação '$1' passou no censo: $M"; fi
}

R=packages/skill-flow-engine/skills/skill_intake_runner_v1.yaml
copia_limpa
C0=$(python3 "$CENSO" "$COPIA")
if [ "$(jexpr "$C0" "$LIMPO")" != "True" ]; then
  incon "a CÓPIA sem mutação não é limpa — as mutações não provariam nada: $C0"
else
  ok "controle: a cópia sem mutação é limpa"
  muta "pool de domínio literal no runner" "$R" \
    "s.replace('pool: \"\$.config.degrade_target\"', 'pool: sac_ia', 1)"
  muta "processo por template de texto" "$R" \
    "s.replace('      context_fields:    \"\$.pipeline_state.dados\"', '      context_json: \\'{\"session.cpf\": \"{{\$.pipeline_state.contato}}\"}\\'', 1)"
  muta "nó do roteiro some da forma" infra/dialog/dialog_limite_roteiro.json \
    "s.replace('\"id\": \"confirmar_recebimento\"', '\"id\": \"confirmar_recebimento_x\"', 1)"
  muta "choice volta a não ler config" packages/skill-flow-engine/src/steps/choice.ts \
    "s.replace('    config:         ctx.config ?? {},\n', '', 1)"
  muta "choice volta a ler journey na sessão" packages/skill-flow-engine/src/steps/choice.ts \
    "s.replace('contextStore.get(naJourney ? \`journey:\${ctx.journeyId}\` : ctx.sessionId', 'contextStore.get(ctx.sessionId', 1)"
  muta "retorno volta a ler da sessão" packages/skill-flow-engine/skills/skill_limite_retorno_v1.yaml \
    "s.replace('@ctx.journey.resultado', '@ctx.session.resultado', 1)"
fi

echo ""
echo "── B · AO VIVO ────────────────────────────────────────────────────────"
if ! curl -sf "$REG/v1/health" >/dev/null 2>&1; then
  incon "agent-registry inalcançável em $REG"
else
  SLOT=$(curl -s "$REG/v1/pools/limite_ia/slots" -H "x-tenant-id: $TENANT")
  V=$(printf '%s' "$SLOT" | python3 -c 'import json,sys,yaml
d = json.load(sys.stdin); cur = (d.get("slots") or d).get("current") or {}
r = yaml.safe_load(open("packages/skill-flow-engine/skills/skill_intake_runner_v1.yaml", encoding="utf-8"))
obrig = [p["key"] for p in r["config_params"] if p.get("required")]
cfg = cur.get("config_json") or {}
snap = json.dumps(cur.get("yaml_snapshot") or {})
print(json.dumps({"skill": cur.get("skill_id"), "faltando": [k for k in obrig if not cfg.get(k)],
                  "context_json": "context_json" in snap, "form": cfg.get("dialog_form_id")}))' 2>/dev/null)
  if [ "$(jexpr "$V" "d['skill']")" != "skill_intake_runner_v1" ]; then
    falha "o slot current do limite_ia não roda o runner: $V"
  elif [ "$(jexpr "$V" "not d['faltando'] and not d['context_json']")" = "True" ]; then
    ok "slot current do limite_ia: runner, config completa, sem context_json no snapshot"
  else
    falha "slot current do limite_ia sujo: $V"
  fi

  FORM=$(jexpr "$V" "d['form']")
  PUB=$(curl -s "$DIALOG/v1/dialog/forms/$FORM?status=published" -H "X-Tenant-ID: $TENANT")
  AUS=$(printf '%s' "$PUB" | python3 -c 'import json,sys,re
d = json.load(sys.stdin); nos = {n.get("id") for n in d.get("nodes", [])}
txt = open("packages/skill-flow-engine/skills/skill_intake_runner_v1.yaml", encoding="utf-8").read() + \
      open("packages/skill-flow-engine/skills/skill_limite_continuidade_v1.yaml", encoding="utf-8").read()
refs = set(re.findall(r"render\.by_node\.([a-z0-9_]+)", txt))
print(json.dumps(sorted(refs - nos)) if nos else "ILEGIVEL")' 2>/dev/null)
  case "$AUS" in
    "[]")      ok "a forma PUBLICADA ($FORM) tem todos os nós que runner e continuidade referenciam" ;;
    ILEGIVEL|"") incon "não consegui ler a forma publicada $FORM" ;;
    *)         falha "nós referenciados ausentes da forma publicada $FORM: $AUS" ;;
  esac

  source infra/test/_intake_runner_exercise.sh
  INJ='4111111111111111", "session.cpf": "52900000000", "x": "1'
  if ! ir_access1 "$INJ"; then
    incon "acesso 1 não montou: $IR_ERRO"
  else
    CPF_N3=$(jexpr "$IR_CTX" "d.get('session.cpf')")
    CARTAO=$(jexpr "$IR_CTX" "d.get('session.numero_cartao')")
    if [ "$CPF_N3" = "$IR_CPF" ] && [ "$CARTAO" = "$INJ" ]; then
      ok "acesso 1: o processo nasceu com o CPF de quem provou ($IR_CPF) e o texto injetado chegou literal"
    else
      falha "acesso 1: session.cpf=$CPF_N3 (esperado $IR_CPF) · numero_cartao=[$CARTAO]"
    fi
  fi
fi

echo ""
if [ "$FALHA" -gt 0 ]; then echo "VEREDICTO: FALHA ($FALHA)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo "VEREDICTO: INCONCLUSIVO ($INCONCL)"; exit 2; fi
echo "VEREDICTO: OK — a porta é de plataforma, e o pedido nasce sob quem provou"
exit 0
