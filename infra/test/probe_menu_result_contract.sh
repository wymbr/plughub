#!/usr/bin/env bash
# probe_menu_result_contract.sh — 2026-09-07  (VOZ-03)
#
# PERGUNTA: os produtores de `menu_result` publicam a chave que o bridge LE?
#
# O DEFEITO
#   `sms.py` publicava `payload["answers"]`; o bridge le `payload["result"]`.
#   Ninguem lia `answers`. O collect sequencial de SMS entregava string VAZIA ao
#   skill — sem erro, sem log, sem vermelho. O `.get("result", "")` do bridge
#   devolvia o default, e o `choice` seguinte comparava contra "" e caia no ramo
#   errado como se o cliente tivesse respondido isso.
#
#   ⚠️ E a suite estava VERDE por cima: o teste do SMS afirmava
#   `payload["answers"]["topic"] == "suporte"` — produtor e teste olhando um para
#   o outro, nenhum dos dois para o consumidor. Um contrato de payload nao mora
#   em nenhum dos lados: mora ENTRE eles, e por isso nao ha arquivo onde um
#   `grep` o encontre. Foi preciso um censo dos DOIS lados.
#
# DOIS RAMOS
#   A  CONTRATO — a chave e MEDIDA no leitor (nunca escrita aqui como constante:
#      isso mediria a concordancia com o gate, e trocar a chave no bridge
#      deixaria os quatro produtores verdes contra um leitor que mudou). Mais de
#      um leitor com chaves diferentes tambem reprova — seria o mesmo defeito do
#      lado do consumidor.
#   B  MUTACAO — renomeia a chave num produtor e exige que o censo acuse.
#   C  METADADO (WCH-11) — `interaction` e `via` nao sao o contrato do VALOR: produtor antigo
#      legitimamente nao os manda. A exigencia e mais fraca e ainda assim mecanismo: todo
#      metadado lido pelo bridge (`_menu_meta`) e publicado por ALGUM produtor.
#   D  MUTACAO — renomeia o metadado no canal e exige que o censo acuse.
#
# EXIT: 0 OK · 1 FALHA · 3 SEM AMOSTRA

set -uo pipefail
cd "$(dirname "$0")/../.."

HELPER="infra/test/_menu_result_contract.py"
FALHA=0
INCONCL=0

rodar() {
  rotulo="$1"
  modo="$2"
  echo ""
  echo "── $rotulo ─────────────────────────────────────────────────────────"
  rc=0
  python3 "$HELPER" "$modo" || rc=$?
  case "$rc" in
    0) ;;
    3) INCONCL=$((INCONCL + 1)) ;;
    *) FALHA=$((FALHA + 1)) ;;
  esac
}

echo "════════════════════════════════════════════════════════════════════"
echo " menu_result — produtor e leitor falam a MESMA chave?"
echo "════════════════════════════════════════════════════════════════════"

rodar "A · CONTRATO — a chave vem do leitor, e todo produtor a carrega" contrato
rodar "B · MUTACAO  — o censo acusa um produtor renomeado"             contrato-mut
rodar "C · METADADO — todo metadado lido pelo bridge tem produtor"     metadado
rodar "D · MUTACAO  — o censo acusa um metadado renomeado"            metadado-mut

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo " RESULTADO: FALHA em $FALHA ramo(s)"
  exit 1
fi
if [ "$INCONCL" -gt 0 ]; then
  echo " RESULTADO: $INCONCL ramo(s) INCONCLUSIVO(s), nenhum vermelho"
  exit 3
fi
echo " RESULTADO: OK"
exit 0
