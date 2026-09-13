#!/usr/bin/env bash
# probe_identity_index_owner.sh — 2026-09-13  (IDN-10)
#
# PERGUNTA: o indice Redis de identidade aponta alguma ancora para um cliente
#           que o CADASTRO atribui a outro?
#
# O DEFEITO QUE O ORIGINOU
#   O Lookup 1 le o Redis PRIMEIRO. Dois escritores do indice apontavam ancora sem
#   perguntar ao cadastro: a identidade progressiva do caminho quente (anexava ao
#   vencedor as ancoras que eram miss NO REDIS, sob um comentario que prometia nao
#   tocar ancora de outro cliente) e a reidratacao do caminho `durable` (gravava
#   TODAS as ancoras da chamada apontando para o vencedor). O resolve devolvia o
#   cliente ERRADO para aquela ancora ate o TTL do indice (30 d) — e e esse id que
#   as pendencias e a retomada usam. Nada ficava vermelho.
#
#   Exposicao no dia do conserto: 4 ancoras no indice, 4 concordando com o
#   cadastro, 0 divergentes. O caminho existia; o dano medido era zero.
#
# QUATRO RAMOS
#   A  CENSO AO VIVO — varre o indice do tenant e compara com o cadastro. Divergente
#      REPROVA. Com indice vazio o ramo e VACUO e diz isso.
#   B  EXERCICIO NA IMAGEM — os dois caminhos contra Postgres e Redis reais, cada um
#      com o seu CONTROLE (ancora sem dono segue anexada: a identidade progressiva
#      nao pode ter morrido junto).
#   C  MUTACAO DO B — com a regra de escrita liberada, os dois casos reprovam e os
#      controles seguem; com a regra de empate antiga, o caso do empate reprova.
#   D  MUTACAO DO A — uma divergencia injetada acende o censo.
#
# IDN-11 (2026-09-13): o caminho frio escolhia o vencedor entre ancoras de
#    clientes DIFERENTES pela ORDEM na chamada, sem o `ambiguous` do caminho quente.
#    Hoje e UMA computacao de candidatos; o B exige `ambiguous` sem escrita no
#    empate a frio, e o C troca a regra de empate para provar que o caso a mede.
#
# EXIT: 0 OK · 1 FALHA · 3 SEM AMOSTRA

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
roda()  { docker exec -i "$GW" python - "$@" < infra/test/_identity_index_owner_exercise.py 2>/dev/null | tail -1; }
campo() { printf '%s' "$1" | python3 -c 'import json,sys
try: print(json.loads(sys.stdin.read()).get(sys.argv[1], ""))
except Exception: print("")' "$2"; }

echo "════════════════════════════════════════════════════════════════════"
echo " o indice Redis aponta ancora para quem o cadastro nao reconhece?"
echo "════════════════════════════════════════════════════════════════════"

# ── A ────────────────────────────────────────────────────────────────────────
echo ""
echo "── A · CENSO AO VIVO ──────────────────────────────────────────────────"
J=$(roda censo)
echo "   $J"
TOT=$(campo "$J" redis_ancoras); DIV=$(campo "$J" divergem)
case "$TOT$DIV" in
  *[!0-9]*|"") incon "censo sem leitura" ;;
  *)
    if [ "$DIV" != "0" ]; then falha "$DIV ancora(s) do indice apontam para cliente diferente do cadastro"
    elif [ "$TOT" = "0" ]; then ok "nenhuma divergencia — VACUAMENTE: o indice esta vazio; quem prova e o B"
    else ok "$TOT ancora(s) no indice, nenhuma divergente do cadastro"
    fi ;;
esac

# ── B / C ────────────────────────────────────────────────────────────────────
julga() {
  printf '%s' "$1" | python3 -c '
import json, sys
try:
    c = json.loads(sys.stdin.read())["casos"]
except Exception:
    print("SEM"); sys.exit()
esperado = json.loads(sys.argv[1])
if any(k not in c for k in esperado):
    print("SEM " + ",".join(k for k in esperado if k not in c)); sys.exit()
ruins = [k for k, v in esperado.items() if c[k] is not v]
print("OK" if not ruins else "FALHA " + ",".join(ruins))' "$2"
}

echo ""
echo "── B · EXERCICIO NA IMAGEM ────────────────────────────────────────────"
J=$(roda exercicio)
echo "   $J"
V=$(julga "$J" '{"progressiva_respeita": true, "progressiva_controle": true, "reidratacao_respeita": true, "reidratacao_controle": true, "empate_frio_ambiguo": true}')
case "$V" in
  OK)   ok "progressiva nao anexa ancora de outro dono · reidratacao nao reaponta a perdedora · ancora sem dono segue anexada nos dois · empate a frio e ambiguo e nao escreve (IDN-11)" ;;
  SEM*) incon "exercicio sem veredicto: ${V#SEM}" ;;
  *)    falha "${V#FALHA }" ;;
esac

echo ""
echo "── C · MUTACAO DO B ───────────────────────────────────────────────────"
J=$(roda exercicio --mutar)
V=$(julga "$J" '{"progressiva_respeita": false, "progressiva_controle": true, "reidratacao_respeita": false, "reidratacao_controle": true, "empate_frio_ambiguo": true}')
case "$V" in
  OK)   ok "com a regra de escrita liberada, os dois caminhos reapontam a ancora e reprovam; controles e empate seguem" ;;
  SEM*) incon "mutacao sem veredicto: ${V#SEM}" ;;
  *)    falha "a mutacao nao produziu o esperado — o exercicio nao mede a regra: ${V#FALHA } — $J" ;;
esac
J=$(roda exercicio --mutar-empate)
V=$(julga "$J" '{"progressiva_respeita": true, "reidratacao_respeita": true, "empate_frio_ambiguo": false}')
case "$V" in
  OK)   ok "com a regra de empate antiga, o empate a frio escolhe um vencedor e reprova (IDN-11); os outros seguem" ;;
  SEM*) incon "mutacao do empate sem veredicto: ${V#SEM}" ;;
  *)    falha "a mutacao do empate nao produziu o esperado: ${V#FALHA } — $J" ;;
esac

# ── D ────────────────────────────────────────────────────────────────────────
echo ""
echo "── D · MUTACAO DO A ───────────────────────────────────────────────────"
J=$(roda censo-mutado)
echo "   $J"
DIV=$(campo "$J" divergem)
# Injecao, censo e restauracao rodam no MESMO processo (`censo-mutado`), com a
# restauracao num `finally`. A primeira versao passava o valor anterior pelo shell
# e gravou a string "None" como dono de uma ancora real — ver o docstring la.
case "$DIV" in
  *[!0-9]*|"") incon "censo com divergencia injetada sem leitura (sem chave no cadastro?)" ;;
  0)           falha "divergencia injetada e o censo seguiu em 0 — o A nao pode reprovar" ;;
  *)           ok "divergencia injetada acende o censo ($DIV)" ;;
esac
J=$(roda censo)
DEPOIS=$(campo "$J" divergem)
[ "$DEPOIS" = "0" ] && ok "a injecao foi desfeita: censo de volta a 0" || falha "a injecao NAO foi desfeita: $J"

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " RESULTADO: FALHA em $FALHA verificacao(oes)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " RESULTADO: $INCONCL INCONCLUSIVO(s), nenhum vermelho"; exit 3; fi
echo " RESULTADO: OK"
exit 0
