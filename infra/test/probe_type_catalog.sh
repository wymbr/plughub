#!/usr/bin/env bash
# probe_type_catalog.sh — GATE da fase V2 (arco ALLOWLIST, adr-contextstore-allowlist §7)
#
# A exigência do ADR é de DOIS LADOS, e um lado só não julga:
#   · todo TIPO do catálogo é alcançável por algum mecanismo (detecção OU declaração
#     de tool via AuditPolicy.data_categories) — senão volta `iban`/`passport`;
#   · toda CATEGORIA do enum tem tipo declarado — o inverso, que um gate de presença
#     não pegaria.
#
# O veredicto NÃO reimplementa a regra: chama `verifyDataTypeCatalog`, exportado do
# próprio @plughub/schemas. Um gate que reconstrói a regra que julga testa a si mesmo.
#
# Ramo E é a testemunha do ORÁCULO: um catálogo sabidamente órfão TEM de reprovar.
# Sem ele, "0 órfãos" não distingue "catálogo limpo" de "verificador quebrado".
#
# Três estados: OK · FALHA · INCONCLUSIVO (nunca OK com ramo inconclusivo).
set -u

cd "$(dirname "$0")/../.." || exit 2
DC="${DC:-docker compose -f docker-compose.demo.yml}"
CFG="${CFG:-http://localhost:3600}"
TENANT="${TENANT:-tenant_demo}"
SVC="${SVC:-mcp-server-plughub}"
NODE_CWD="/app/packages/mcp-server-plughub"

fail=0
inconclusive=0
say()  { echo "  $*"; }
bad()  { echo "  ✗ $*"; fail=$((fail+1)); }
ok()   { echo "  ✓ $*"; }
huh()  { echo "  ? $*"; inconclusive=$((inconclusive+1)); }

echo "═══ probe_type_catalog — V2 do arco ALLOWLIST ═══════════════"

# ── P0. PREFLIGHT DE SÍMBOLO ─────────────────────────────────────────────────
# Nenhum serviço monta o fonte: sem este ramo, uma imagem antiga faria TODOS os
# outros ramos falharem pelo motivo errado.
echo
echo "── P0. preflight de símbolo (a imagem tem o catálogo?) ──────"
SYM="$($DC exec -T "$SVC" sh -c "cd $NODE_CWD && node -e \"const s=require('@plughub/schemas'); console.log([typeof s.verifyDataTypeCatalog, typeof s.DEFAULT_DATA_TYPE_CATALOG].join(','))\"" 2>&1 | tr -d '\r')"
case "$SYM" in
  function,object)
    ok "verifyDataTypeCatalog + DEFAULT_DATA_TYPE_CATALOG presentes na imagem"
    ;;
  *)
    huh "símbolos ausentes na imagem de ${SVC} (obtido: '${SYM}')"
    say "   ⇒ imagem anterior à V2. Rode: \$DC build ${SVC} && \$DC up -d ${SVC}"
    echo
    echo "VEREDICTO: INCONCLUSIVO — nada medido"
    exit 2
    ;;
esac

# ── A + B. lado do CÓDIGO ─────────────────────────────────────────────────────
echo
echo "── A. catálogo do código: dois lados ────────────────────────"
CODE="$($DC exec -T "$SVC" sh -c "cd $NODE_CWD && node -e \"const s=require('@plughub/schemas'); const v=s.verifyDataTypeCatalog(); console.log(JSON.stringify({d:v.declared,o:v.orphan_types,m:v.categories_without_type,ids:s.DEFAULT_DATA_TYPE_CATALOG.types.map(t=>t.id),rules:s.DEFAULT_MASKING_RULES.map(r=>r.category)}))\"" 2>&1 | tr -d '\r')"

DECLARED="$(echo "$CODE" | jq -r 'if has("d") then (.d|tostring) else "AUSENTE" end' 2>/dev/null)"
if [ "$DECLARED" = "AUSENTE" ] || [ -z "$DECLARED" ]; then
  huh "saída do node não é o JSON esperado: $(echo "$CODE" | head -c 200)"
  echo; echo "VEREDICTO: INCONCLUSIVO — nada medido"; exit 2
fi

N_ORPHAN="$(echo "$CODE"  | jq -r '.o | length')"
N_MISSING="$(echo "$CODE" | jq -r '.m | length')"
say "declarados=${DECLARED}   (testemunha de presença — zero sobre zero não é aprovação)"

if [ "$DECLARED" = "0" ]; then
  huh "catálogo VAZIO no código — os dois lados fechariam trivialmente"
  echo; echo "VEREDICTO: INCONCLUSIVO — nada medido"; exit 2
fi

if [ "$N_ORPHAN" = "0" ]; then
  ok "lado 1 — nenhum tipo órfão (todo tipo é alcançável)"
else
  bad "lado 1 — ${N_ORPHAN} tipo(s) órfão(s): $(echo "$CODE" | jq -r '.o | join(", ")')"
fi

if [ "$N_MISSING" = "0" ]; then
  ok "lado 2 — nenhuma categoria do enum sem tipo declarado"
else
  bad "lado 2 — ${N_MISSING} categoria(s) sem tipo: $(echo "$CODE" | jq -r '.m | join(", ")')"
fi

echo
echo "── B. DEFAULT_MASKING_RULES é DERIVADA, não redigitada ──────"
RULES="$(echo "$CODE" | jq -r '.rules | join(",")')"
DETECT="$($DC exec -T "$SVC" sh -c "cd $NODE_CWD && node -e \"const s=require('@plughub/schemas'); console.log(s.DEFAULT_DATA_TYPE_CATALOG.types.filter(t=>t.formato&&t.formato.detect_pattern).map(t=>t.id).join(','))\"" 2>&1 | tr -d '\r')"
if [ "$RULES" = "$DETECT" ]; then
  ok "regras == tipos detectáveis, na mesma ordem: ${RULES}"
else
  bad "regras (${RULES}) ≠ tipos detectáveis (${DETECT}) — a derivação foi contornada"
fi

# ── C. lado da CONFIG VIVA ────────────────────────────────────────────────────
echo
echo "── C. catálogo VIVO no config-api ───────────────────────────"
LIVE="$(curl -s --max-time 10 "${CFG}/config/masking?tenant_id=${TENANT}")"
if [ -z "$LIVE" ]; then
  huh "config-api em ${CFG} não respondeu"
else
  HAS="$(echo "$LIVE" | jq -r 'if (.entries | has("types")) then "sim" else "nao" end' 2>/dev/null)"
  if [ "$HAS" != "sim" ]; then
    huh "masking.types AUSENTE na config viva — seed ainda não rodou (\$DC up -d config-seed)"
  else
    LIVE_IDS="$(echo "$LIVE" | jq -r '[.entries.types.types[].id] | sort | join(",")')"
    CODE_IDS="$(echo "$CODE" | jq -r '.ids | sort | join(",")')"
    if [ "$LIVE_IDS" = "$CODE_IDS" ]; then
      ok "config viva == código: ${LIVE_IDS}"
    else
      bad "DIVERGEM — viva=[${LIVE_IDS}] código=[${CODE_IDS}] (D7: a viva vence; o seed é que precisa ser reconciliado)"
    fi
  fi
fi

# ── D. os fantasmas ───────────────────────────────────────────────────────────
echo
echo "── D. fantasmas (iban / passport) ───────────────────────────"
G_CODE="$(echo "$CODE" | jq -r '[.ids[] | select(. == "iban" or . == "passport")] | length')"
if [ "$G_CODE" = "0" ]; then
  ok "ausentes do catálogo do código"
else
  bad "PRESENTES no catálogo do código — a V2 os removeu por não serem alcançáveis"
fi
if [ -n "$LIVE" ]; then
  G_LIVE="$(echo "$LIVE" | jq -r '[.entries.types.types[]?.id | select(. == "iban" or . == "passport")] | length' 2>/dev/null)"
  [ -z "$G_LIVE" ] && G_LIVE=0
  if [ "$G_LIVE" = "0" ]; then
    ok "ausentes da config viva"
  else
    bad "PRESENTES na config viva (${G_LIVE})"
  fi
  N_LEGACY="$(echo "$LIVE" | jq -r '[.entries | keys[] | select(startswith("rule."))] | length')"
  say "chaves legadas rule.* = ${N_LEGACY}   (informação, não falha — a remoção é MEDIDA por este número zerar)"
fi

# ── E. TESTEMUNHA DO ORÁCULO ─────────────────────────────────────────────────
# "0 órfãos" só vale se o verificador SOUBER acusar um. Sem este ramo o gate
# aprovaria um `verifyDataTypeCatalog` que devolvesse listas vazias sempre.
echo
echo "── E. testemunha — o oráculo consegue REPROVAR? ─────────────"
WIT="$($DC exec -T "$SVC" sh -c "cd $NODE_CWD && node -e \"const s=require('@plughub/schemas'); const v=s.verifyDataTypeCatalog({types:[{id:'iban',formato:{},mascara:{},lgpd:'none'}]}); console.log(JSON.stringify({o:v.orphan_types.length,m:v.categories_without_type.length}))\"" 2>&1 | tr -d '\r')"
W_O="$(echo "$WIT" | jq -r 'if has("o") then (.o|tostring) else "AUSENTE" end' 2>/dev/null)"
W_M="$(echo "$WIT" | jq -r 'if has("m") then (.m|tostring) else "AUSENTE" end' 2>/dev/null)"
if [ "$W_O" = "1" ] && [ "$W_M" != "0" ] && [ "$W_M" != "AUSENTE" ]; then
  ok "catálogo sabidamente órfão reprova nos DOIS lados (órfãos=${W_O}, faltantes=${W_M})"
else
  bad "o oráculo NÃO reprova um catálogo órfão (órfãos=${W_O}, faltantes=${W_M}) — o verde acima não vale nada"
fi

# ── veredicto ─────────────────────────────────────────────────────────────────
echo
echo "═════════════════════════════════════════════════════════════"
if [ "$fail" -gt 0 ]; then
  echo "VEREDICTO: FALHA — ${fail} verificação(ões) vermelha(s)"
  exit 1
fi
if [ "$inconclusive" -gt 0 ]; then
  echo "VEREDICTO: INCONCLUSIVO — ${inconclusive} ramo(s) não julgado(s). NÃO é OK."
  exit 2
fi
echo "VEREDICTO: OK"
exit 0
