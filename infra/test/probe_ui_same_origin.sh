#!/usr/bin/env bash
#
# probe_ui_same_origin.sh — AUT-20 (2026-09-29)
#
# A PERGUNTA
# ==========
# O platform-ui fala SÓ com a própria origem (a borda da 5174, ou o proxy do Vite em dev)?
# E a borda entrega ao channel-gateway só o que a UI usa dele?
#
# POR QUE EXISTE
# ==============
# Medido em 2026-09-29: `api/registry.ts` montava as 23 chamadas sobre
# `VITE_REGISTRY_URL || http://localhost:3300`, e a env não era definida em lugar nenhum —
# todas furavam a borda e iam a OUTRA origem, que só existe no host que publica a 3300.
# O force-complete do Monitor fazia o mesmo com a 3100. Nada ficava vermelho: no host de
# desenvolvimento as duas portas respondem.
#
# E a mesma linha escondia coisa pior: a borda mandava `^/v1/channels` INTEIRO ao gateway,
# e ali mora o `POST /v1/channels/webhook/pool/{pool_id}`, anônimo por construção. Pela
# 5174, sem credencial, ele respondeu 201 e criou sessão — até para pool inexistente, que
# ficou numa fila sem TTL. A regra agora é o que a UI chama: `webhook/(identity|resume)`.
#
# RAMOS
#   A  estático — nenhuma base de serviço de outra origem em `src/` (literal localhost:porta
#      ou `import.meta.env.VITE_*_URL`), com mutação provando que o detector vê.
#   B  estático — nginx (Dockerfile) e Vite roteiam ao gateway, sob /v1/channels, SÓ
#      `webhook/(identity|resume)`.
#   C  vivo, pela borda — `/v1/channels` chega ao REGISTRY; o gatilho de pool NÃO chega ao
#      gateway (GET, sem efeito colateral: o gateway responde 405 a GET nessa rota, e essa é
#      a testemunha medida DIRETO nele); e `webhook/identity` ainda chega (controle).
#
# Veredicto: 0 = OK · 1 = REPROVOU · 3 = INCONCLUSIVO
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 3

UI=packages/platform-ui
BORDA="${BORDA:-http://localhost:5174}"
GW="${GW:-http://localhost:8010}"
TENANT="${TENANT:-tenant_demo}"

FAIL=0; INCONCL=0
ok()  { echo "  ✓ $*"; }
bad() { echo "  ❌ $*"; FAIL=$((FAIL+1)); }
inc() { echo "  ⏭️  $* (INCONCLUSIVO)"; INCONCL=$((INCONCL+1)); }

# Linhas de CÓDIGO (não comentário) com base de outra origem.
outra_origem() {
  grep -rnE "(['\"\`]https?://localhost:[0-9]+|import\.meta\.env(\.|\[['\"])VITE_[A-Z_]*URL)" "$1" \
    --include=*.ts --include=*.tsx 2>/dev/null \
  | grep -vE '/__tests__/|\.test\.tsx?:' \
  | grep -vE '^[^:]+:[0-9]+:[[:space:]]*(//|\*|/\*)'
}

echo "== probe_ui_same_origin =="
echo
echo "── A · nenhuma base de outra origem em $UI/src ──"
ACHOU=$(outra_origem "$UI/src")
if [ -z "$ACHOU" ]; then ok "zero chamadas a localhost:porta ou VITE_*_URL"
else echo "$ACHOU" | sed 's/^/     /'; bad "base de outra origem no código da UI"; fi

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/src"
printf "const b = import.meta.env.VITE_REGISTRY_URL || 'http://localhost:3300'\n" > "$TMP/src/x.ts"
printf "// comentario com 'http://localhost:3100' nao conta\n" > "$TMP/src/y.ts"
M=$(outra_origem "$TMP/src" | wc -l)
[ "$M" = "1" ] && ok "mutação: a linha injetada é vista, o comentário não (1 achado)" \
               || bad "mutação: esperado 1 achado, veio $M — o detector não mede o que diz"

echo
echo "── B · a borda entrega ao gateway, sob /v1/channels, só o que a UI chama ──"
for f in "$UI/Dockerfile" "$UI/vite.config.ts"; do
  if grep -qE "\^/v1/channels(\(/\|\\\$\)|')" "$f"; then
    bad "$(basename "$f"): regra LARGA ^/v1/channels — publica o gatilho anônimo de pool"
  elif grep -qF "^/v1/channels/webhook/(identity|resume)(/|$)" "$f"; then
    ok "$(basename "$f"): só webhook/(identity|resume)"
  else
    bad "$(basename "$f"): a regra do gateway sob /v1/channels sumiu — a UI perde identity/resume"
  fi
done

echo
echo "── C · pela borda, ao vivo ──"
W=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$GW/v1/channels/webhook/pool/probe_aut20" 2>/dev/null || true)
if [ "$W" != "405" ]; then
  inc "testemunha: GET direto no gateway devolveu '$W', não 405 — sem ela não dá para dizer quem responde pela borda"
else
  ok "testemunha: o gateway responde 405 a GET em webhook/pool (direto, 8010)"
  c=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$BORDA/v1/channels/webhook/pool/probe_aut20" 2>/dev/null || true)
  if [ -z "$c" ] || [ "$c" = "000" ]; then inc "borda inalcançável em $BORDA"
  elif [ "$c" = "405" ]; then bad "pela borda, webhook/pool ainda chega ao GATEWAY (405) — o gatilho anônimo está publicado"
  else ok "pela borda, webhook/pool NÃO chega ao gateway ($c)"; fi
fi
b=$(curl -s -m 5 -H "x-tenant-id: $TENANT" -w '\n%{http_code}' "$BORDA/v1/channels" 2>/dev/null || true)
bc=$(printf '%s' "$b" | tail -1); bb=$(printf '%s' "$b" | head -n -1)
if [ "$bc" = "200" ] && ! printf '%s' "$bb" | grep -q '"detail"'; then ok "/v1/channels pela borda → registry (200)"
else bad "/v1/channels pela borda → $bc ${bb:0:80} (esperado 200 do registry)"; fi
i=$(curl -s -m 5 -w '\n%{http_code}' "$BORDA/v1/channels/webhook/identity/customers/search?tenant_id=$TENANT&q=x" 2>/dev/null || true)
ic=$(printf '%s' "$i" | tail -1); ib=$(printf '%s' "$i" | head -n -1)
if printf '%s' "$ib" | grep -q '"detail"'; then ok "controle: webhook/identity pela borda ainda chega ao gateway ($ic, resposta FastAPI)"
else bad "controle: webhook/identity pela borda → $ic ${ib:0:80} — a UI perdeu o Cliente 360"; fi

echo
echo "  FAIL=$FAIL  INCONCLUSIVO=$INCONCL"
[ "$FAIL" -gt 0 ] && { echo "❌ REPROVOU"; exit 1; }
[ "$INCONCL" -gt 0 ] && { echo "⏭️  INCONCLUSIVO — não é verde"; exit 3; }
echo "✅ a UI fala só com a própria origem, e a borda entrega ao gateway só o que ela usa"
exit 0
