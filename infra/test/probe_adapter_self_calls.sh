#!/usr/bin/env bash
# probe_adapter_self_calls.sh — 2026-09-07  (VOZ-03)
#
# PERGUNTA: todo `self.X(...)` chamado num adapter de canal existe no MRO?
#
# O DEFEITO QUE O ORIGINOU
#   `voice.py` chamava SEIS metodos que nao existiam em lugar nenhum —
#   `_publish_inbound`, `_normalize_text`, `_normalize_menu_result`,
#   `_open_session`, `_route_inbound`, `_close_session`. Medido contra a IMAGEM
#   construida, nao contra o fonte: `hasattr` era `False` nos seis, e o MRO
#   (`VoiceAdapter -> ChannelAdapter -> ABC -> object`) nao tinha nenhum.
#
#   O caminho de entrada INTEIRO do canal de voz nunca publicou nada: transcricao
#   de STT, evento de gravacao e resultado de coleta morriam no mesmo
#   `AttributeError`, dentro de um `except Exception` largo que o reportava como
#   `logger.debug("voice media WS receive loop ended")` -- fim NORMAL do laco, em
#   nivel debug. Nada ficava vermelho.
#
#   ⚠️ E a suite nao pegava porque MOCKAVA os inexistentes: `adapter._normalize_text
#   = MagicMock()` sob o comentario *"Mock inherited base methods"* -- uma
#   afirmacao falsa que a propria atribuicao tornava verdadeira dentro do teste.
#   E o *teste que nao pode reprovar* na forma mais pura: prova a CHAMADA e
#   esconde a AUSENCIA. Um mock nao verifica que o alvo existe; ele o CRIA.
#
# DOIS RAMOS
#   A  CENSO — AST sobre todos os adapters. So conta `self.NOME(...)` DIRETO
#      (`self.attr.metodo()` e chamada no atributo, nao no adapter), e o universo
#      de nomes validos e a uniao de metodos da classe, das bases resolvidas no
#      pacote, e de todo `self.NOME = ...`. Medido: 217 chamadas, 3 orfas -- zero
#      falso positivo em seis adapters.
#      ⚠️ As 3 restantes sao DIVIDA DECLARADA com dono (VOZ-03, metade restante):
#      o ciclo de vida de SESSAO do voice foi escrito contra uma API de
#      classe-base que nunca existiu, e consertar e decidir como uma chamada PSTN
#      abre sessao, roteia e fecha -- nao e "definir tres metodos". A tabela nao
#      envelhece: nome que sai do fonte e continua na divida tambem REPROVA.
#   B  MUTACAO — injeta uma chamada a metodo inexistente e exige que o censo acuse.
#
# ⚠️ Por que AST e nao `hasattr` na imagem: o `hasattr` responde por UMA classe de
#    cada vez e exige a imagem de pe; o censo responde pela populacao inteira e
#    roda no repositorio, que e onde a orfa nasce.
#
# EXIT: 0 OK · 1 FALHA · 3 SEM AMOSTRA

set -uo pipefail
cd "$(dirname "$0")/../.."

HELPER="infra/test/_adapter_self_calls.py"
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
echo " adapters — todo \`self.X(...)\` existe no MRO?"
echo "════════════════════════════════════════════════════════════════════"

rodar "A · CENSO    — orfa nova reprova; divida declarada e CONTADA" censo
rodar "B · MUTACAO  — o censo acusa um metodo inexistente"           censo-mut

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
