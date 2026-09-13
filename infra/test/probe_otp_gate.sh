#!/usr/bin/env bash
# probe_otp_gate.sh — 2026-09-13  (PID-10)
#
# PERGUNTA: o OTP so e emitido contra ancora ENTREGAVEL e de procedencia
#           AUTORITATIVA para o cliente que pede — recusando de forma explicita?
#
# O DEFEITO QUE O ORIGINOU (ADR adr-identity-door-evidence D8)
#   `OtpService.challenge` aceitava qualquer kind e devolvia `sent: true` sempre:
#   fora do modo dev a entrega era `TODO(prod)`, e o default do modo dev era LIGADO.
#   O `skill_limite_entrada_v1` desafiava `kind: cpf` — CPF nao tem para onde mandar
#   codigo, e "provar posse" dele era provar que se sabe o numero que se digitou. A
#   posse ficava DURAVEL no CPF: dali em diante, digitar aquele CPF entregava as
#   pendencias e o `resume_token` sem prova nenhuma. E o verify anexava a posse a
#   qualquer `customer_id` informado, nao ao do desafio.
#
# QUATRO RAMOS
#   A  CENSO DO PARQUE — o snapshot do slot `current` de todo pool (o que RODA, nunca
#      o YAML) e o `skill.flow` publicado (pool sem slot, e o proximo promote): todo
#      `otp_challenge` com kind entregavel e `customer_id`. Violacao REPROVA; leitor quebrado e INCONCLUSIVO. Informa, sem julgar, as ancoras
#      `possessed` de kind nao-entregavel ja gravadas (o legado).
#   B  EXERCICIO NA IMAGEM — o adaptador contra Postgres e Redis reais, e a rota do
#      processo que esta de pe, cada recusa com o seu CONTROLE que emite.
#   C  MUTACOES DO B — procedencia sempre autoritativa · verify pelo subject guardado
#      · cpf entregavel: cada uma derruba o SEU caso, e o controle anterior segue.
#   D  MUTACAO DO A — um snapshot sintetico que desafia cpf acende o censo.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
roda()  { docker exec -i "$GW" python - "$@" < infra/test/_otp_gate_exercise.py 2>/dev/null | tail -1; }
# $1 = json · $2 = expressao python sobre `d` (o dict)
expr()  { printf '%s' "$1" | python3 -c 'import json,sys
try:
    d = json.loads(sys.stdin.read())
    print(eval(sys.argv[1]))
except Exception as e:
    print("__ERRO__ %s" % e)' "$2"; }
caso()  { expr "$1" "d.get('casos', {}).get('$2')"; }

echo "════════════════════════════════════════════════════════════════════"
echo " OTP so contra ancora entregavel e autoritativa, com recusa explicita?"
echo "════════════════════════════════════════════════════════════════════"

# ── A ────────────────────────────────────────────────────────────────────────
echo ""
echo "── A · CENSO DO PARQUE (slot current + skill.flow) ────────────────────"
J=$(roda censo)
echo "   $J"
LF=$(expr "$J" "d.get('leitura_falhou')")
if [ -z "$J" ] || [ "${LF#__ERRO__}" != "$LF" ]; then
  incon "o exercicio nao devolveu JSON — nada se conclui"
elif [ "$LF" != "None" ]; then
  incon "o leitor do registry falhou ($LF) — zero violacoes NAO e resposta"
else
  NP=$(expr "$J" "d['pools']"); ND=$(expr "$J" "d['desafios']"); NV=$(expr "$J" "len(d['violacoes'])")
  if [ "$NV" != "0" ]; then
    falha "$NV desafio(s) de OTP vivos contra ancora nao-entregavel ou sem customer_id: $(expr "$J" "d['violacoes']")"
  elif [ "$ND" = "0" ]; then
    incon "nenhum otp_challenge nos $NP snapshots vivos nem nos skill.flow — o censo e VACUO"
  else
    ok "$ND desafio(s) de OTP ($NP snapshots vivos + skill.flow publicados), todos entregaveis e com customer_id"
  fi
  echo "  INFO    posse ja gravada em kind nao-entregavel (legado, nao julgado aqui): $(expr "$J" "d['posse_nao_entregavel']")"
fi

# ── B ────────────────────────────────────────────────────────────────────────
echo ""
echo "── B · EXERCICIO NA IMAGEM ────────────────────────────────────────────"
CASOS="cpf_autoritativo_recusa declarado_recusa autoritativo_de_outro_recusa sem_entrega_recusa autoritativo_emite verify_de_outro_nao_anexa verify_mesmo_cliente_possessed rota_viva_recusa_cpf rota_viva_emite"
J=$(roda exercicio)
echo "   $J" | cut -c1-600
if [ "$(expr "$J" "d.get('sem_fixture', False)")" = "True" ] || [ -z "$J" ]; then
  incon "a fixture (cliente importado) nao nasceu — nada se conclui"
else
  for k in $CASOS; do
    [ "$(caso "$J" "$k")" = "True" ] && ok "$k" || falha "$k"
  done
fi

# ── C ────────────────────────────────────────────────────────────────────────
echo ""
echo "── C · MUTACOES DO B ──────────────────────────────────────────────────"
muta() {  # $1 flag · $2 casos que TEM de cair · $3 controles que TEM de ficar
  local J; J=$(roda exercicio "$1")
  if [ -z "$J" ] || [ "$(expr "$J" "d.get('sem_fixture', False)")" = "True" ]; then
    incon "$1: sem fixture"; return
  fi
  for k in $2; do
    [ "$(caso "$J" "$k")" = "False" ] && ok "$1 derruba $k" || falha "$1 NAO derrubou $k — o caso nao mede a regra"
  done
  for k in $3; do
    [ "$(caso "$J" "$k")" = "True" ] && ok "$1 mantem o controle $k" || falha "$1 derrubou o controle $k — a mutacao quebrou outra coisa"
  done
}
muta --mutar-procedencia "declarado_recusa autoritativo_de_outro_recusa" "cpf_autoritativo_recusa autoritativo_emite"
# sob esta, o verify alheio CONSOME o desafio e move a ancora: os passos depois dele
# nao tem como passar, e por isso so o controle ANTERIOR e cobrado
muta --mutar-subject "verify_de_outro_nao_anexa" "autoritativo_emite declarado_recusa"
muta --mutar-entregavel "cpf_autoritativo_recusa" "declarado_recusa autoritativo_emite verify_mesmo_cliente_possessed"

# ── D ────────────────────────────────────────────────────────────────────────
echo ""
echo "── D · MUTACAO DO A ───────────────────────────────────────────────────"
J=$(roda censo --injetar)
if [ "$(expr "$J" "any(v['pool'] == '__injetado__' and v['kind'] == 'cpf' for v in d['violacoes'])")" = "True" ]; then
  ok "o snapshot sintetico que desafia cpf e acusado pelo censo"
else
  falha "o censo NAO acusou o desafio a cpf injetado — o ramo A nao pode reprovar"
fi

# ── veredicto ────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo " FALHA ($FALHA)"; exit 1
elif [ "$INCONCL" -gt 0 ]; then
  echo " INCONCLUSIVO ($INCONCL)"; exit 2
fi
echo " OK"
exit 0
