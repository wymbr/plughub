#!/usr/bin/env bash
# probe_context_visibility_selector.sh — GATE da D6 (arco ALLOWLIST, fase V5):
# a tela do pool vira SELETOR sobre os nos do mapa do ContextStore.
#
# O que a D6 conserta, e por que "a dica esta certa agora" nao serve de gate:
#   `context_visibility` era texto livre separado por virgula, e foi isso que deixou
#   a UI prometer namespaces que nao existem. Medido em 2026-08-29 havia QUATRO
#   copias da mesma afirmacao sobre o default, e elas discordavam:
#     · codigo      `DEFAULT_OPERATOR_NAMESPACES = ["service","session"]`  (autoridade)
#     · dica i18n   "Default: service, session"  — mas citando `history`, que nao existe
#     · placeholder "service, journey, session"  — `journey` nunca esteve no default
#     · docstring   `["service","journey","session"]` no schema
#   Corrigir os textos e o conserto que envelhece; o SELETOR e o mecanismo, porque a
#   lista deixa de ser escrita e passa a ser derivada. Logo o gate nao confere prosa:
#   confere que (a) nao ha como digitar, e (b) a lista vem do mapa.
#
# Seis ramos:
#   A. SEM TEXTO LIVRE  — o bloco da tela nao tem mais <input type="text">
#   B. DERIVADA         — o endpoint serve o vocabulario e diz `source`
#   C. FANTASMAS FORA   — `service`/`history` NAO sao ofertaveis (testemunha do B)
#   D. LEGADO SOBREVIVE — o componente trata valor fora do mapa (nao descarta calado)
#   E. LIMPEZA EXISTE   — `context_visibility` aceita null e o update mapeia DbNull
#   F. i18n             — chaves novas nas DUAS locales, e nenhuma string solta no .tsx
#
# Ramo C e a testemunha do B: um endpoint que devolvesse lista VAZIA passaria no B
# ("respondeu") e no C ("nao tem fantasma") — por isso o C exige tambem que os
# namespaces REAIS estejam la. Ausencia sobre ausencia nao e aprovacao.
#
# Ramo E existe porque o seletor tornou o gesto de LIMPAR natural (basta remover os
# chips), e ate 2026-08-29 nao havia caminho de limpeza: o corpo omitia a chave, o
# valor antigo permanecia, e a tela exibia vazio um pool que continuava com politica.
#
# Tres estados: OK / FALHA / INCONCLUSIVO (nunca OK com ramo inconclusivo).
# ⚠️ UTF-8 explicito na saida do python — e esta linha E o conserto, nao um paliativo.
#
# Nesta bancada o `stdout` do python usa cp1252. Um `print` com acento sai em bytes
# cp1252, e todo consumidor a jusante — `grep` com padrao UTF-8, outro python, o proprio
# shell — deixa de casar sobre um texto que ESTA la. Medido com A/B em 2026-09-02
# (CNS-18): sem a env, `grep -c 'meta NAO escrito'` devolve 0 pelos DOIS caminhos
# (arquivo e variavel); com a env, devolve 1 pelos dois.
#
# ⚠️ O diagnostico levou TRES tentativas e as duas primeiras foram publicadas erradas:
# `sys.stdin` (CNS-12) e a variavel de shell (CNS-17). Nao era o fluxo de ENTRADA nem o
# transporte — era a SAIDA. Variavel e arquivo sao ambos inocentes, e `docker logs`
# tambem: medido, sobrevive intacto pelos dois. Se voce for mexer nisto, o teste que
# separa as hipoteses e o A/B na propria env, com UMA variavel por vez.
export PYTHONIOENCODING=utf-8

set -u

cd "$(dirname "$0")/../.." || exit 2
REG="${REG:-http://localhost:3300}"
TENANT="${TENANT:-tenant_demo}"
PAGE="packages/platform-ui/src/modules/config-recursos/PoolsPage.tsx"
COMP="packages/platform-ui/src/modules/config-recursos/ContextVisibilitySelect.tsx"
SCHEMAS="packages/schemas"

fail=0
inconclusive=0
bad()  { echo "  x $*"; fail=$((fail+1)); }
ok()   { echo "  v $*"; }
huh()  { echo "  ? $*"; inconclusive=$((inconclusive+1)); }

NODE_BIN="${NODE_BIN:-$HOME/.nvm/versions/node/v24.14.1/bin}"
[ -d "$NODE_BIN" ] && PATH="$NODE_BIN:$PATH"
export PATH

echo "=== probe_context_visibility_selector — D6 do arco ALLOWLIST ==="

# Recompila SEMPRE. Um `dist/` herdado faria os ramos que o leem julgarem uma
# versao anterior do schema — o simbolo existe e nao e o de agora.
if command -v node >/dev/null 2>&1; then
  ( cd "$SCHEMAS" && npx tsc >/tmp/cvsel_tsc.log 2>&1 ) || huh "tsc dos schemas reprovou — ver /tmp/cvsel_tsc.log"
else
  huh "node ausente — o ramo E nao pode rodar (defina NODE_BIN)"
fi

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "-- A. SEM TEXTO LIVRE --"
if [ ! -f "$PAGE" ]; then
  huh "A: $PAGE ausente"
else
  # O bloco da tela precisa usar o seletor, e nao pode ter voltado a input de texto
  # ligado ao estado de context_visibility.
  if grep -qE 'context_visibility_(ns|allow_tags)' "$PAGE" \
     && grep -nE '<input[^>]*type="text"' "$PAGE" | grep -q 'context_visibility'; then
    bad "A: ainda ha <input type=text> ligado a context_visibility"
  elif grep -q "ContextVisibilitySelect" "$PAGE"; then
    ok "A: o bloco usa ContextVisibilitySelect, sem campo de texto"
  else
    bad "A: a tela nao referencia ContextVisibilitySelect"
  fi
  # O placeholder que prometia `service, journey, session` nao pode voltar.
  if grep -q 'service, journey, session' "$PAGE"; then
    bad "A: o placeholder fantasma 'service, journey, session' voltou"
  else
    ok "A: placeholder fantasma ausente"
  fi
  # Estado precisa ser ARRAY — voltar a string separada por virgula reabre a porta.
  if grep -qE "context_visibility_ns: *'' as string" "$PAGE"; then
    bad "A: o estado voltou a ser string separada por virgula"
  else
    ok "A: o estado do formulario e array"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "-- B/C. DERIVADA do mapa + fantasmas FORA --"
OPTS=$(curl -sS --max-time 10 "$REG/v1/context-map/visibility-options" -H "x-tenant-id: $TENANT" 2>/dev/null)
if [ -z "$OPTS" ]; then
  huh "B: agent-registry inalcancavel em $REG"
else
  SRC=$(echo "$OPTS" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("source","?"))' 2>/dev/null || echo "?")
  NNS=$(echo "$OPTS" | grep -oE '"ns":"[a-z_]+"' | wc -l | tr -d ' ')
  NTG=$(echo "$OPTS" | grep -oE '"tag":"[a-z_.]+"' | wc -l | tr -d ' ')
  if [ "${NNS:-0}" -gt 0 ] && [ "${NTG:-0}" -gt 0 ]; then
    ok "B: vocabulario servido — $NNS namespaces, $NTG tags (source=$SRC)"
  else
    bad "B: vocabulario VAZIO (ns=$NNS tags=$NTG) — o seletor nao teria o que oferecer"
  fi
  [ "$SRC" = "config" ] \
    && ok "B: derivado do mapa do TENANT (config), nao do embutido" \
    || huh "B: source=$SRC — a lista veio do mapa EMBUTIDO; o vocabulario do tenant nao foi lido"

  # C — os dois fantasmas medidos (zero produtores em packages/ e no store vivo).
  gh=0
  for ph in service history; do
    echo "$OPTS" | grep -qE "\"ns\":\"$ph\"" && { bad "C: namespace fantasma OFERECIDO: $ph"; gh=1; }
  done
  [ "$gh" = "0" ] && ok "C: fantasmas (service, history) fora das opcoes"
  # Testemunha POSITIVA: os reais TEM de estar la, senao "sem fantasma" e so lista vazia.
  miss=""
  for real in session journey caller; do
    echo "$OPTS" | grep -qE "\"ns\":\"$real\"" || miss="$miss $real"
  done
  [ -z "$miss" ] \
    && ok "C: namespaces REAIS presentes (session, journey, caller) — nao e lista vazia" \
    || bad "C: namespace real AUSENTE:$miss — a lista esta vazia ou quebrada"
fi

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "-- D. LEGADO SOBREVIVE (nao e descartado calado) --"
if [ ! -f "$COMP" ]; then
  huh "D: $COMP ausente"
else
  d_ok=1
  # (a) o discriminador existe e (b) o aviso e RENDERIZADO — presenca da variavel
  # sozinha nao prova que alguem a usa.
  grep -q "known.has" "$COMP"                  || { bad "D: sem discriminador known.has"; d_ok=0; }
  grep -q "unknownSelected.length > 0" "$COMP" || { bad "D: o aviso de valor desconhecido nao e renderizado"; d_ok=0; }
  # (c) NEGATIVA, e e ela que pega a regressao REALISTA: "descartar em silencio" nao
  # se parece com codigo removido, e sim com um filtro a mais. Se `value` for
  # filtrado contra o mapa em qualquer ponto, o valor legado some do chip e do save.
  # A NEGACAO importa: `value.filter(v => !known.has(v))` e o calculo LEGITIMO da
  # lista de desconhecidos; `value.filter(v => known.has(v))` e o descarte. Uma
  # regex que nao distinguisse as duas reprovaria o proprio conserto — foi o que
  # aconteceu na primeira versao deste ramo.
  if grep -nE 'value\s*\.\s*filter\([^)]*=>\s*known\.has' "$COMP" >/dev/null 2>&1; then
    bad "D: `value` e FILTRADO contra o mapa — valor legado seria descartado em silencio"
    d_ok=0
  fi
  [ "$d_ok" = "1" ] && ok "D: desconhecido distinguido, avisado e NUNCA filtrado de value"
fi

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "-- E. LIMPEZA EXISTE (null limpa a politica) --"
if [ -f "$SCHEMAS/dist/agent-registry.js" ]; then
  E=$(cd "$SCHEMAS" && node -e '
    const { PoolRegistrationSchema } = require("./dist/agent-registry.js")
    const base = { pool_id: "p", tenant_id: "t" }
    const nul  = PoolRegistrationSchema.partial().safeParse({ context_visibility: null }).success
    const obj  = PoolRegistrationSchema.partial().safeParse({ context_visibility: { operator_namespaces: ["session"] } }).success
    console.log(JSON.stringify({ nul, obj }))
  ' 2>/dev/null)
  echo "$E" | grep -q '"nul":true' \
    && ok "E: o schema aceita null (ha caminho de limpeza)" \
    || bad "E: o schema RECUSA null — esvaziar o seletor volta a ser no-op"
  # Testemunha positiva: aceitar null nao pode ter afrouxado o objeto valido.
  echo "$E" | grep -q '"obj":true' \
    && ok "E: objeto valido segue aceito (testemunha positiva)" \
    || bad "E: o schema passou a recusar objeto valido — $E"
else
  huh "E: nao foi possivel compilar os schemas"
fi
# O schema aceitar null nao basta: a coluna Json do Prisma exige DbNull no update.
if grep -q "context_visibility:       body.context_visibility ?? Prisma.DbNull" packages/agent-registry/src/routes/pools.ts; then
  ok "E: o update mapeia null -> Prisma.DbNull"
else
  bad "E: o update passa null CRU a uma coluna Json — a limpeza nao chega ao banco"
fi

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "-- F. i18n --"
missing=""
for k in nsPlaceholder tagsPlaceholder unknownBadge unknownHint scopeBadge legacyBadge builtinWarning optionsEmpty search; do
  for loc in en pt-BR; do
    grep -q "\"$k\"" "packages/platform-ui/src/i18n/locales/$loc/configRecursos.json" || missing="$missing $loc/$k"
  done
done
[ -z "$missing" ] && ok "F: chaves novas presentes nas DUAS locales" || bad "F: chave i18n ausente:$missing"
# Nenhuma string visivel solta no componente: ele recebe TUDO por prop.
if grep -qE '>[A-Z][a-z]{3,}[^<]*<' "$COMP" 2>/dev/null; then
  bad "F: string visivel hardcoded no componente (deve vir por prop/t())"
else
  ok "F: componente sem string visivel hardcoded"
fi

# ─────────────────────────────────────────────────────────────────────────────
echo
if [ "$inconclusive" -gt 0 ]; then
  echo "INCONCLUSIVO ($inconclusive ramo(s) sem poder julgar, $fail falha(s))"
  exit 2
elif [ "$fail" -gt 0 ]; then
  echo "FALHA ($fail)"
  exit 1
else
  echo "OK — seletor derivado do mapa, fantasmas fora, legado preservado, limpeza possivel"
  exit 0
fi
