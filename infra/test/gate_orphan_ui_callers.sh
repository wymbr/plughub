#!/usr/bin/env bash
# gate_orphan_ui_callers.sh — nenhuma tela chama endpoint DURAMENTE deprecado
#
# POR QUE ESTE GATE EXISTE. Dois casos na mesma semana, com a mesma forma:
#
#   2026-08-05  `POST /api/force-complete/:sessionId` — as duas chamadas da UI iam
#               SEM `Authorization` num endpoint que exige `supervisor|admin`, e
#               tomavam 401 antes de chegar ao handler. A lacuna descrevia o
#               handler; o defeito estava no CAMINHO.
#   2026-08-07  `POST /v1/workflow/instances/{id}/cancel` — 410 hard, QUATRO telas
#               chamando, `catch { alert(String(e)) }`, e a mensagem do 410
#               apontava um substituto (`DELETE /v1/channels/webhook/{sid}`) que
#               nunca foi construído.
#
# O que os dois têm em comum não é o status: é que **ninguém conferiu quem chama**.
# Um endpoint deprecado sozinho é higiene; deprecado COM chamador vivo é um botão
# que promete uma ação e entrega um erro opaco. Este gate torna o segundo caso
# vermelho no dia, em vez de descoberto meses depois.
#
# ─── DESENHO: estático, não dinâmico ──────────────────────────────────────────
# Sondar os endpoints ao vivo seria pior: exigiria stack de pé, credenciais por
# rota, e um POST de teste teria EFEITO COLATERAL. O gate lê o CÓDIGO dos dois
# lados e cruza.
#
# ─── O que conta como "duramente deprecado" (e o que NÃO conta) ───────────────
# Conta: `@router.post("...", status_code=410)` / `501` — o status declarado no
# decorador é INCONDICIONAL por construção: a rota nunca faz outra coisa.
#
# NÃO conta — e a distinção é o coração do gate:
#   · `raise HTTPException(410, …)` dentro de um `if` (ex.: upload_router.py:135,
#     "attachment expired") é resposta CORRETA a uma condição, não rota morta.
#   · o 501 do ramo 2 do `force-complete` é o padrão que a I5 estabeleceu como
#     BOM — *"um 501 que NOMEIA a ausência vale mais que um flag falso"*. Marcar
#     isso como defeito ensinaria a esconder a ausência, que é o oposto do que a
#     § Postura pede.
# Ou seja: o gate persegue rota que NUNCA funciona, nunca resposta condicional.
#
# ─── Contador-testemunha ──────────────────────────────────────────────────────
# "Zero chamadores órfãos" só significa alguma coisa se o detector estiver
# detectando. Se o gate achar ZERO rotas duras, ele não passa verde — declara
# INCONCLUSIVO, porque nesse caso o zero mede o próprio gate, não o código.
#
# PREVISÃO, escrita antes de rodar (método § 4). Autor: sessão 2026-08-07,
# CONTADA por grep, não estimada:
#   · rotas duras encontradas: **5** (persist-suspend, complete, fail,
#     collect/persist, collect/respond — todas em workflow-api/router.py)
#   · chamadores órfãos na platform-ui: **0** (o /cancel, que era o 6º, saiu
#     nesta mesma mudança)
#   ⇒ verde. Se vier 6 rotas, o /cancel voltou; se vier > 0 órfãos, achamos o
#     terceiro caso da família.
#
# Uso:  bash infra/test/gate_orphan_ui_callers.sh
# Pré:  nenhuma — roda sobre o repositório, sem stack.
# Saída: 0 = nenhum chamador órfão · 1 = achou chamador órfão · 2 = INCONCLUSIVO.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UI="$ROOT/packages/platform-ui/src"

echo "══ gate: chamador de endpoint duramente deprecado na platform-ui ══"
echo

if [ ! -d "$UI" ]; then
  echo "⚠️  INCONCLUSIVO — não achei $UI. O gate não leu nada."
  exit 2
fi

# ── 1. Rotas duras (decorador com status_code=410|501) ───────────────────────
DEAD=$(grep -rhoE '@(router|app)\.(post|get|put|delete|patch)\("([^"]+)"[^)]*status_code=(410|501)' \
         "$ROOT/packages" --include='*.py' 2>/dev/null \
       | sed -E 's/.*\("([^"]+)".*/\1/' | sort -u)

DEAD_N=$(printf '%s\n' "$DEAD" | grep -c . || true)

echo "── rotas duramente deprecadas encontradas: ${DEAD_N} ─────────────────────"
printf '%s\n' "$DEAD" | sed 's/^/   /'
echo

# Contador-testemunha: sem rotas duras, o gate não está detectando nada.
if [ "${DEAD_N:-0}" -eq 0 ]; then
  echo "⚠️  INCONCLUSIVO — ZERO rotas duras detectadas."
  echo "    Isto NÃO é 'não há endpoint deprecado': é o detector sem detectar."
  echo "    Conferir se o padrão do decorador mudou antes de confiar num verde."
  exit 2
fi

# ── 2. Cruzar com o que a UI chama ───────────────────────────────────────────
# Casamento por PREFIXO literal + SUFIXO literal no MESMO arquivo. O caminho tem
# parâmetro no meio (`{instance_id}`) e a UI o interpola, então o caminho INTEIRO
# nunca aparece como string — casar o literal completo devolveria zero sempre, que
# é o modo de falha "portão que não pode reprovar".
ORPHANS=0
while IFS= read -r ROUTE; do
  [ -z "$ROUTE" ] && continue
  PREFIX="${ROUTE%%\{*}"                 # /v1/workflow/instances/
  SUFFIX="${ROUTE##*\}}"                 # /cancel   (vazio se não há parâmetro)

  if [ "$PREFIX" = "$ROUTE" ]; then
    # Rota sem parâmetro: o literal inteiro deve aparecer.
    HITS=$(grep -rn --include='*.ts' --include='*.tsx' -F "$ROUTE" "$UI" 2>/dev/null || true)
  else
    # Com parâmetro: arquivo que contenha o prefixo E o sufixo.
    FILES=$(grep -rl --include='*.ts' --include='*.tsx' -F "$PREFIX" "$UI" 2>/dev/null || true)
    HITS=""
    for F in $FILES; do
      [ -z "$SUFFIX" ] && continue
      H=$(grep -n -F "$SUFFIX" "$F" 2>/dev/null || true)
      [ -n "$H" ] && HITS="${HITS}${F}:${H}"$'\n'
    done
  fi

  if [ -n "$HITS" ]; then
    ORPHANS=$(( ORPHANS + 1 ))
    echo "❌ CHAMADOR ÓRFÃO — $ROUTE"
    printf '%s\n' "$HITS" | sed 's|^|     |' | sed "s|$UI/||"
    echo
  fi
done <<< "$DEAD"

# ── Veredicto ────────────────────────────────────────────────────────────────
echo "══ veredicto ══════════════════════════════════════════════════════════════"
if [ "$ORPHANS" -eq 0 ]; then
  echo "✅ VERDE — ${DEAD_N} rota(s) dura(s), nenhuma chamada por tela."
  echo "   (testemunha: o detector achou rotas, então o zero mede o código.)"
  exit 0
fi
echo "❌ VERMELHO — ${ORPHANS} rota(s) dura(s) com chamador vivo na platform-ui."
echo
echo "   Antes de 'consertar' apontando a tela para outro endpoint, fazer o"
echo "   levantamento que rendeu achados fora do enunciado nos dois casos"
echo "   anteriores: O QUE GRAVA · QUEM LÊ · QUEM CHAMA · COM O QUÊ."
echo "   Em particular: o substituto documentado EXISTE? (no caso do /cancel,"
echo "   não existia.) E o endereço que ele exige está preenchido no dado?"
exit 1
