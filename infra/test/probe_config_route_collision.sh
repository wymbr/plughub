#!/usr/bin/env bash
# probe_config_route_collision.sh — o proxy do platform-ui não pode transformar
# GET de NAMESPACE do config-api em HTML.
#
# ── O defeito que este gate existe para pegar (medido 2026-08-26) ──────────────
# O nginx do platform-ui serve as PÁGINAS do React Router sob /config/<nome>.
# `masking` é ao mesmo tempo nome de página E namespace do config-api, e as duas
# URIs são IDÊNTICAS — a query string NÃO entra no match de `location`. Resultado:
# `GET /config/masking?tenant_id=x` recebia `index.html` com HTTP 200, o `safeJson`
# do front lançava por content-type, `entries` ficava vazio e a tela de Masking
# dizia "0 regras configuradas" com 23 em vigor.
#
# ── Por que ele pode ficar VERMELHO por um motivo que ainda não existe ─────────
# A lista de páginas é lida do PRÓPRIO Dockerfile e a lista de namespaces vem do
# PRÓPRIO config-api. Um namespace novo que colida com um nome de página passa a
# reprovar sozinho, sem ninguém atualizar este arquivo.
#
# ── As duas direções, porque o conserto ingênuo quebra a outra ────────────────
# Remover a rota de SPA consertaria o fetch e QUEBRARIA a navegação para a página.
# Por isso há contra-teste: navegação (Accept: text/html) tem de continuar
# recebendo HTML.
#
# Estados: 0 = OK · 1 = FALHA · 2 = INCONCLUSIVO (nunca verde por ausência).
# Uso: bash infra/test/probe_config_route_collision.sh
# NÃO usa `set -e` de propósito: `VAR=$(curl …)` com serviço fora do ar mataria o
# script sem imprimir nada, e silêncio já passou por verde neste repositório.

UI_PORT="${UI_PORT:-5174}"
CFG_PORT="${CFG_PORT:-3600}"
TENANT="${TENANT:-tenant_demo}"
DF="${DF:-packages/platform-ui/Dockerfile}"

fail=0
note() { echo "  $*"; }

ctype() { # $1 = url, $2 = accept header
  curl -s -o /dev/null -m 10 -H "Accept: $2" -w '%{http_code} %{content_type}' "$1" 2>/dev/null
}

echo "═══ probe_config_route_collision — UI :$UI_PORT · config-api :$CFG_PORT ═══"

command -v jq >/dev/null || { echo "INCONCLUSIVO: jq ausente"; exit 2; }
[ -f "$DF" ] || { echo "INCONCLUSIVO: $DF não encontrado (rode da raiz do repo)"; exit 2; }

# ── 1. Fonte A: nomes de página, lidos do Dockerfile (não de uma cópia aqui) ───
PAGES=$(grep -o '/config/([a-z|]\+)' "$DF" | head -1 | sed 's|/config/(||; s|)||' | tr '|' ' ')
if [ -z "$PAGES" ]; then
  echo "INCONCLUSIVO: não consegui extrair a lista de páginas de $DF"
  note "esperava uma linha 'location ~ ^/config/(a|b|c)/?\$'"
  exit 2
fi
note "páginas SPA declaradas: $(echo "$PAGES" | wc -w)"

# ── 2. Fonte B: namespaces que o config-api realmente tem ─────────────────────
ALL=$(curl -s -m 10 "http://localhost:$CFG_PORT/config?tenant_id=$TENANT" 2>/dev/null)
NS=$(printf '%s' "$ALL" | jq -r '.config | keys[]' 2>/dev/null)
if [ -z "$NS" ]; then
  echo "INCONCLUSIVO: config-api :$CFG_PORT não devolveu namespaces (de pé? tenant $TENANT existe?)"
  exit 2
fi
note "namespaces no config-api: $(echo "$NS" | wc -l)"

# ── 3. Interseção — é ela que o defeito habita ────────────────────────────────
COLLIDE=""
for p in $PAGES; do
  if printf '%s\n' "$NS" | grep -qx "$p"; then COLLIDE="$COLLIDE $p"; fi
done
COLLIDE=$(echo "$COLLIDE" | tr -s ' ')

if [ -z "${COLLIDE// /}" ]; then
  echo "INCONCLUSIVO: nenhum nome de página é também namespace — este gate não julga nada hoje"
  note "não é verde: se a colisão sumiu por remoção de namespace, o defeito volta com o próximo"
  exit 2
fi
note "COLIDEM (nome de página × namespace):$COLLIDE"

# ── 4. O ramo que mede o defeito: fetch de namespace colidente tem de vir JSON ─
echo "── fetch de namespace (Accept: */*) — tem de ser application/json"
for ns in $COLLIDE; do
  r=$(ctype "http://localhost:$UI_PORT/config/$ns?tenant_id=$TENANT" '*/*')
  case "$r" in
    *application/json*) note "✓ $ns → $r" ;;
    "")                 note "✗ $ns → sem resposta (UI :$UI_PORT de pé?)"; fail=1 ;;
    *)                  note "✗ $ns → $r  ← COLISÃO DE ROTA: o SPA comeu a chamada de API"; fail=1 ;;
  esac
done

# ── 5. Testemunha de PRESENÇA: namespace que NÃO é página tem de vir JSON ──────
# Sem ela, um proxy inteiro fora do ar reprovaria com a mesma cara da colisão.
WITNESS=""
for n in $NS; do
  printf '%s\n' $PAGES | grep -qx "$n" || { WITNESS="$n"; break; }
done
echo "── testemunha de presença (namespace sem colisão)"
if [ -z "$WITNESS" ]; then
  note "INCONCLUSIVO neste ramo: todo namespace é também nome de página"
  fail=2
else
  r=$(ctype "http://localhost:$UI_PORT/config/$WITNESS?tenant_id=$TENANT" '*/*')
  case "$r" in
    *application/json*) note "✓ $WITNESS → $r" ;;
    *) note "✗ $WITNESS → $r  ← o proxy /config está quebrado em geral, não é colisão"; fail=1 ;;
  esac
fi

# ── 6. CONTRA-TESTE: navegação continua recebendo o SPA ───────────────────────
# O conserto ingênuo (apagar a rota de SPA) passaria no ramo 4 e reprovaria aqui.
echo "── contra-teste: navegação (Accept: text/html) tem de receber HTML"
for ns in $COLLIDE; do
  r=$(ctype "http://localhost:$UI_PORT/config/$ns" 'text/html,application/xhtml+xml')
  case "$r" in
    *text/html*) note "✓ $ns → $r" ;;
    "")          note "✗ $ns → sem resposta"; fail=1 ;;
    *)           note "✗ $ns → $r  ← a PÁGINA quebrou: o conserto derrubou a navegação"; fail=1 ;;
  esac
done

echo "─────────────────────────────────────────────────────────────"
case "$fail" in
  0) echo "GATE config/route-collision: OK"; exit 0 ;;
  2) echo "GATE config/route-collision: INCONCLUSIVO (ramo sem população)"; exit 2 ;;
  *) echo "GATE config/route-collision: FALHA"; exit 1 ;;
esac
