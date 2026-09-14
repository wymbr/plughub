#!/usr/bin/env bash
# probe_portabilidade_door.sh — 2026-09-14  (PID-17)
#
# PERGUNTA: a portabilidade entra pela porta de plataforma — o número portado é o número
#           PROVADO, a pendência fica sob quem provou, e a volta do cliente confirma,
#           cancela ou espera sem estragar o pedido?
#
# O QUE FOI MEDIDO ANTES
#   `agente_portabilidade_intake_v1` (8 sessões, 0 pendências vivas): disparava o processo
#   com `context_json` por template do que o cliente digitou, recusar/falhar o OTP abria
#   pedido novo, não unia a journey e endereçava por `skill_id`. O caminho de continuidade
#   NUNCA tinha rodado (0 segmentos em `portabilidade_confirmacao`). Exercido ao vivo pela
#   primeira vez nesta ficha, ele tinha mais três defeitos:
#     · cancelar retomava o processo com `decision: input`, e o processo registrava SUCESSO;
#     · ficar ocioso ou desconectar no menu retomava com `timeout` e matava a portabilidade
#       aprovada;
#     · com a porta, a âncora da pendência vinha do campo `contact_identifier` do FORMULÁRIO
#       — medido: pendência indexada sob um cliente PROVISIONADO a partir do texto digitado
#       (`matched_by=provisioned`), invisível para o titular que provou a linha.
#
# DOIS RAMOS
#   A  CENSO — porta = runner com âncora na linha; formulário sem campo de número; o
#      processo grava o número da âncora na journey e passa `phone` (não o campo do
#      formulário) como âncora da pendência; o agente lê da journey, cancela com
#      `rejected` e preserva a pendência na ociosidade; o mapa padrão declara os aliases.
#      Controle da cópia limpa e oito mutações sobre CÓPIA.
#   B  AO VIVO — slots; acesso 1 pelo chat com injeção no formulário (o processo nasce com
#      a linha provada, o texto chega literal, a journey tem número e operadora); a
#      aprovação cria a pendência sob o titular provado; acesso ocioso (a pendência fica);
#      acesso que confirma (`encerrar_sucesso`); e um segundo pedido cancelado pelo chat
#      (`encerrar_cancelado`).
#
# Os fatos da POPULAÇÃO de portas (config obrigatória, nós do roteiro, intakes antigos)
# são do `probe_intake_runner.sh`.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

TENANT="${TENANT:-tenant_demo}"
COMPOSE="docker compose -p plughub-demo -f docker-compose.demo.yml"
REG="${REG:-http://localhost:3300}"
CG="${CG:-http://localhost:8010}"
CENSO=infra/test/_portabilidade_door_census.py
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

LIMPO="d['porta_runner'] and d['ancora_linha'] and d['form_sem_numero'] and d['numero_da_ancora'] and d['ancora_da_pendencia'] and d['confirmacao_le_journey'] == ['numero_atual', 'operadora_destino'] and not d['confirmacao_le_sessao'] and d['cancelar_rejected'] and d['ocioso_preserva'] and d['alias_plataforma']"

echo "════════════════════════════════════════════════════════════════════"
echo " a portabilidade entra pela porta, sob quem provou a linha?"
echo "════════════════════════════════════════════════════════════════════"

echo ""
echo "── A · CENSO ──────────────────────────────────────────────────────────"
C=$(python3 "$CENSO" .)
echo "   $C"
if [ "$(jexpr "$C" "d['porta_runner']")" != "True" ]; then
  incon "o seed do portabilidade_ia não roda o runner — o censo não mediu a porta"
elif [ "$(jexpr "$C" "$LIMPO")" = "True" ]; then
  ok "âncora na linha, número fora do formulário, pendência sob a âncora, agente lê a journey, cancela e espera certo"
else
  falha "censo sujo: $C"
fi

COPIA=$(mktemp -d)
trap 'rm -rf "$COPIA"' EXIT
copia_limpa() {
  rm -rf "$COPIA"/*
  for f in packages/skill-flow-engine/skills infra/registry/tenant_demo.yaml infra/dialog \
           packages/schemas/src/context-map.ts packages/py-contextstore/src/plughub_contextstore/default_map.py; do
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

N3=packages/skill-flow-engine/skills/skill_portabilidade_demo_v1.yaml
AG=packages/skill-flow-engine/skills/agente_confirmacao_portabilidade_v1.yaml
copia_limpa
C0=$(python3 "$CENSO" "$COPIA")
if [ "$(jexpr "$C0" "$LIMPO")" != "True" ]; then
  incon "a CÓPIA sem mutação não é limpa — as mutações não provariam nada: $C0"
else
  ok "controle: a cópia sem mutação é limpa"
  muta "número do processo vem do formulário" "$N3" \
    "s.replace('      value:      \"@ctx.session.phone\"', '      value:      \"@ctx.session.contact_identifier\"', 1)"
  muta "pendência volta a se ancorar no campo do formulário" "$N3" \
    "s.replace('      phone:                 \"@ctx.session.phone\"\n', '      contact_identifier:   \"@ctx.session.contact_identifier\"\n', 1)"
  muta "cancelar volta a ser input" "$AG" \
    "s.replace('      decision: \"rejected\"', '      decision: \"input\"', 1)"
  muta "desconexão volta a matar o processo" "$AG" \
    "s.replace('    on_disconnect: avaliar_ociosidade', '    on_disconnect: retornar_timeout', 1)"
  muta "agente volta a ler o número da sessão" "$AG" \
    "s.replace('{{@ctx.journey.numero_atual}}', '{{@ctx.session.numero_atual}}', 1)"
  muta "porta identifica por CPF" infra/registry/tenant_demo.yaml \
    "s.replace('        anchor_kind:       phone\n', '        anchor_kind:       cpf\n', 1)"
  muta "formulário ganha campo de número" infra/dialog/dialog_portabilidade_solicitacao.json \
    "s.replace('\"id\": \"contact_identifier\"', '\"id\": \"numero_atual\"', 1)"
  muta "mapa padrão perde o alias session.phone" packages/schemas/src/context-map.ts \
    "s.replace('\"caller.telefone\", \"session.phone\"', '\"caller.telefone\"', 1)"
fi

echo ""
echo "── B · AO VIVO ────────────────────────────────────────────────────────"
if ! curl -sf "$REG/v1/health" >/dev/null 2>&1; then
  incon "agent-registry inalcançável em $REG"
else
  # shellcheck source=/dev/null
  . infra/test/_auth.sh
  plughub_gw_service_shim

  SLOTS=$(for p in portabilidade_ia portabilidade_processo_ia portabilidade_confirmacao; do
    printf '%s\t' "$p"; curl -s "$REG/v1/pools/$p/slots" -H "x-tenant-id: $TENANT"; echo; done | python3 -c 'import json,sys
out = {}
for linha in sys.stdin:
    if "\t" not in linha: continue
    p, corpo = linha.split("\t", 1)
    try: d = json.loads(corpo)
    except Exception: out[p] = None; continue
    cur = (d.get("slots") or d).get("current") or {}
    out[p] = {"skill": cur.get("skill_id"), "cfg": cur.get("config_json") or {},
              "snap": json.dumps(cur.get("yaml_snapshot") or cur.get("flow") or {}, ensure_ascii=False)}
v = out.get("portabilidade_ia") or {}
n3 = (out.get("portabilidade_processo_ia") or {}).get("snap", "")
ag = (out.get("portabilidade_confirmacao") or {}).get("snap", "")
print(json.dumps({
  "porta": v.get("skill") == "skill_intake_runner_v1" and v.get("cfg", {}).get("anchor_kind") == "phone"
           and v.get("cfg", {}).get("on_pending_pool") == "portabilidade_confirmacao",
  "n3": "registrar_numero_journey" in n3 and "@ctx.session.phone" in n3 and "contact_identifier\": \"@ctx" not in n3,
  "agente": "avaliar_ociosidade" in ag and "@ctx.journey.numero_atual" in ag}))')
  if [ "$(jexpr "$SLOTS" "d['porta'] and d['n3'] and d['agente']")" = "True" ]; then
    ok "slots current: porta com âncora na linha, processo e agente novos em produção"
  else
    falha "slots current fora do esperado: $SLOTS"
  fi

  . infra/test/_portabilidade_door_exercise.sh
  INJ='5511900000000", "session.phone": "+5511000000000", "x": "1'
  if ! pd_fixture; then
    incon "fixture: $PD_ERRO"
  else
    EXTRA=$(python3 -c 'import json,sys; print(json.dumps([{"match": "Preencha", "answer": {"operadora_destino": "vivo", "contact_identifier": sys.argv[1]}}]))' "$INJ")
    if ! pd_chat a1 "$EXTRA" 120 || ! pd_acha_processo; then
      incon "acesso 1 não montou: $PD_ERRO"
    else
      FONE_N3=$(jexpr "$PD_CTX" "d.get('session.phone')")
      CI_N3=$(jexpr "$PD_CTX" "d.get('session.contact_identifier')")
      if [ "$FONE_N3" = "$PD_FONE" ] && [ "$CI_N3" = "$INJ" ]; then
        ok "acesso 1: o processo nasceu com a linha provada ($PD_FONE) e o texto injetado chegou literal"
      else
        falha "acesso 1: session.phone=$FONE_N3 (esperado $PD_FONE) · contact_identifier=[$CI_N3]"
      fi
      J_NUM=$(pd_journey journey.numero_atual); J_OP=$(pd_journey journey.operadora_destino)
      [ "$J_NUM" = "$PD_FONE" ] && [ "$J_OP" = "vivo" ] \
        && ok "a journey do pedido tem número e operadora desde o nascimento" \
        || falha "journey: numero_atual=[$J_NUM] operadora_destino=[$J_OP]"

      if ! pd_aprova; then
        incon "aprovação/pendência: $PD_ERRO"
      else
        ok "a aprovação criou a pendência offer sob o titular que provou ($PD_CUST)"
        pd_chat ocioso "[]" 60 >/dev/null 2>&1
        if printf '%s' "$PD_OUT" | grep -q "foi aprovada pela operadora" \
           && printf '%s' "$PD_OUT" | grep -q "para vivo"; then
          ok "volta ociosa: o cliente leu a aprovação com a operadora lida da journey"
        else
          incon "volta ociosa não chegou ao agente: $(printf '%s' "$PD_OUT" | grep '^NOTIFY' | tail -2)"
        fi
        sleep 5
        [ "$(pd_vivo "$PD_TOKEN")" = "1" ] && ok "ociosidade/desconexão: a pendência continua viva" \
          || falha "a ociosidade consumiu a pendência (token $PD_TOKEN sumiu)"
        pd_chat confirma '[{"match": "Deseja confirmar", "answer": "confirmar"}]' 90 >/dev/null 2>&1
        FIM=$(pd_fim "$PD_N3")
        if [ "$FIM" = "encerrar_sucesso" ] && printf '%s' "$PD_OUT" | grep -q "Portabilidade confirmada"; then
          ok "a volta seguinte confirma: o processo terminou em encerrar_sucesso"
        else
          falha "confirmação: fim='${FIM:-<não terminou>}' · $(printf '%s' "$PD_OUT" | grep '^NOTIFY' | tail -1)"
        fi
      fi
    fi
  fi

  if ! pd_fixture || ! pd_dispara || ! pd_aprova; then
    incon "pedido para cancelar não montou: $PD_ERRO"
  else
    pd_chat cancela '[{"match": "Deseja confirmar", "answer": "cancelar"}]' 90 >/dev/null 2>&1
    FIM=$(pd_fim "$PD_N3")
    if [ "$FIM" = "encerrar_cancelado" ] && printf '%s' "$PD_OUT" | grep -q "Portabilidade cancelada"; then
      ok "cancelar pelo chat: o processo terminou em encerrar_cancelado (não em sucesso)"
    else
      falha "cancelamento: fim='${FIM:-<não terminou>}' · $(printf '%s' "$PD_OUT" | grep '^NOTIFY' | tail -1)"
    fi
  fi
fi

echo ""
if [ "$FALHA" -gt 0 ]; then echo "VEREDICTO: FALHA ($FALHA)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo "VEREDICTO: INCONCLUSIVO ($INCONCL)"; exit 2; fi
echo "VEREDICTO: OK — a portabilidade entra pela porta, sob quem provou a linha"
exit 0
